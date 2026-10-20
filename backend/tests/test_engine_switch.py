"""切换本地引擎必须先释放旧管线；不需要 GPU 或真实模型。"""
import asyncio
from pathlib import Path

from app import queue_manager as qm


def test_old_model_released_before_new_engine_is_created(fresh_db, tmp_path, monkeypatch):
    events = []

    class OldEngine:
        def unload(self):
            events.append('unload old')

    class NewEngine:
        def __init__(self):
            assert events == ['unload old']
            events.append('load new')

        def generate(self, **kwargs):
            frame = kwargs['frames_dir'] / 'frame_0000.png'
            frame.write_bytes(b'frame')
            return [frame]

    queue = qm.JobQueue()
    queue._engine_instances['anime'] = OldEngine()
    monkeypatch.setattr(qm, 'OUTPUTS_DIR', tmp_path)
    monkeypatch.setattr(qm, 'get_engine', lambda engine: NewEngine)
    monkeypatch.setattr(qm, 'frames_to_video', lambda frames, dest, fps, **kwargs: Path(dest).write_bytes(b'video'))
    monkeypatch.setattr(queue, '_job_metadata', lambda job, engine: {'hardware': 'isolated test'})
    job = fresh_db.create_job({'engine': 'wan22', 'prompt': 'test', 'width': 512, 'height': 512,
                              'num_frames': 13, 'fps': 8, 'steps': 25, 'guidance': 5, 'seed': 42})
    try:
        asyncio.run(queue._run_job(job['id']))
        assert fresh_db.get_job(job['id'])['status'] == 'done'
        assert events == ['unload old', 'load new']
        assert 'anime' not in queue._engine_instances
    finally:
        queue._executor.shutdown(wait=True)
