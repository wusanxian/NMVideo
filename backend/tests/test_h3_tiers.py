# -*- coding: utf-8 -*-
"""H3 画质档与画布上限的回归（本地保留，backend/tests 已在 .gitignore）。

钉住三件事：
1. 四档的参数必须与 FreeVideo 节点语义一致——两遍加速只服务 8 步首遍
   （two_pass.TWO_PASS_BASE_STEPS = 8），16/20 步只能单遍；
2. 归一后的参数就是存档参数：帧数落在 17n+5、fps 固定 24、步数来自档位；
3. 超过本机标定上限要明确拒绝并给替代方案，不能悄悄截断。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import h3_protocol  # noqa: E402


def params(**over):
    base = {
        "prompt": "a cat on a windowsill",
        "width": 832,
        "height": 480,
        "num_frames": 90,
        "fps": 24,
        "steps": 25,
        "guidance": 7.5,
        "seed": 1,
        "preview": False,
    }
    base.update(over)
    return base


class TierTests(unittest.TestCase):
    def setUp(self):
        h3_protocol._limits_cache = None

    def test_four_tiers_match_plugin_semantics(self):
        ids = [t["id"] for t in h3_protocol.tiers()]
        self.assertEqual(ids, ["light", "medium", "high", "max"])
        for tier_id, tier in h3_protocol.QUALITY_TIERS.items():
            # 节点契约：非 8 步首遍时两遍加速自动失效，配成 True 会让人以为还在加速
            if tier["base_steps"] != 8:
                self.assertFalse(tier["two_pass"], msg=f"{tier_id} 不该开两遍")
            else:
                self.assertTrue(tier["two_pass"])
            self.assertIn(tier["upscale_factor"], ("2", "1.5", "1.2"))
            self.assertIn(tier["step_cache"], ("none", "easycache-av", "fbcache"))

    def test_normalize_applies_tier(self):
        cases = {
            "light": (8, True, "none"),
            "medium": (8, True, "none"),
            "high": (16, False, "easycache-av"),
            "max": (20, False, "easycache-av"),
        }
        for tier_id, (steps, two_pass, cache) in cases.items():
            p = params(h3_quality=tier_id)
            h3_protocol.normalize_request(p)
            self.assertEqual(p["steps"], steps, msg=tier_id)
            self.assertIs(p["two_pass"], two_pass, msg=tier_id)
            self.assertEqual(p["step_cache"], cache, msg=tier_id)
            self.assertEqual(p["h3_quality"], tier_id)

    def test_unknown_tier_falls_back_to_light(self):
        p = params(h3_quality="ultra")  # 旧存档 / 手写错值
        h3_protocol.normalize_request(p)
        self.assertEqual(p["h3_quality"], "light")
        self.assertEqual(p["steps"], 8)

    def test_frames_snap_to_17n_plus_5(self):
        for wanted in (8, 24, 90, 121, 400):
            p = params(num_frames=wanted)
            h3_protocol.normalize_request(p)
            frames = p["num_frames"]
            self.assertGreaterEqual(frames, 39)
            self.assertEqual((frames - 5) % 17, 0, msg=f"{wanted} -> {frames}")

    def test_fps_forced_to_24(self):
        p = params(fps=8, num_frames=16)
        h3_protocol.normalize_request(p)
        self.assertEqual(p["fps"], 24)
        # 8fps 16 帧 = 2 秒 → 24fps 下至少 39 帧（节点最短 1.625 秒的量化结果）
        self.assertGreaterEqual(p["num_frames"], 39)

    def test_size_aligned_to_32(self):
        p = params(width=830, height=478)
        h3_protocol.normalize_request(p)
        self.assertEqual(p["width"] % 32, 0)
        self.assertEqual(p["height"] % 32, 0)


class LimitTests(unittest.TestCase):
    def setUp(self):
        h3_protocol._limits_cache = None

    def tearDown(self):
        h3_protocol._limits_cache = None

    def test_8gb_tier_limits(self):
        h3_protocol._limits_cache = (0.0, 1344 * 768, 430, "8GB 档")
        p = params(width=1920, height=1088)
        with self.assertRaises(ValueError) as ctx:
            h3_protocol.normalize_request(p)
        self.assertIn("像素", str(ctx.exception))

    def test_too_many_frames_suggests_split(self):
        h3_protocol._limits_cache = (0.0, 1344 * 768, 430, "8GB 档")
        p = params(num_frames=600)
        with self.assertRaises(ValueError) as ctx:
            h3_protocol.normalize_request(p)
        msg = str(ctx.exception)
        self.assertIn("430", msg)
        self.assertIn("续接", msg, msg="超时要给出可执行的路，不只是报错")

    def test_big_card_gets_wider_canvas(self):
        h3_protocol._limits_cache = (16.0, 1920 * 1088, 855, "大显存档")
        p = params(width=1920, height=1088, num_frames=400)
        h3_protocol.normalize_request(p)  # 不抛即通过
        self.assertEqual(p["width"], 1920)

    def test_limits_pick_tier_by_vram(self):
        """ComfyUI 不可达时的本机分档（可达时按服务端算，另有专门用例）。

        注意：2026-11-03 起上限优先取 ComfyUI 服务端显存，所以这个用例必须
        先把服务端置为不可达，否则测的是「服务端 8GB」而不是本机 24GB——
        真机上 8188 一起来这条就会假失败。
        """
        class FakeProbe:
            value = 8.0

            @staticmethod
            def vram_total_gb():
                return FakeProbe.value

        import app.hw_probe as real
        import app.h3_server as hs

        original = real.vram_total_gb
        orig_probe = hs.probe
        real.vram_total_gb = FakeProbe.vram_total_gb
        hs.probe = lambda *a, **k: {"reachable": False, "vram_gib": -1.0, "base_url": ""}
        try:
            h3_protocol._limits_cache = None
            self.assertEqual(h3_protocol.limits()[3], "8GB 档")
            FakeProbe.value = 24.0
            h3_protocol._limits_cache = None
            self.assertEqual(h3_protocol.limits()[3], "大显存档")
            # 探测失败按保守一侧：宁可提示 1344×768，也不要在没显存信息时放行 1080p
            FakeProbe.value = -1.0
            h3_protocol._limits_cache = None
            self.assertEqual(h3_protocol.limits()[3], "8GB 档")
        finally:
            real.vram_total_gb = original
            hs.probe = orig_probe

    def test_payload_has_canvas_options(self):
        h3_protocol._limits_cache = (0.0, 1344 * 768, 430, "8GB 档")
        got = h3_protocol.payload()
        self.assertEqual(len(got["tiers"]), 4)
        self.assertEqual(got["limits"]["max_frames"], 430)
        canvases = got["limits"]["canvas_options"]
        self.assertTrue(canvases)
        for w, hgt in canvases:
            self.assertLessEqual(w * hgt, 1344 * 768)
            self.assertEqual(w % 32, 0)
            self.assertEqual(hgt % 32, 0)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        h3_protocol._limits_cache = (0.0, 1344 * 768, 430, "8GB 档")

    def test_preview_keeps_tier_on_second_pass(self):
        """二采要按同一档位继续，否则「预览看着好、成片掉回 light」。"""
        import json

        p = params(preview=True, h3_quality="medium")
        h3_protocol.normalize_request(p)
        target = json.loads(p["generation_target"])
        self.assertEqual(target["h3_quality"], "medium")
        self.assertEqual(p["preview_kind"], "native")


if __name__ == "__main__":
    unittest.main()
