"""资源守卫单测：估算/降档/拒绝/放行四条路径。

用标准库 unittest（不依赖 pytest）——本机 pip 走 HTTPS 经常 SSL 报错，
测试套件越少外部依赖越能真跑起来。
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import hw_probe, resource_guard


class GuardTest(unittest.TestCase):
    def setUp(self):
        self._saved = []
        self._env = {}

    def tearDown(self):
        for obj, name, old in reversed(self._saved):
            setattr(obj, name, old)
        for key, old in self._env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    # ---------- 打桩 ----------
    def patch(self, obj, name, value):
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def env(self, key, value):
        self._env.setdefault(key, os.environ.get(key))
        os.environ[key] = value

    def resources(self, *, ram=-1.0, ram_total=-1.0, vram_total=-1.0, vram_free=-1.0):
        self.patch(hw_probe, "free_ram_gb", lambda: ram)
        self.patch(hw_probe, "ram_total_gb", lambda: ram_total)
        self.patch(hw_probe, "vram_total_gb", lambda: vram_total)
        self.patch(hw_probe, "free_vram_gb", lambda: vram_free)

    def comfy(self, *, total, free, ram=64.0, ram_total=64.0):
        """桩掉 ComfyUI 视角的余量。

        ram 默认给足（而不是 -1）很重要：`available()` 在 ComfyUI 没报内存
        时会回落到**本机实测**，用例就变成「跑在什么机器上就什么结果」
        ——本机开着浏览器只剩 2GB 时，本该放行的用例会突然变红。
        """
        self.patch(
            resource_guard, "_comfy_avail",
            lambda url="": {"vram_total": total, "vram_free": free,
                            "ram": ram, "ram_total": ram_total, "source": "comfyui"},
        )

    def setUp_mode(self):
        self.env("AIVIDEO_RESOURCE_GUARD", "enforce")
        self.env("AIVIDEO_RESOURCE_WAIT_S", "0")

    # ---------- 用例 ----------
    def test_estimate_combines_fixed_and_variable(self):
        base = resource_guard.estimate("wan22", {"width": 832, "height": 480, "num_frames": 81})
        self.assertAlmostEqual(base["vram"], 7.5, delta=0.1)   # 固定 5 + 变量 2.5
        double = resource_guard.estimate("wan22", {"width": 832, "height": 480, "num_frames": 162})
        # 变量部分翻倍，固定部分不动
        self.assertAlmostEqual(double["vram"], 10.0, delta=0.1)

    def test_downgrade_when_card_too_small(self):
        self.setUp_mode()
        self.comfy(total=8.0, free=7.0)
        params = {"engine": "comfyui", "width": 1280, "height": 720,
                  "num_frames": 121, "fps": 24, "mode": "t2v", "preset": "ultra"}
        d = resource_guard.plan("comfyui", params)
        self.assertTrue(d.ok)
        self.assertTrue(d.changed)
        self.assertLess(d.params["width"] * d.params["height"], 1280 * 720)
        self.assertLessEqual(d.params["num_frames"], 121)
        self.assertEqual(d.params["preset"], "")          # 降档后不再声称「极致」
        self.assertIn("已自动降档", d.note)
        self.assertEqual(d.params["fps"], 24)             # 帧率不在降档范围内
        self.assertLessEqual(d.need["vram"] + resource_guard.VRAM_RESERVE_GB, 8.0)

    def test_no_change_when_resources_plenty(self):
        self.setUp_mode()
        self.comfy(total=24.0, free=20.0)
        params = {"engine": "comfyui", "width": 832, "height": 480, "num_frames": 81}
        d = resource_guard.plan("comfyui", params)
        self.assertTrue(d.ok)
        self.assertFalse(d.changed)
        self.assertEqual(d.params["width"], 832)
        self.assertEqual(d.note, "")

    def test_reject_when_fixed_memory_cannot_fit(self):
        self.setUp_mode()
        # H3 原生固定吃 32GB 内存，系统只剩 3GB：降分辨率救不了
        self.resources(ram=3.0, vram_total=24.0, vram_free=20.0)
        params = {"engine": "h3_native", "width": 832, "height": 480, "num_frames": 81}
        d = resource_guard.plan("h3_native", params)
        self.assertFalse(d.ok)
        self.assertIn("内存", d.reason)
        self.assertIn("已降到最低可用档", d.reason)

    def test_native_uses_free_vram_not_total(self):
        self.setUp_mode()
        # 24GB 卡只剩 1GB 空闲：进程内推理赶不走别的进程
        self.resources(ram=64.0, vram_total=24.0, vram_free=1.0)
        params = {"engine": "anime", "width": 512, "height": 512, "num_frames": 33}
        d = resource_guard.plan("anime", params)
        self.assertFalse(d.ok)
        self.assertIn("显存空闲", d.reason)

    def test_comfy_uses_total_capacity(self):
        self.setUp_mode()
        # ComfyUI 会卸载自己的模型：空闲少也能跑，判据是总容量
        self.comfy(total=24.0, free=0.5, ram=32.0)
        params = {"engine": "comfyui", "width": 832, "height": 480, "num_frames": 81}
        d = resource_guard.plan("comfyui", params)
        self.assertTrue(d.ok)
        self.assertFalse(d.changed)
        self.assertIn("其他程序占用显卡", d.note)

    def test_comfy_ram_judged_by_total_not_available(self):
        self.setUp_mode()
        # 32GB 内存的机器、当前只剩 3GB：ComfyUI 通道按总量放行（会走 pagefile），
        # 只提示变慢——不能因为用户开着浏览器就把 H3 永久判死
        self.comfy(total=24.0, free=20.0, ram=3.0, ram_total=32.0)
        params = {"engine": "h3", "width": 832, "height": 480,
                  "num_frames": 81, "mode": "t2v"}
        d = resource_guard.plan("h3", params)
        self.assertTrue(d.ok, d.reason)
        self.assertIn("可能变慢", d.note)

    def test_comfy_ram_rejected_when_total_too_small(self):
        self.setUp_mode()
        # 16GB 内存装不下 H3 的 26GB 需求 → 降档也救不了，直接拒绝
        self.comfy(total=24.0, free=20.0, ram=10.0, ram_total=16.0)
        params = {"engine": "h3", "width": 832, "height": 480,
                  "num_frames": 81, "mode": "t2v"}
        d = resource_guard.plan("h3", params)
        self.assertFalse(d.ok)
        self.assertIn("内存共 16GB", d.reason)

    def test_ram_floor_blocks_everything(self):
        self.setUp_mode()
        # 可用内存 0.5GB：系统在换页，任何引擎都不该开跑
        self.resources(ram=0.5, ram_total=32.0, vram_total=24.0, vram_free=20.0)
        params = {"engine": "anime", "width": 416, "height": 256, "num_frames": 17}
        d = resource_guard.plan("anime", params)
        self.assertFalse(d.ok)
        self.assertIn("地板", d.reason)

    def test_probe_failure_fails_open(self):
        self.setUp_mode()
        self.resources(ram=-1.0, vram_total=-1.0, vram_free=-1.0)
        params = {"engine": "wan22", "width": 1280, "height": 720, "num_frames": 121}
        d = resource_guard.plan("wan22", params)
        self.assertTrue(d.ok)
        self.assertFalse(d.changed)

    def test_warn_mode_never_blocks(self):
        self.setUp_mode()
        self.env("AIVIDEO_RESOURCE_GUARD", "warn")
        self.resources(ram=1.0, vram_total=8.0, vram_free=1.0)
        params = {"engine": "h3_native", "width": 1280, "height": 720, "num_frames": 121}
        d = resource_guard.plan("h3_native", params)
        self.assertTrue(d.ok)
        self.assertIn("仅提示", d.note)
        self.assertEqual(d.params["width"], 1280)

    def test_off_mode_passthrough(self):
        self.setUp_mode()
        self.env("AIVIDEO_RESOURCE_GUARD", "off")
        self.resources(ram=0.5, vram_total=4.0, vram_free=0.2)
        params = {"engine": "h3_native", "width": 1280, "height": 720, "num_frames": 121}
        d = resource_guard.plan("h3_native", params)
        self.assertTrue(d.ok)
        self.assertFalse(d.changed)
        self.assertEqual(d.note, "")

    def test_min_vram_from_workflow_capability(self):
        self.setUp_mode()
        # h3 的 ComfyUI 预设声明 min_vram_gb=16 → 8GB 卡上装不下
        self.patch(resource_guard, "_workflow_min_vram", lambda e, p: 16.0)
        self.comfy(total=8.0, free=7.0, ram=64.0)
        params = {"engine": "h3", "width": 832, "height": 480,
                  "num_frames": 81, "mode": "t2v"}
        d = resource_guard.plan("h3", params)
        self.assertFalse(d.ok)
        self.assertIn("要求至少 16GB 显存", d.reason)
        self.assertIn("降低分辨率无法满足", d.reason)

    def test_min_vram_floor_is_not_padded_by_reserve(self):
        self.setUp_mode()
        # 8GB 卡跑声明 min_vram_gb=8 的工作流：刚好够，不能被 1GB 余量判成不够
        self.patch(resource_guard, "_workflow_min_vram", lambda e, p: 8.0)
        self.comfy(total=8.0, free=8.0, ram=64.0)
        params = {"engine": "h3", "width": 416, "height": 256,
                  "num_frames": 17, "mode": "t2v"}
        d = resource_guard.plan("h3", params)
        self.assertTrue(d.ok, d.reason)

    def test_downgrade_never_below_usable_floor(self):
        self.setUp_mode()
        # 8GB 卡跑 H3 480p：只能降到「仍可用」的规格，不允许降到 256×144 之类
        self.patch(resource_guard, "_workflow_min_vram", lambda e, p: 0.0)
        self.comfy(total=8.0, free=7.0, ram=64.0)
        params = {"engine": "h3", "width": 832, "height": 480,
                  "num_frames": 81, "mode": "t2v"}
        d = resource_guard.plan("h3", params)
        if d.changed:
            self.assertGreaterEqual(d.params["width"], resource_guard.MIN_RESULT_WIDTH)
            self.assertGreaterEqual(d.params["num_frames"], resource_guard.MIN_RESULT_FRAMES)
        else:
            self.assertFalse(d.ok)

    def test_wait_for_headroom_no_downgrade(self):
        self.setUp_mode()
        self.resources(ram=2.0, vram_total=24.0, vram_free=20.0)
        params = {"engine": "h3_native", "width": 832, "height": 480, "num_frames": 81}
        d = resource_guard.wait_for_headroom("h3_native", params, timeout_s=0)
        self.assertFalse(d.ok)
        self.assertEqual(d.params["width"], 832)   # 只等不降档

    def test_downscale_keeps_global_minimums(self):
        self.setUp_mode()
        self.resources(ram=64.0, vram_total=4.0, vram_free=3.0)
        params = {"engine": "anime", "width": 1344, "height": 1344, "num_frames": 121}
        d = resource_guard.plan("anime", params)
        # 4GB 卡连最低档都装不下 → 拒绝，且不产出越界参数
        self.assertFalse(d.ok)
        self.assertEqual(d.params["width"], 1344)


if __name__ == "__main__":
    unittest.main(verbosity=2)
