"""看门狗单测：什么时候中止、什么时候必须忍住不中止。

用标准库 unittest（同 test_resource_guard：本机 pip 走 HTTPS 常报 SSL 错，
测试越少外部依赖越能真跑）。
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import resource_watch
from app.resource_watch import Watcher


class FakeClock:
    """可控时钟：validate grace / 持续时长的判定，不真的 sleep。"""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds: float):
        self.t += seconds


class WatcherTest(unittest.TestCase):
    def setUp(self):
        self._env = {}
        self.clock = FakeClock()

    def tearDown(self):
        for key, old in self._env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    def env(self, key, value):
        self._env.setdefault(key, os.environ.get(key))
        os.environ[key] = value

    def watcher(self, **kwargs):
        kwargs.setdefault("clock", self.clock)
        kwargs.setdefault("interval_s", 5.0)
        kwargs.setdefault("samples", 3)
        kwargs.setdefault("grace_s", 15.0)
        return Watcher(**kwargs)

    def feed(self, w, ram, times=1):
        """喂 times 次采样，每次正常推进一个采样间隔。返回最后一次结论。"""
        v = None
        for _ in range(times):
            self.clock.advance(w.interval_s)
            v = w.observe(ram)
        return v

    # ---------- 会中止 ----------
    def test_aborts_after_sustained_breach(self):
        w = self.watcher(need_ram_gb=18.0)
        self.assertEqual(self.feed(w, 4.0, times=3).level, "ok")   # 还在 grace 期
        v = self.feed(w, 0.5, times=3)                             # 越过 grace + 连击
        self.assertEqual(v.level, "abort")
        self.assertIn("0.5GB", v.reason)
        self.assertIn("硬底", v.reason)
        self.assertIn("18.0GB", v.reason)                          # 需求写进说明
        self.assertIn("重新生成", v.reason)

    def test_streak_accumulates_during_grace(self):
        # grace 期里就已经跌破：grace 一结束就该中止，不再从头数三下
        w = self.watcher()
        self.feed(w, 0.4, times=2)                                  # t=10s，仍在 grace
        self.assertEqual(w.streak, 2)
        self.assertNotEqual(self.feed(w, 0.4).level, "ok")           # t=15s grace 结束即中止

    # ---------- 必须忍住 ----------
    def test_single_dip_does_not_abort(self):
        w = self.watcher()
        self.feed(w, 8.0, times=3)
        self.assertEqual(self.feed(w, 0.3).level, "ok")             # 只抖了一下
        self.assertEqual(self.feed(w, 8.0).level, "ok")
        self.assertEqual(w.streak, 0)

    def test_recovery_resets_streak(self):
        w = self.watcher()
        self.feed(w, 8.0, times=3)
        self.feed(w, 0.3, times=2)                                  # 差一次
        self.feed(w, 6.0)                                           # 恢复
        self.assertEqual(w.streak, 0)
        self.assertEqual(self.feed(w, 0.3, times=2).level, "ok")

    def test_probe_failure_never_aborts(self):
        w = self.watcher()
        self.feed(w, 8.0, times=3)
        for _ in range(10):
            self.assertEqual(self.feed(w, -1.0).level, "ok")
        self.assertEqual(w.streak, 0)

    def test_probe_failure_keeps_existing_streak(self):
        # 连续 2 次真跌破后探测失效：不清零也不累加，恢复数据后继续累计
        w = self.watcher(samples=4)
        self.feed(w, 8.0, times=3)
        self.feed(w, 0.3, times=2)
        self.feed(w, -1.0, times=2)
        self.assertEqual(w.streak, 2)
        self.assertEqual(self.feed(w, 0.3, times=2).level, "abort")

    # ---------- 紧张（提示但不中止） ----------
    def test_tight_warns_once_without_aborting(self):
        w = self.watcher()          # floor 1.0，tight 线 2.5
        self.feed(w, 8.0, times=3)
        first = self.feed(w, 2.0)
        self.assertEqual(first.level, "tight")
        self.assertIn("2.0GB", first.reason)
        self.assertIn("继续生成", first.reason)
        self.assertEqual(self.feed(w, 2.0).level, "ok")             # 只提示一次
        self.assertEqual(w.streak, 0)                               # 不参与中止连击

    def test_tight_does_not_leak_into_abort(self):
        w = self.watcher()
        self.feed(w, 8.0, times=3)
        for _ in range(6):
            self.assertNotEqual(self.feed(w, 2.0).level, "abort")

    # ---------- 开关 ----------
    def test_enabled_respects_switches(self):
        self.env("AIVIDEO_RESOURCE_GUARD", "enforce")
        self.env("AIVIDEO_RESOURCE_WATCHDOG", "on")
        self.assertTrue(resource_watch.watchdog_enabled())

        self.env("AIVIDEO_RESOURCE_WATCHDOG", "off")
        self.assertFalse(resource_watch.watchdog_enabled())

        self.env("AIVIDEO_RESOURCE_WATCHDOG", "on")
        self.env("AIVIDEO_RESOURCE_GUARD", "warn")
        self.assertFalse(resource_watch.watchdog_enabled())   # 只提示不打断

        self.env("AIVIDEO_RESOURCE_GUARD", "off")
        self.assertFalse(resource_watch.watchdog_enabled())

    def test_defaults_come_from_env(self):
        self.env("AIVIDEO_WATCHDOG_S", "2")
        self.env("AIVIDEO_WATCHDOG_SAMPLES", "5")
        self.env("AIVIDEO_WATCHDOG_GRACE_S", "0")
        w = Watcher(clock=self.clock)
        self.assertEqual(w.interval_s, 2.0)
        self.assertEqual(w.samples, 5)
        self.assertEqual(w.grace_s, 0.0)
        self.assertEqual(self.feed(w, 0.2, times=5).level, "abort")


if __name__ == "__main__":
    unittest.main(verbosity=2)
