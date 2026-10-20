"""取消与恢复必须同步镜头状态，且不因分页期间状态变化漏任务。"""
import asyncio

import pytest

from app import queue_manager as qm, storyboard as sb


@pytest.fixture
def queue(monkeypatch, tmp_path):
    import app.config as config
    from app.routers import jobs, storyboard
    out = tmp_path / 'outputs'
    out.mkdir()
    for module in (config, qm, jobs, storyboard):
        monkeypatch.setattr(module, 'OUTPUTS_DIR', out)
    instance = qm.JobQueue()
    monkeypatch.setattr(jobs, 'job_queue', instance)
    monkeypatch.setattr(storyboard, 'job_queue', instance)
    yield instance
    instance.stop()


def make_job(database, **extra):
    return database.create_job({
        'engine': 'anime', 'prompt': 'Walking', 'width': 512, 'height': 512,
        'num_frames': 16, 'fps': 8, 'steps': 25, 'guidance': 5, 'seed': 42, **extra,
    })


def make_shot():
    project = sb.create_project('Lifecycle regression')
    scene = sb.create_scene(project['id'], 'Scene')
    return sb.create_shot(scene['id'], prompt='A person walking')


def test_cancel_pending_then_regenerate(client, fresh_db, queue):
    shot = make_shot()
    response = client.post(f"/api/shots/{shot['id']}/generate", json={'engine': 'anime'})
    assert response.status_code == 201, response.text
    job_id = response.json()['id']
    assert client.post(f'/api/jobs/{job_id}/cancel').status_code == 200
    assert fresh_db.get_job(job_id)['status'] == 'cancelled'
    after = sb.get_shot(shot['id'])
    assert (after['status'], after['job_id'], after['review_stage']) == ('draft', '', '')
    # 重复取消、队列里的旧 id 均不能复活任务或锁住新任务。
    queue.cancel(job_id)
    asyncio.run(queue._run_job(job_id))
    again = client.post(f"/api/shots/{shot['id']}/generate", json={'engine': 'anime'})
    assert again.status_code == 201, again.text
    assert sb.get_shot(shot['id'])['job_id'] == again.json()['id']


@pytest.mark.parametrize('status', ['pending', 'needs_attention', 'running'])
def test_cancel_old_job_does_not_clear_new_shot_binding(fresh_db, queue, status):
    shot = make_shot()
    old = make_job(fresh_db, shot_id=shot['id'], status=status)
    new = make_job(fresh_db, shot_id=shot['id'])
    sb.set_shot_status(shot['id'], 'queued', job_id=new['id'], review_stage='generation')
    assert fresh_db.cancel_job(old['id'])
    after = sb.get_shot(shot['id'])
    assert (after['job_id'], after['status'], after['review_stage']) == (new['id'], 'queued', 'generation')


def test_delete_pending_releases_shot_and_running_delete_waits(client, fresh_db, queue):
    shot = make_shot()
    job = make_job(fresh_db, shot_id=shot['id'])
    sb.set_shot_status(shot['id'], 'queued', job_id=job['id'])
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert sb.get_shot(shot['id'])['status'] == 'draft'
    running = make_job(fresh_db, status='running')
    assert client.delete(f"/api/jobs/{running['id']}").status_code == 409
    assert fresh_db.get_job(running['id']) is not None


@pytest.mark.parametrize('status', ['running', 'needs_attention'])
def test_recovery_processes_all_rows_while_statuses_change(fresh_db, queue, monkeypatch, status):
    count = queue._RECOVER_PAGE * 2 + 3
    with fresh_db.transaction() as conn:
        conn.executemany(
            'INSERT INTO jobs (id,status,engine,prompt,width,height,num_frames,fps,steps,guidance,seed,created_at) '
            'VALUES (?, ?, ?, ?, 512,512,16,8,25,5,42,?)',
            [(f'job_{index:04}', status, 'anime', 'test', '2026-10-20') for index in range(count)],
        )
    queue._recover_stale_jobs()
    assert fresh_db.list_jobs(limit=count + 1, status=status) == []
    assert len(fresh_db.list_jobs(limit=count + 1, status='failed')) == count


@pytest.mark.parametrize('order', ['asc', 'desc'])
def test_recovery_cursor_preserves_order_when_rows_leave_filter(fresh_db, queue, order):
    queue._RECOVER_PAGE = 2
    for index in range(7):
        fresh_db.create_job({
            'engine': 'anime', 'prompt': 'test', 'width': 512, 'height': 512,
            'num_frames': 16, 'fps': 8, 'steps': 25, 'guidance': 5, 'seed': 42,
            'created_at': '2026-10-20',
        }, job_id=f'ordered_{index}')
    seen = []
    for job in queue._iter_jobs('pending', order=order):
        seen.append(job['id'])
        fresh_db.update_job(job['id'], status='done')
    assert seen == sorted([f'ordered_{index}' for index in range(7)], reverse=order == 'desc')


def test_cancelled_job_cannot_be_claimed(fresh_db):
    job = make_job(fresh_db)
    assert fresh_db.cancel_job(job['id'])
    assert not fresh_db.claim_job(job['id'])
    assert fresh_db.get_job(job['id'])['status'] == 'cancelled'


def test_cancel_recovered_job_before_worker_starts(fresh_db, queue, monkeypatch):
    shot = make_shot()
    job = make_job(fresh_db, shot_id=shot['id'], status='running')
    sb.set_shot_status(shot['id'], 'generating', job_id=job['id'], review_stage='generation')
    monkeypatch.setattr(qm, 'get_engine', lambda engine: pytest.fail('cancelled job must not execute'))

    async def run():
        await queue.enqueue(job['id'])
        queue.cancel(job['id'])
        worker = asyncio.create_task(queue._worker())
        await asyncio.sleep(0)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

    asyncio.run(run())
    assert fresh_db.get_job(job['id'])['status'] == 'cancelled'
    assert sb.get_shot(shot['id'])['status'] == 'draft'
