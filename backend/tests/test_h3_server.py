"""H3 上限「按谁算」的回归。

坑的来历（2026-11-03）：上限以前按**本机**显存算。H3 跑在 ComfyUI 上，
8GB 笔记本连 24GB 工作站会被按 8GB 限死在 1344×768；反过来 24GB 机器连
8GB 服务器会放行 1080p 然后对面 OOM。现在按 ComfyUI 服务端显存算。

边界：只用 ComfyUI 官方 ``/system_stats``。**不读服务端文件系统、不认任何
自定义节点目录**——ComfyUI 里装什么插件、用什么权重是它的事，本程序只知道
「这台服务能画多大」。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import h3_protocol, h3_server  # noqa: E402


class FakeClient:
    """替掉真实 ComfyUIClient：不让测试发 HTTP。"""

    stats = None
    calls = 0

    def __init__(self, base_url=None, *_a, **_k):
        self.base_url = (base_url or "http://127.0.0.1:8188").rstrip("/")

    def system_stats(self, timeout=5):
        type(self).calls += 1
        if type(self).stats is None:
            raise RuntimeError("unreachable")
        return type(self).stats


def _stats(vram_gib=None, ram_gib=64.0, name="NVIDIA GeForce RTX 4090"):
    devices = []
    if vram_gib is not None:
        devices.append({
            "name": f"cuda:0 {name} : cudaMallocAsync",
            "type": "cuda",
            "vram_total": int(vram_gib * 2 ** 30),
            "vram_free": int(vram_gib * 2 ** 30 / 2),
        })
    return {
        "system": {"ram_total": int(ram_gib * 2 ** 30), "comfyui_version": "0.34.0"},
        "devices": devices,
    }


class Base(unittest.TestCase):
    def setUp(self):
        self._orig_client = h3_server.comfyui.ComfyUIClient
        h3_server.comfyui.ComfyUIClient = FakeClient
        FakeClient.stats = None
        FakeClient.calls = 0
        h3_server.reset_cache()
        h3_protocol.reset_limits()

    def tearDown(self):
        h3_server.comfyui.ComfyUIClient = self._orig_client
        h3_server.reset_cache()
        h3_protocol.reset_limits()


class ParseTest(unittest.TestCase):
    def test_parse_reads_server_vram(self):
        got = h3_server._parse(_stats(vram_gib=24.0))
        self.assertAlmostEqual(got["vram_gib"], 24.0, delta=0.1)
        self.assertAlmostEqual(got["ram_gib"], 64.0, delta=0.5)

    def test_device_name_has_no_prefix_or_backend_tail(self):
        """真机返回 "cuda:0 NVIDIA GeForce RTX 4060 : cudaMallocAsync"。

        这个名字要直接显示给用户，不能带 "cuda:0" 也不能带后端后缀——
        曾经切出过 "0 NVIDIA GeForce RTX 4060"，把卡序号当成了型号一部分。
        """
        got = h3_server._parse(_stats(vram_gib=8.0, name="NVIDIA GeForce RTX 4060"))
        self.assertEqual(got["gpu_name"], "NVIDIA GeForce RTX 4060")

    def test_parse_without_devices_does_not_invent(self):
        got = h3_server._parse({"system": {}, "devices": []})
        self.assertEqual(got["vram_gib"], -1.0)
        self.assertEqual(got["gpu_name"], "")

    def test_parse_skips_non_cuda_devices(self):
        doc = _stats(vram_gib=8.0)
        doc["devices"].insert(0, {"name": "cpu", "type": "cpu"})
        got = h3_server._parse(doc)
        self.assertAlmostEqual(got["vram_gib"], 8.0, delta=0.1)


class ProbeTest(Base):
    def test_unreachable_is_not_an_error(self):
        got = h3_server.probe(force=True)
        self.assertFalse(got["reachable"])
        self.assertEqual(got["vram_gib"], -1.0)

    def test_probe_cached_within_ttl(self):
        """提交任务是高频路径：不能用一次就发一次 HTTP。"""
        FakeClient.stats = _stats(vram_gib=24.0)
        h3_server.probe(force=True)
        h3_server.probe()
        h3_server.probe()
        self.assertEqual(FakeClient.calls, 1)

    def test_only_standard_fields_are_exposed(self):
        """边界：探测只应碰 /system_stats，不读服务端任何文件。

        字段集合本身就是护栏——如果有人又想加「读插件目录判断量化格式」，
        这条会立刻红。
        """
        FakeClient.stats = _stats(vram_gib=24.0)
        got = h3_server.probe(force=True)
        self.assertEqual(set(got), {
            "reachable", "base_url", "gpu_name", "vram_gib", "ram_gib", "comfy_version",
        })


class LimitsFollowServerTest(Base):
    def test_24gb_server_is_not_limited_by_local_8gb(self):
        """核心回归：本机 8GB + 服务端 24GB → 应该按大显存档放行。

        改之前这里会返回 8GB 档，用户拿 24GB 工作站也只能出 1344×768。
        """
        FakeClient.stats = _stats(vram_gib=24.0)
        row = h3_protocol.limits()
        self.assertEqual(row[3], "大显存档")
        self.assertEqual(row[1], 1920 * 1088)
        self.assertEqual(h3_protocol.limits_info()["vram_source"], "server")

    def test_8gb_server_on_24gb_machine_is_limited(self):
        """反过来也要拦住：本机大卡连 8GB 服务器，别放行 1080p 让对面 OOM。"""
        FakeClient.stats = _stats(vram_gib=8.0)
        row = h3_protocol.limits()
        self.assertEqual(row[3], "8GB 档")
        self.assertEqual(row[1], 1344 * 768)

    def test_unreachable_falls_back_to_local_and_says_so(self):
        """ComfyUI 不可达 → 退回本机口径，但来源必须标出来，不能假装是服务端。"""
        FakeClient.stats = None
        h3_protocol.limits()
        info = h3_protocol.limits_info()
        self.assertIn(info["vram_source"], ("local", "unknown"))
        self.assertNotEqual(info["vram_source"], "server")

    def test_server_without_vram_treated_as_unknown(self):
        """GPU 字段缺失（CPU 版 ComfyUI）→ 不能当成「显存无限」。"""
        FakeClient.stats = _stats(vram_gib=None)
        row = h3_protocol.limits()
        self.assertEqual(row[3], "8GB 档")

    def test_payload_exposes_basis(self):
        FakeClient.stats = _stats(vram_gib=24.0, name="RTX 4090")
        got = h3_protocol.payload()
        basis = got["limits"]["basis"]
        self.assertEqual(basis["vram_source"], "server")
        self.assertIn("RTX 4090", basis["gpu_name"])
        self.assertEqual(got["limits"]["max_frames"], 855)

    def test_reject_message_says_which_machine(self):
        """超上限的报错要说清「按哪台机器标定」，否则用户以为是程序乱拦。"""
        FakeClient.stats = _stats(vram_gib=8.0)
        h3_protocol.limits()
        with self.assertRaises(ValueError) as ctx:
            h3_protocol._too_big({"width": 1920, "height": 1088, "num_frames": 39})
        self.assertIn("ComfyUI 服务端", str(ctx.exception))

    def test_task_specific_comfy_url_wins(self):
        """任务自带 comfy_url 时按那台服务算，不能拿默认服务的显存去卡它。"""
        FakeClient.stats = _stats(vram_gib=24.0)
        row = h3_protocol.limits("http://192.168.1.50:8188")
        self.assertEqual(row[3], "大显存档")


if __name__ == "__main__":
    unittest.main()
