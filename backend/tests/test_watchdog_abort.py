"""看门狗端到端：真的驱动一次 _run_job，验证「内存跌破 → 任务失败 + 说明」，
以及两条不该误伤的路径（内存充足不中止、用户取消仍算取消）。

不依赖 pytest：标准库 unittest + 临时 SQLite + 假引擎，跑在本机也不出片。
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _stubs  # noqa: E402

_stubs.install()

from app import db as db_module  # noqa: E402
from app import hw_probe, queue_manager, resource_guard  # noqa: E402
from app.engines.base import GenerationCancelled  # noqa: E402
from app.queue_manager import JobQueue  # noqa: E402

JOB = {
    "engine": "anime",
    "prompt": "看门狗端到端测试",
    "negative_prompt": "",
    "width": 416,
    "height": 256,
    "num_frames": 17,
    "fps": 24,
    "steps": 4,
    "guidance": 5.0,
    "seed": 42,
    "mode": "t2v",
    "preview": 0,
    "preset": "",
    "shot_id": "",
}


class EngineDone(Exception):
    """假引擎正常跑完（真实引擎会返回帧列表；这里没有 ffmpeg，用它收尾）。"""


class SlowEngine:
    """假引擎：一直转，直到 is_cancelled() 翻真——复现「跑到一半」。

    步数刻意压小：每次 on_progress 都是一次 SQLite 提交，本机 fsync 很慢
    （200 次回调能耗掉一分钟），测试要的是「被打断/没被打断」而不是压测。
    """

    meta = type("M", (), {"id": "anime", "execution_channel": "native"})()
    STEPS = 20

    def __init__(self):
        self.saw_cancel = False
        self.steps = 0

    def generate(self, *, frames_dir, is_cancelled, on_progress, **kwargs):
        import time

        for i in range(self.STEPS):
            self.steps += 1
            if is_cancelled():
                self.saw_cancel = True
                raise GenerationCancelled()
            if i % 5 == 0:
                on_progress(i / self.STEPS, "采样")
            time.sleep(0.01)     # 模拟一步采样的耗时
        raise EngineDone("假引擎跑完，未被打断")


class WatchdogRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 独立临时库 + 独立输出目录，绝不碰开发库 backend/aivideo.db。
        # 建库要十几秒（大量建表 + 落盘），整个类共用一份，别每个用例重建。
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls.tmp.name)
        cls.db = db_module.Database(cls.root / "test.db")
        cls._saved = []
        cls._patch(db_module, "db", cls.db)
        cls._patch(queue_manager, "OUTPUTS_DIR", cls.root / "outputs")
        # 入队守卫的判定与夹具无关：放行，专注看门狗本身
        cls._patch(
            queue_manager.resource_guard, "plan",
            staticmethod(lambda *a, **k: resource_guard.Decision(ok=True, params={})),
        )

    @classmethod
    def _patch(cls, obj, name, value):
        cls._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    @classmethod
    def tearDownClass(cls):
        for obj, name, old in reversed(cls._saved):
            setattr(obj, name, old)
        cls.db.close()
        cls.tmp.cleanup()

    def setUp(self):
        self._env = {}
        self._own = []
        self.engine = SlowEngine()
        self._patch_self(queue_manager, "get_engine", lambda engine_id: (lambda: self.engine))
        self.env("AIVIDEO_RESOURCE_GUARD", "enforce")
        self.env("AIVIDEO_RESOURCE_WATCHDOG", "on")
        self.env("AIVIDEO_WATCHDOG_S", "0.02")
        self.env("AIVIDEO_WATCHDOG_SAMPLES", "2")
        self.env("AIVIDEO_WATCHDOG_GRACE_S", "0")

    def tearDown(self):
        for obj, name, old in reversed(self._own):
            setattr(obj, name, old)
        for key, old in self._env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    def _patch_self(self, obj, name, value):
        self._own.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def env(self, key, value):
        self._env.setdefault(key, os.environ.get(key))
        os.environ[key] = value

    def ram(self, value):
        self._patch_self(hw_probe, "free_ram_gb", lambda: value)

    def new_job(self) -> str:
        return self.db.create_job(dict(JOB))["id"]

    # ---------- 用例 ----------
    def test_abort_marks_failed_with_reason(self):
        self.ram(0.4)                     # 跌破 1GB 硬底
        job_id = self.new_job()
        asyncio.run(JobQueue()._run_job(job_id))

        job = self.db.get_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("可用内存", job["error"])
        self.assertIn("硬底", job["error"])
        self.assertIn("重新生成", job["error"])
        self.assertIn("硬底", job["resource_note"] or "")
        self.assertTrue(self.engine.saw_cancel, "引擎应收到取消信号并退出")

    def test_healthy_machine_is_not_interrupted(self):
        self.ram(12.0)                    # 内存充足：看门狗只该看着
        job_id = self.new_job()
        asyncio.run(JobQueue()._run_job(job_id))
        # 假引擎跑满全部步数才走到 EngineDone —— 说明看门狗没动手
        self.assertEqual(self.engine.steps, SlowEngine.STEPS)
        self.assertFalse(self.engine.saw_cancel)
        err = self.db.get_job(job_id)["error"] or ""
        self.assertIn("未被打断", err)
        self.assertNotIn("硬底", err)

    def test_user_cancel_still_reads_as_cancelled(self):
        self.ram(12.0)
        job_id = self.new_job()
        queue = JobQueue()

        async def scenario():
            task = asyncio.ensure_future(queue._run_job(job_id))
            await asyncio.sleep(0.05)
            queue.cancel(job_id)          # 用户点取消
            await task

        asyncio.run(scenario())
        job = self.db.get_job(job_id)
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["stage"], "已取消")
        self.assertNotIn("硬底", job["error"] or "")

    def test_probe_failure_does_not_abort(self):
        self.ram(-1.0)                    # 探测失败 = 没数据，不杀任务
        job_id = self.new_job()
        asyncio.run(JobQueue()._run_job(job_id))
        self.assertFalse(self.engine.saw_cancel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
