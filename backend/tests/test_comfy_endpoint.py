"""切换设置后，排队/恢复/升级任务仍使用各自保存的 ComfyUI 服务。"""
import asyncio
import json
from pathlib import Path

import pytest

from app import comfyui, queue_manager as qm
from app.engines.comfy import ComfyEngine
from app.generation import prepare_comfy_workflow, persist_prepared_job
from app.generation_assets import restore_conditions, upload_condition


SERVER_A = 'http://server-a.invalid:8188'
SERVER_B = 'http://server-b.invalid:8188'


@pytest.fixture
def environment(tmp_path, monkeypatch):
    current = {'url': SERVER_A}
    events = []
    monkeypatch.setattr(comfyui, '_default_base_url', lambda: current['url'])
    monkeypatch.setattr(comfyui.ComfyUIClient, 'is_available',
                        lambda self: events.append(('preflight', self.base_url)) or True)
    monkeypatch.setattr(comfyui.ComfyUIClient, 'submit',
                        lambda self, graph, client_id: events.append(('submit', self.base_url)) or 'pid')
    monkeypatch.setattr(comfyui.ComfyUIClient, 'download',
                        lambda self, *args: events.append(('download', self.base_url)) or b'image')
    monkeypatch.setattr(comfyui, 'upload_reference_image',
                        lambda client, url: events.append(('upload', client.base_url)) or 'condition.png')
    monkeypatch.setattr(ComfyEngine, '_wait',
                        lambda *args, **kwargs: {'outputs': {'1': {'images': [{'filename': 'frame.png'}]}}})
    monkeypatch.setattr(comfyui, 'load_workflow_template', lambda: {
        'prompt': {'1': {'class_type': 'LoadImage', 'inputs': {}}},
        '_aivideo_map': {'reference_image': {'node': '1', 'input': 'image'}},
    })
    monkeypatch.setattr(qm, 'OUTPUTS_DIR', tmp_path)
    monkeypatch.setattr(qm, 'frames_to_video',
                        lambda frames, dest, fps, **kw: Path(dest).write_bytes(b'video'))
    queue = qm.JobQueue()
    monkeypatch.setattr(queue, '_job_metadata', lambda job, engine: {'hardware': 'test'})
    yield queue, current, events
    queue.stop()


def prepare_job(database, output_dir):
    params = {'engine': 'comfyui', 'prompt': 'Walking', 'width': 512, 'height': 512,
              'num_frames': 16, 'fps': 8, 'steps': 25, 'guidance': 5, 'seed': 42,
              'mode': 'i2v', 'reference_image': '/outputs/source.png'}
    graph, template = prepare_comfy_workflow(params, output_dir=output_dir)
    return persist_prepared_job(database, params, output_dir, graph=graph, template=template)


def test_queued_jobs_upload_submit_and_download_on_frozen_server(fresh_db, tmp_path, environment):
    queue, current, events = environment
    (tmp_path / 'source.png').write_bytes(b'image')
    first = prepare_job(fresh_db, tmp_path)
    current['url'] = SERVER_B
    second = prepare_job(fresh_db, tmp_path)
    assert first['comfy_url'] == SERVER_A and second['comfy_url'] == SERVER_B
    assert [e[1] for e in events if e[0] == 'upload'] == [SERVER_A, SERVER_B]
    assert json.loads(Path(first['workflow_snapshot']).read_text(encoding='utf-8'))['comfy_url'] == SERVER_A
    # 排队期间再改全局设置，已经准备好的任务也不能跟着迁移。
    current['url'] = 'http://server-c.invalid:8188'
    asyncio.run(queue._run_job(first['id']))
    asyncio.run(queue._run_job(second['id']))
    assert fresh_db.get_job(first['id'])['status'] == 'done'
    assert fresh_db.get_job(second['id'])['status'] == 'done'
    assert [e[1] for e in events if e[0] == 'submit'] == [SERVER_A, SERVER_B]
    assert [e[1] for e in events if e[0] == 'download'] == [SERVER_A, SERVER_B]
    assert ('comfyui', SERVER_A) in queue._engine_instances
    assert ('comfyui', SERVER_B) in queue._engine_instances


def test_recovery_probes_original_server(fresh_db, tmp_path, environment, monkeypatch):
    queue, current, events = environment
    job = prepare_job(fresh_db, tmp_path)
    fresh_db.update_job(job['id'], status='running', prompt_id='persisted-pid')
    current['url'] = SERVER_B
    monkeypatch.setattr(comfyui.ComfyUIClient, 'probe',
                        lambda self, pid: events.append(('probe', self.base_url)) or 'running')
    queue._recover_stale_jobs()
    assert ('probe', SERVER_A) in events
    assert ('probe', SERVER_B) not in events
    assert queue._queue.get_nowait() == job['id']


def test_restore_conditions_uploads_to_original_server(tmp_path, environment):
    _, current, events = environment
    (tmp_path / 'source.png').write_bytes(b'original image')
    params = {'comfy_url': SERVER_A}
    old = upload_condition(params, comfyui.ComfyUIClient(SERVER_A), '/outputs/source.png', 'first', tmp_path)
    current['url'] = SERVER_B
    graph = {'1': {'class_type': 'LoadImage', 'inputs': {'image': old}}}
    restore_conditions(params, graph, {}, tmp_path)
    assert [e[1] for e in events if e[0] == 'upload'] == [SERVER_A, SERVER_A]


def test_legacy_task_freezes_current_url_once(fresh_db, environment):
    queue, current, _ = environment
    job = fresh_db.create_job({'engine': 'comfyui', 'prompt': 'legacy', 'width': 512, 'height': 512,
                              'num_frames': 16, 'fps': 8, 'steps': 25, 'guidance': 5, 'seed': 42})
    assert queue._comfy_url(job) == SERVER_A
    current['url'] = SERVER_B
    assert queue._comfy_url(fresh_db.get_job(job['id'])) == SERVER_A
