"""ComfyUI 可达性缓存：一次探测，全进程共享。

真实踩过：缓存挂在引擎实例上，而 `list_engines()` 每次 `c()` 新建实例，
于是 /api/engines 每次都要真探一遍。ComfyUI 没起来时实测 4.2 秒——生成页
一打开就转圈，用户以为软件卡死了。

这里钉三件事：
  1. 同一地址的第二次调用不再发 HTTP
  2. 不同地址各有各的缓存（切服务器不会串味）
  3. 过期后重新探测；forget_available() 能立刻作废
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _stubs  # noqa: E402

_stubs.install()

from app.engines import comfy  # noqa: E402


class FakeClient:
    """替身：记账调用次数，按构造时传入的地址作答。"""

    calls = []
    answer = {}          # base_url -> bool
    default = False

    def __init__(self, base_url=None):
        self.base_url = (base_url or "http://127.0.0.1:8188").rstrip("/")

    def is_available(self, timeout=2.5):
        FakeClient.calls.append((self.base_url, timeout))
        return FakeClient.answer.get(self.base_url, FakeClient.default)


class ProbeCacheTest(unittest.TestCase):
    def setUp(self):
        self._orig = comfy.ComfyUIClient
        self._orig_cache = dict(comfy._AVAIL_CACHE)
        comfy.ComfyUIClient = FakeClient
        comfy._AVAIL_CACHE.clear()
        FakeClient.calls = []
        FakeClient.answer = {}
        FakeClient.default = False

    def tearDown(self):
        comfy.ComfyUIClient = self._orig
        comfy._AVAIL_CACHE.clear()
        comfy._AVAIL_CACHE.update(self._orig_cache)

    def test_second_call_hits_cache(self):
        comfy.probe_available("http://127.0.0.1:8188")
        comfy.probe_available("http://127.0.0.1:8188")
        comfy.probe_available("http://127.0.0.1:8188")
        self.assertEqual(len(FakeClient.calls), 1, "只该真探一次")

    def test_engines_sharing_instance_cache_is_the_point(self):
        """模拟 list_engines()：每次 new 一个引擎实例，缓存仍要命中。"""
        for _ in range(3):
            engine = comfy.ComfyEngine()
            engine.is_available_cached()
        self.assertEqual(len(FakeClient.calls), 1)

    def test_different_urls_are_separate(self):
        FakeClient.answer = {"http://a:8188": True, "http://b:8188": False}
        self.assertTrue(comfy.probe_available("http://a:8188"))
        self.assertFalse(comfy.probe_available("http://b:8188"))
        self.assertEqual(len(FakeClient.calls), 2)

    def test_expiry_retries(self):
        comfy.probe_available("http://127.0.0.1:8188", ttl=0)
        comfy.probe_available("http://127.0.0.1:8188", ttl=0)
        self.assertEqual(len(FakeClient.calls), 2, "ttl=0 表示每次都要重新确认")

    def test_offline_answer_is_cached_too(self):
        """离线也要缓存——离线才是慢的那条路（等满超时）。"""
        self.assertFalse(comfy.probe_available("http://127.0.0.1:8188"))
        self.assertFalse(comfy.probe_available("http://127.0.0.1:8188"))
        self.assertEqual(len(FakeClient.calls), 1)

    def test_forget_one_url(self):
        comfy.probe_available("http://a:8188")
        comfy.probe_available("http://b:8188")
        comfy.forget_available("http://a:8188")
        comfy.probe_available("http://a:8188")
        comfy.probe_available("http://b:8188")
        self.assertEqual([c[0] for c in FakeClient.calls],
                         ["http://a:8188", "http://b:8188", "http://a:8188"])

    def test_probe_never_raises(self):
        """探测本身炸了也要返回 False——列表接口不能因此 500。"""
        def boom(self, timeout=2.5):
            raise RuntimeError("网卡炸了")

        # 用 patch.object 而不是 del：del 会把类上的方法整个删掉，
        # 后面的用例调用 is_available 直接 AttributeError，全被 except 吞成 False。
        with mock.patch.object(FakeClient, "is_available", boom):
            self.assertFalse(comfy.probe_available("http://127.0.0.1:8188"))

    def test_timeout_is_short_for_listing(self):
        """列表页的探测超时得短：ComfyUI 离线时这笔时间直接算在首屏上。"""
        comfy.probe_available("http://127.0.0.1:8188")
        self.assertEqual(FakeClient.calls[0][1], 1.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
