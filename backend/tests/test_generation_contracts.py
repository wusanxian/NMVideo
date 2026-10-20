"""生成入口、图片条件、快照与升级的行为回归；不加载真实模型。"""
import json
from pathlib import Path

import pytest

from app import comfyui
from app.routers import jobs, storyboard
from app.engines.comfy import ComfyEngine
from app.generation import persist_prepared_job


@pytest.fixture
def shot(client):
    project = client.post('/api/projects', json={'name': 'contract'}).json()
    scene = client.post(f"/api/projects/{project['id']}/scenes", json={'title': 'scene'}).json()
    return client.post(f"/api/scenes/{scene['id']}/shots", json={'prompt': '中文角色在雨中行走'}).json()


@pytest.fixture
def isolated_outputs(monkeypatch, tmp_path):
    out = tmp_path / 'outputs'
    out.mkdir()
    monkeypatch.setattr(storyboard, 'OUTPUTS_DIR', out)
    monkeypatch.setattr(jobs, 'OUTPUTS_DIR', out)
    return out


@pytest.mark.parametrize('options', [
    {'preset': 'fine'}, {'preset': 'fine', 'preview': True},
    {'preset': 'standard', 'width': 640, 'steps': 18},
    {'preset': 'standard', 'width': 640, 'steps': 18, 'preview': True},
    {'width': 519, 'height': 519, 'num_frames': 20},
])
def test_two_entry_points_share_effective_parameters(client, shot, options):
    body = {'engine': 'wan22', 'seed': 123, **options}
    standalone = client.post('/api/jobs', json={'prompt': shot['prompt'], **body})
    linked = client.post(f"/api/shots/{shot['id']}/generate", json=body)
    assert standalone.status_code == linked.status_code == 201
    for key in ('width', 'height', 'num_frames', 'fps', 'steps', 'guidance', 'preset', 'preview', 'seed'):
        assert standalone.json()[key] == linked.json()[key], key
    if options.get('preview'):
        assert linked.json()['width'] == 416
        target = json.loads(linked.json()['generation_target'])
        assert target['width'] == options.get('width', 1280)
        if 'steps' in options:
            assert target['steps'] == options['steps']


@pytest.mark.parametrize('engine', ['anime', 'wan22'])
@pytest.mark.parametrize('mode', ['i2v', 'first_last', 'continuation'])
def test_unsupported_mode_rejected_without_job(client, fresh_db, shot, engine, mode):
    result = client.post(f"/api/shots/{shot['id']}/generate", json={'engine': engine, 'mode': mode})
    assert result.status_code == 400
    assert '不支持' in result.json()['detail']
    assert fresh_db.list_jobs() == []


@pytest.mark.parametrize('key', ['reference_image', 'first_frame_image'])
def test_independent_first_frame_consumed(client, shot, isolated_outputs, monkeypatch, key):
    template = {'prompt': {'1': {'class_type': 'LoadImage', 'inputs': {'image': 'placeholder'}}},
                '_aivideo_map': {key: {'node': '1', 'input': 'image'}}}
    monkeypatch.setattr(comfyui, 'load_workflow_template', lambda: template)
    monkeypatch.setattr(comfyui.ComfyUIClient, 'is_available', lambda self: True)
    uploaded = []
    monkeypatch.setattr(comfyui, 'upload_reference_image', lambda c, url: uploaded.append(url) or 'first_uploaded.png')
    client.patch(f"/api/shots/{shot['id']}", json={'first_frame_image': '/outputs/first.png'})
    result = client.post(f"/api/shots/{shot['id']}/generate", json={'engine': 'comfyui', 'mode': 'i2v'})
    assert result.status_code == 201, result.text
    payload = json.loads(Path(result.json()['workflow_snapshot']).read_text(encoding='utf-8'))
    assert payload['graph']['1']['inputs']['image'] == 'first_uploaded.png'
    assert uploaded == ['/outputs/first.png']


def test_snapshot_write_failure_creates_no_executable_job(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'replace', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        persist_prepared_job(fresh_db, {'engine': 'comfyui', 'prompt': 'test'}, tmp_path, graph={'1': {'inputs': {}}})
    assert fresh_db.list_jobs() == []
    assert list(tmp_path.rglob('*.tmp')) == []


def test_missing_explicit_snapshot_never_uses_current_template(tmp_path, monkeypatch):
    import app.engines.comfy as engine_module
    monkeypatch.setattr(engine_module, 'load_workflow_template', lambda: pytest.fail('must not load current template'))
    with pytest.raises(comfyui.ComfyUIError, match='快照缺失'):
        ComfyEngine()._resolve_workflow(str(tmp_path / 'missing.json'), {}, tmp_path / 'job' / 'frames')


def test_upgrade_keeps_shot_seed_and_character_snapshot(client, shot, fresh_db):
    preview = client.post(f"/api/shots/{shot['id']}/generate", json={
        'engine': 'wan22', 'preset': 'fine', 'seed': 93, 'preview': True,
    }).json()
    fresh_db.update_job(preview['id'], status='done', characters_snapshot='[{"name":"old"}]')
    import app.storyboard as sb
    sb.set_shot_status(shot['id'], 'review')
    client.patch(f"/api/shots/{shot['id']}", json={'prompt': 'later edit'})
    result = client.post(f"/api/jobs/{preview['id']}/upgrade", json={})
    assert result.status_code == 201, result.text
    upgraded = result.json()
    assert upgraded['parent_job_id'] == preview['id']
    assert upgraded['shot_id'] == shot['id']
    assert upgraded['seed'] == 93 and not upgraded['preview']
    assert upgraded['width'] == 1280 and upgraded['num_frames'] == 81
    assert upgraded['prompt'] == preview['prompt']
    assert upgraded['characters_snapshot'] == '[{"name":"old"}]'
    assert fresh_db.get_job(preview['id'])['status'] == 'done'


def test_comfy_upgrade_uses_frozen_graph_after_template_edit(client, isolated_outputs, monkeypatch, fresh_db):
    numeric = ('width', 'height', 'num_frames', 'fps', 'steps', 'guidance', 'seed')
    template = {'prompt': {'1': {'inputs': {'marker': 'original', 'image': 'original_reference.png'}}},
                '_aivideo_map': {key: {'node': '1', 'input': key} for key in numeric}}
    monkeypatch.setattr(comfyui, 'load_workflow_template', lambda: template)
    monkeypatch.setattr(comfyui.ComfyUIClient, 'is_available', lambda self: True)
    preview = client.post('/api/jobs', json={'engine': 'comfyui', 'prompt': 'test', 'preview': True, 'preset': 'fine'}).json()
    fresh_db.update_job(preview['id'], status='done')
    monkeypatch.setattr(comfyui, 'load_workflow_template', lambda: pytest.fail('current template must not be used'))
    response = client.post(f"/api/jobs/{preview['id']}/upgrade", json={})
    assert response.status_code == 201, response.text
    payload = json.loads(Path(response.json()['workflow_snapshot']).read_text(encoding='utf-8'))
    assert payload['graph']['1']['inputs']['marker'] == 'original'
    assert payload['graph']['1']['inputs']['image'] == 'original_reference.png'
    assert payload['graph']['1']['inputs']['width'] == 1280
