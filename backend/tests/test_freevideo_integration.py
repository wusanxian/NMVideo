"""官方 FreeVideo 节点协议适配；运行模型的效果另做实机验收。"""
import json
from pathlib import Path

import pytest

from app import comfyui
from app.generation import prepare_comfy_workflow
from app.routers import jobs, storyboard


@pytest.fixture
def freevideo_env(monkeypatch, tmp_path):
    out = tmp_path / 'outputs'
    out.mkdir()
    monkeypatch.setattr(jobs, 'OUTPUTS_DIR', out)
    monkeypatch.setattr(storyboard, 'OUTPUTS_DIR', out)
    inputs = ('text', 'width', 'height', 'seconds', 'seed', 'base_steps', 'two_pass',
              'frames', 'preview', 'refine_from')
    info = {name: {'input': {'required': {}}} for name in
            ('FreeVideoGenerate', 'LoadImage', 'FreeVideoReference', 'FreeVideoLoRA')}
    info['FreeVideoGenerate']['input']['required'] = {key: ('STRING',) for key in inputs}
    info['FreeVideoLoRA']['input']['required'] = {'lora': [['hero.safetensors']]}
    monkeypatch.setattr(comfyui.ComfyUIClient, 'is_available', lambda self: True)
    monkeypatch.setattr(comfyui.ComfyUIClient, 'object_info', lambda self, node_names=None: info)
    monkeypatch.setattr(comfyui, 'upload_reference_image', lambda c, url: Path(url).name)
    template = json.loads((comfyui.PRESETS_DIR / 'freevideo_h3_t2v.json').read_text(encoding='utf-8'))
    monkeypatch.setattr(comfyui, 'load_workflow_template', lambda: template)
    return out, template


def test_native_preview_uses_target_geometry_and_native_steps(client, freevideo_env):
    response = client.post('/api/jobs', json={
        'engine': 'comfyui', 'prompt': 'test', 'preview': True, 'preset': 'fine',
    })
    assert response.status_code == 201, response.text
    job = response.json()
    graph = json.loads(Path(job['workflow_snapshot']).read_text(encoding='utf-8'))['graph']
    assert job['preview_kind'] == 'native'
    assert graph['1']['inputs']['preview'] is True
    assert graph['1']['inputs']['width'] == 1280
    assert graph['1']['inputs']['height'] == 736
    assert graph['1']['inputs']['base_steps'] == 8
    assert graph['1']['inputs']['frames'] == 90
    assert graph['1']['inputs']['seconds'] == 90 / 24


def test_native_upgrade_continues_original_remote_preview(client, fresh_db, freevideo_env, monkeypatch):
    monkeypatch.setattr(comfyui, '_default_base_url', lambda: 'http://original.invalid:8188')
    preview = client.post('/api/jobs', json={
        'engine': 'comfyui', 'prompt': 'original prompt', 'preview': True,
        'preset': 'fine', 'seed': 97,
    }).json()
    fresh_db.update_job(preview['id'], status='done', native_output=json.dumps({
        'filename': 'original.preview.mp4', 'subfolder': 'freevideo', 'type': 'output',
    }))
    monkeypatch.setattr(comfyui, 'load_workflow_template', lambda: pytest.fail('no current template'))
    monkeypatch.setattr(comfyui, '_default_base_url', lambda: 'http://changed.invalid:8188')
    response = client.post(f"/api/jobs/{preview['id']}/upgrade", json={})
    assert response.status_code == 201, response.text
    assert response.json()['comfy_url'] == 'http://original.invalid:8188'
    graph = json.loads(Path(response.json()['workflow_snapshot']).read_text(encoding='utf-8'))['graph']
    assert graph['1']['inputs']['preview'] is False
    assert graph['1']['inputs']['refine_from'] == 'freevideo/original.preview.mp4'
    assert graph['1']['inputs']['text'] == 'original prompt'
    assert graph['1']['inputs']['seed'] == 97


def test_character_references_and_lora_are_connected(freevideo_env):
    out, template = freevideo_env
    params = {'engine': 'comfyui', 'prompt': 'hero', 'width': 512, 'height': 512,
              'num_frames': 81, 'fps': 24, 'steps': 25, 'guidance': 5, 'seed': 42,
              'characters_snapshot': json.dumps([{
                  'name': 'hero', 'reference_images': [{'url': '/outputs/front.png'}, '/outputs/side.png'],
                  'lora': 'hero.safetensors',
              }])}
    graph, _ = prepare_comfy_workflow(params, template=template, output_dir=out)
    generator = graph['1']['inputs']
    last_ref = graph[generator['references'][0]]['inputs']
    assert graph[last_ref['image'][0]]['inputs']['image'] == 'side.png'
    first_ref = graph[last_ref['previous'][0]]['inputs']
    assert graph[first_ref['image'][0]]['inputs']['image'] == 'front.png'
    assert graph[generator['loras'][0]]['inputs'] == {'lora': 'hero.safetensors', 'strength': 1.0}
    assert len(json.loads(params['generation_inputs'])) == 2


def test_image_manifest_keeps_content_after_original_changes(freevideo_env):
    from app.generation_assets import upload_condition
    out, _ = freevideo_env
    original = out / 'original.png'
    original.write_bytes(b'original image')
    params = {}
    upload_condition(params, comfyui.ComfyUIClient(), '/outputs/original.png', 'first', out)
    original.write_bytes(b'later replacement')
    record = json.loads(params['generation_inputs'])[0]
    frozen = out / record['frozen_url'].removeprefix('/outputs/')
    assert frozen.read_bytes() == b'original image'
    assert record['sha256'] in frozen.name


def test_incompatible_node_schema_is_rejected(client, fresh_db, freevideo_env, monkeypatch):
    monkeypatch.setattr(comfyui.ComfyUIClient, 'object_info', lambda self, node_names=None: {'FreeVideoGenerate': {}})
    response = client.post('/api/jobs', json={'engine': 'comfyui', 'prompt': 'test'})
    assert response.status_code == 400
    assert '接口不兼容' in response.json()['detail']
    assert fresh_db.list_jobs() == []


@pytest.mark.parametrize('tamper', [False, True])
def test_upgrade_reuploads_verified_frozen_image(freevideo_env, monkeypatch, tamper):
    from app.generation_assets import upload_condition, restore_conditions
    out, _ = freevideo_env
    (out / 'source.png').write_bytes(b'original image')
    params = {}
    old_name = upload_condition(params, comfyui.ComfyUIClient(), '/outputs/source.png', 'first_frame_image', out)
    record = json.loads(params['generation_inputs'])[0]
    (out / 'source.png').write_bytes(b'later replacement')
    graph = {'2': {'class_type': 'LoadImage', 'inputs': {'image': old_name}}}
    template = {'_aivideo_map': {'first_frame_image': {'node': '2', 'input': 'image'}}}
    monkeypatch.setattr(comfyui, 'upload_reference_image', lambda c, url: 'restored.png')
    if tamper:
        (out / record['frozen_url'].removeprefix('/outputs/')).write_bytes(b'tampered')
        with pytest.raises(ValueError, match='冻结图片'):
            restore_conditions(params, graph, template, out)
    else:
        restore_conditions(params, graph, template, out)
        assert graph['2']['inputs']['image'] == 'restored.png'


def test_workflow_editor_keeps_capabilities_and_mapping(client, tmp_path, monkeypatch):
    monkeypatch.setattr(comfyui, 'DEFAULT_WORKFLOW', tmp_path / 'workflow.json')
    monkeypatch.setattr(comfyui, 'WORKFLOWS_DIR', tmp_path)
    template = {'prompt': {'1': {'class_type': 'FreeVideoGenerate', 'inputs': {}}},
                '_aivideo_map': {'prompt': {'node': '1', 'input': 'text'}},
                '_meta': {'model_id': 'vdn-minimax-h3', 'execution_kind': 'local'}}
    response = client.put('/api/comfy/workflow', json=template)
    assert response.status_code == 200, response.text
    saved = client.get('/api/comfy/workflow').json()
    assert saved['_meta'] == template['_meta']
    assert saved['_aivideo_map'] == template['_aivideo_map']


def test_official_v3_preview_video_is_archived_as_native_video():
    history = {'outputs': {'1': {'images': [{
        'filename': 'original.preview.mp4', 'subfolder': 'freevideo', 'type': 'output',
    }], 'animated': [True]}}}
    files = comfyui.extract_output_files(history)
    assert files == [{'filename': 'original.preview.mp4', 'subfolder': 'freevideo',
                      'type': 'output', 'kind': 'videos'}]


@pytest.mark.parametrize('use_images', [True, False])
def test_keyframe_and_reference_layout_conflict_is_explicit(freevideo_env, use_images):
    from app.freevideo import attach_characters
    graph = {'1': {'class_type': 'FreeVideoGenerate', 'inputs': {'first': ['2', 0]}},
             '2': {'class_type': 'LoadImage', 'inputs': {'image': 'first.png'}}}
    params = {'use_character_images': use_images,
              'characters_snapshot': json.dumps([{'reference_images': ['/outputs/hero.png']}])}
    if use_images:
        with pytest.raises(ValueError, match='不支持首尾帧与角色参考图混用'):
            attach_characters(graph, params, comfyui.ComfyUIClient())
    else:
        result = attach_characters(graph, params, comfyui.ComfyUIClient())
        assert result['1']['inputs'] == {'first': ['2', 0]}
