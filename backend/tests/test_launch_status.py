"""启动页接口契约：前端模板直接读这些字段，字段变了页面就会白屏。

所以这里不测业务逻辑，只钉住「返回结构 + 三步状态机」——尤其别让
「ComfyUI 没起来」变成 500（首次启动就是这个状态，一崩就整页打不开）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _stubs  # noqa: E402

_stubs.install()

from fastapi import HTTPException  # noqa: E402

from app.routers import launch  # noqa: E402

# setUp 会把 _dir_usage 换成桩，先留一份真实现给「目录不存在」用例
ORIG_DIR_USAGE = launch._dir_usage


class LaunchStatusTest(unittest.TestCase):
    def setUp(self):
        self._saved = []
        self.patch(launch, "_dir_usage", lambda path, ttl=None: {"size_gb": 1.5, "files": 7})
        self.patch(launch, "_hardware", lambda: {
            "gpu_name": "RTX 4060", "vram_gb": 8.0, "free_vram_gb": 1.9,
            "ram_gb": 32.0, "available_ram_gb": 17.3, "device": "cuda",
        })

    def tearDown(self):
        for obj, name, old in reversed(self._saved):
            setattr(obj, name, old)

    def patch(self, obj, name, value):
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def comfy(self, online: bool):
        self.patch(launch, "_comfy_state", lambda: {
            "url": "http://127.0.0.1:8188", "online": online,
            "vram_total_gb": 8.0, "vram_free_gb": 1.9, "ram_free_gb": 17.0,
            "queue_running": 1 if online else 0, "queue_pending": 0,
        })

    def test_offline_is_reported_not_raised(self):
        """ComfyUI 没起来是**正常首启状态**：要 200 + 明确原因，不能 500。"""
        self.comfy(False)
        got = launch.launch_status()
        self.assertFalse(got["ok"])
        self.assertFalse(got["ready"])
        self.assertEqual(got["progress"], round(1 / 3, 3))
        states = {s["key"]: s["state"] for s in got["steps"]}
        self.assertEqual(states["backend"], "done")
        self.assertEqual(states["comfyui"], "blocked")
        self.assertIn("未检测到", got["steps"][1]["detail"])

    def test_ready_when_comfy_online(self):
        self.comfy(True)
        got = launch.launch_status()
        self.assertTrue(got["ok"])
        self.assertTrue(got["ready"])
        self.assertEqual(got["progress"], 1.0)
        self.assertTrue(all(s["state"] == "done" for s in got["steps"]))

    def test_payload_shape_for_template(self):
        """模板里铺开读的字段一个都不能少（缺了就是整页白屏）。"""
        self.comfy(True)
        got = launch.launch_status()
        for key in ("ok", "ready", "version", "progress", "steps", "comfyui",
                    "paths", "disk", "hardware", "resource", "recommend", "deps"):
            self.assertIn(key, got)
        for key in ("url", "online", "vram_total_gb", "vram_free_gb",
                    "queue_running", "queue_pending"):
            self.assertIn(key, got["comfyui"])
        for key in ("project", "backend", "db", "models", "outputs"):
            self.assertIn(key, got["paths"])
        self.assertIn("size_gb", got["paths"]["models"])
        self.assertIn("files", got["paths"]["outputs"])
        self.assertIn("free_gb", got["disk"])
        self.assertIn("gpu_name", got["hardware"])
        # 环境准备清单直接铺开读这两块，字段缺一个就是整页白屏
        rec = got["recommend"]
        for key in ("recommended", "summary", "options", "downloads"):
            self.assertIn(key, rec)
        self.assertTrue(rec["downloads"]["packs"])
        for key in ("total_gib", "need_disk_gb", "packs"):
            self.assertIn(key, rec["downloads"])
        for opt in rec["options"]:
            for key in ("id", "name", "available", "note", "blockers"):
                self.assertIn(key, opt)
        h3 = next(o for o in rec["options"] if o["id"] == "h3")
        for key in ("channel", "need_vram_gb", "need_ram_gb", "need_disk_gb", "native"):
            self.assertIn(key, h3)

    def test_dir_usage_survives_missing_dir(self):
        """目录不存在也不能抛（安装包首启时 models/outputs 可能就是空的）。"""
        got = ORIG_DIR_USAGE(Path("Z:/definitely/not/here"), ttl=0)
        self.assertEqual(got, {"size_gb": 0.0, "files": 0})

    def test_resource_policy_is_the_effective_one(self):
        """设置页「兼容性」显示的是实际生效策略，不是文档里的默认值。

        用户把 AIVIDEO_RESOURCE_GUARD 设成 off 之后，界面还写「自动降档」就是撒谎。
        """
        self.comfy(True)
        self.patch(launch.resource_guard, "guard_mode", lambda: "off")
        self.patch(launch.resource_watch, "watchdog_enabled", lambda: False)
        got = launch.launch_status()
        self.assertEqual(got["resource"]["guard"], "off")
        self.assertFalse(got["resource"]["watchdog"])
        # 预警线必须在硬底之上，否则「内存紧张」提示永远不会出现
        self.assertGreater(got["resource"]["ram_tight_gb"],
                           got["resource"]["ram_floor_gb"])


    def test_version_falls_back_to_runtime_version_file(self):
        """打包后没有 desktop/ 目录，版本号得从运行根的 VERSION 文件读。

        真实踩过：安装包里 `version` 返回空串，「关于」那行显示成「版本 —」。
        build.py 写的格式是 "0.1.9 2026-10-30"，只取第一段。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("0.7.3 2026-10-30", encoding="utf-8")
            self.patch(launch, "PROJECT_DIR", root)
            with mock.patch.dict(os.environ, {"AIVIDEO_VERSION": ""}):
                self.assertEqual(launch._version(), "0.7.3")

    def test_version_empty_when_nothing_available(self):
        """三处都没有就返回空串——不能抛，页面靠这个判断要不要显示「—」。"""
        with tempfile.TemporaryDirectory() as tmp:
            self.patch(launch, "PROJECT_DIR", Path(tmp))
            with mock.patch.dict(os.environ, {"AIVIDEO_VERSION": ""}):
                self.assertEqual(launch._version(), "")


class LaunchSetupTest(unittest.TestCase):
    """首启引导：引擎推荐由本机实测推出来，打开目录只认白名单。

    真实踩过两次，都记在这里防止再犯：

    1. 设计稿把「8GB 显存 + 32GB 内存 → 优先 H3」写死，但当时 H3 的口径是
       **进程内原生权重**（bf16 123.8GiB，int4 也要 52GB 内存常驻）→ 32GB 机器上
       必然 OOM。
    2. 反过来也踩过：拿原生门槛去卡 **ComfyUI 插件通道**，得出「8GB+32GB 跑不了
       H3」——而 FreeVideo 官方门槛就是 8GB 显存 / 16GB 内存（按块流式 + 文本
       编码器独立进程）。两条通道门槛差三倍，混算就是错。

    所以推荐必须是算出来的，而且必须分通道算。
    """

    BASE = {
        "gpu_name": "RTX 4060", "vram_gb": 8.0, "ram_gb": 32.0,
        "compute_cap": "sm_89", "device": "cuda", "disk_free_gb": 200.0,
        "model_id": "ti2v-5b", "quant": "fp8",
    }

    def test_h3_runs_on_8g_32g_via_plugin(self):
        """8GB 显存 + 32GB 内存 → 插件通道能跑，推荐 H3（这就是用户本机的情况）。"""
        got = launch._recommend_engine(dict(self.BASE))
        self.assertEqual(got["recommended"], "h3")
        h3 = next(o for o in got["options"] if o["id"] == "h3")
        self.assertTrue(h3["available"])
        self.assertEqual(h3["channel"], "comfyui")
        self.assertEqual(h3["need_vram_gb"], 8)
        self.assertEqual(h3["need_ram_gb"], 32)
        # 说清「能跑但慢」，别让人以为卡死了
        self.assertIn("流式", h3["note"])
        self.assertIn("H3", got["summary"])

    def test_native_channel_still_reported_as_blocked(self):
        """原生通道的门槛不能因为插件通道能跑就被抹掉：52GB 内存这条要说出来。"""
        got = launch._recommend_engine(dict(self.BASE))
        h3 = next(o for o in got["options"] if o["id"] == "h3")
        native = h3["native"]
        self.assertFalse(native["available"])
        self.assertEqual(native["min_ram_gb"], 52)
        self.assertIn("内存", native["reason"])

    def test_low_ram_blocks_h3_plugin(self):
        """内存低于本程序标定的 32GB → 挡掉，并说清差多少。"""
        got = launch._recommend_engine(dict(self.BASE, ram_gb=16.0))
        self.assertEqual(got["recommended"], "wan22")
        h3 = next(o for o in got["options"] if o["id"] == "h3")
        self.assertFalse(h3["available"])
        self.assertTrue(any("内存" in b and "32GB" in b for b in h3["blockers"]))

    def test_32gb_machine_reported_as_31_96_still_passes(self):
        """32GB 实机报 31.96GB（固件/核显预留）：门槛要按「装了多少」判。

        严格 `< 32` 会把目标配置本身判成不够——与显存 8188MiB 报成 7.996GB 同类。
        """
        got = launch._recommend_engine(dict(self.BASE, ram_gb=31.96))
        h3 = next(o for o in got["options"] if o["id"] == "h3")
        self.assertTrue(h3["available"], msg=f"31.96GB 被判内存不足：{h3['blockers']}")
        self.assertEqual(got["recommended"], "h3")

    def test_low_vram_blocks_h3_plugin(self):
        got = launch._recommend_engine(dict(self.BASE, vram_gb=4.0))
        h3 = next(o for o in got["options"] if o["id"] == "h3")
        self.assertFalse(h3["available"])
        self.assertTrue(any("显存" in b for b in h3["blockers"]))

    def test_disk_short_blocks_h3_too(self):
        """四个资源包约 49.5GiB，装不进磁盘同样是硬门槛，不能只看内存。"""
        got = launch._recommend_engine(dict(self.BASE, disk_free_gb=40.0))
        self.assertEqual(got["recommended"], "wan22")
        h3 = next(o for o in got["options"] if o["id"] == "h3")
        self.assertTrue(any("磁盘" in b for b in h3["blockers"]))

    def test_local_97gb_disk_is_enough(self):
        """真机 97GB 可用必须判「可跑」。

        2026-11-01 之前按 model_files.json 的 bf16 分片记成 100.3GiB，把本机
        97GB 判死、顺手推去下 32GB 的 Wan。实际落盘只有 49.5GiB（int8 量化包）。
        这条用例守着那个口径别复活。
        """
        got = launch._recommend_engine(dict(self.BASE, disk_free_gb=97.0))
        self.assertEqual(got["recommended"], "h3")
        h3 = next(o for o in got["options"] if o["id"] == "h3")
        self.assertTrue(h3["available"], msg=f"97GB 被判装不下：{h3['blockers']}")
        self.assertLess(h3["need_disk_gb"], 60)

    def test_disk_tight_warns_but_still_available(self):
        """刚好装得下也要提醒：装完磁盘基本写满。"""
        need = launch.h3_freevideo.need_disk_gib()
        got = launch._recommend_engine(dict(self.BASE, disk_free_gb=need + 5))
        h3 = next(o for o in got["options"] if o["id"] == "h3")
        self.assertTrue(h3["available"])
        self.assertTrue(h3["disk_tight"])
        self.assertIn("磁盘", got["summary"])

    def test_unknown_hardware_does_not_say_zero_gb(self):
        """探测失败（全 0）不能报「显存 0GB」——那是在编数据。"""
        got = launch._recommend_engine({})
        h3 = next(o for o in got["options"] if o["id"] == "h3")
        self.assertFalse(h3["available"])
        self.assertFalse(any("0GB" in b for b in h3["blockers"]))

    def test_no_cuda_flags_wan_as_slow(self):
        hw = dict(self.BASE, device="cpu", compute_cap="")
        got = launch._recommend_engine(hw)
        wan = next(o for o in got["options"] if o["id"] == "wan22")
        self.assertFalse(wan["available"])
        self.assertIn("CUDA", wan["note"])
        # 没 CUDA 时插件通道同样跑不了，不能因为磁盘够就放行
        h3 = next(o for o in got["options"] if o["id"] == "h3")
        self.assertFalse(h3["available"])
        self.assertTrue(any("CUDA" in b for b in h3["blockers"]))

    def test_pack_sizes_match_byte_counts(self):
        """资源包字节数是真机实测值，换 GiB 必须和展示值对得上。

        改体积时忘了改另一边 → 界面上「要下 50GB」和实际差一截。
        总量 49.5GiB 是 2026-11-01 在装了 FreeVideo 的机器上逐目录量的
        （int8-convrot 落盘口径），不是 model_files.json 的 bf16 清单。
        """
        for pack in launch.h3_freevideo.PACKS:
            self.assertAlmostEqual(pack.nbytes / 2 ** 30, pack.size_gib, delta=0.02,
                                   msg=f"{pack.name} 体积与字节数不一致")
        self.assertAlmostEqual(launch.h3_freevideo.total_gib(), 49.5, delta=0.3)
        self.assertAlmostEqual(launch.h3_freevideo.need_disk_gib(), 57.5, delta=0.3)

    def test_open_folder_rejects_unknown_key(self):
        """这个接口能拉起系统文件管理器，绝不能拿前端传的路径当参数。"""
        with self.assertRaises(HTTPException) as ctx:
            launch.open_folder(launch.OpenFolderBody(key="C:/Windows"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_open_folder_creates_then_reveals(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "outputs"
            saved_keys, saved_reveal = launch._FOLDER_KEYS, launch._reveal
            opened = []
            launch._FOLDER_KEYS = {"outputs": target}
            launch._reveal = lambda p: opened.append(str(p))
            try:
                got = launch.open_folder(launch.OpenFolderBody(key="outputs"))
            finally:
                launch._FOLDER_KEYS, launch._reveal = saved_keys, saved_reveal
            self.assertTrue(got["ok"])
            self.assertTrue(target.is_dir())
            self.assertEqual(opened, [str(target)])


class LaunchStorageTest(unittest.TestCase):
    """资源占用明细：只统计不删除，in_use 决定「这部分能不能腾出来」。

    真机场景：8GB 卡 + 磁盘差 19GB 才能跑 H3，而当初误下的 Wan 权重正好 29GB。
    这时列表必须把 Wan 标成「推荐引擎用不上」，否则用户不知道该删什么。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.models = Path(self._tmp.name) / "models"
        (self.models / "wan-ti2v-5b").mkdir(parents=True)
        (self.models / "hf-cache").mkdir()
        self._saved = []
        self.patch(launch, "MODELS_DIR", self.models)
        self.patch(launch, "_dir_usage", lambda path, ttl=None: {
            "size_gb": 29.0 if path.name == "wan-ti2v-5b" else 2.0,
            "files": 3,
        })

    def tearDown(self):
        for obj, name, old in reversed(self._saved):
            setattr(obj, name, old)

    def patch(self, obj, name, value):
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def test_h3_recommended_marks_wan_idle(self):
        data = launch.launch_storage(recommended="h3")
        self.assertEqual(data["total_gb"], 31.0)
        self.assertEqual(data["idle_gb"], 31.0)
        items = {i["id"]: i for i in data["items"]}
        self.assertFalse(items["wan-ti2v-5b"]["in_use"])
        self.assertFalse(items["hf-cache"]["in_use"])

    def test_wan_recommended_keeps_weights(self):
        data = launch.launch_storage(recommended="wan22")
        items = {i["id"]: i for i in data["items"]}
        self.assertTrue(items["wan-ti2v-5b"]["in_use"])
        self.assertEqual(data["idle_gb"], 2.0)

    def test_missing_models_dir_is_empty_not_error(self):
        """首启时 models 目录还不存在——必须给空列表，不能 500。"""
        self.patch(launch, "MODELS_DIR", Path(self._tmp.name) / "nope")
        data = launch.launch_storage()
        self.assertEqual(data["items"], [])
        self.assertEqual(data["total_gb"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)