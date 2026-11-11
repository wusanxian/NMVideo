"""模型库：状态判定与「不自动下载」的边界。

真机教训：8GB/32GB 机器上 H3 差 1.3GB 磁盘跑不了，启动器按 auto 决策
把 Wan 当兜底，结果顺手拉了 32GB 权重——用户既没要、磁盘还被吃掉一块。
所以这里钉住三件事：
  1. 缺权重只报状态，绝不自动开下；
  2. 同一时刻只允许一个下载任务（带宽/磁盘都受不了两份并行）；
  3. 「下全了」以关键索引文件为准，不是「目录非空」。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _stubs  # noqa: E402

_stubs.install()

from app import model_store  # noqa: E402


class ModelStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "models"
        self.root.mkdir(parents=True)
        self._saved = []
        self.patch(model_store, "MODELS_DIR", self.root)
        self.patch(model_store, "_scan_cache", {})
        self.patch(model_store, "_tasks", {})
        self.patch(model_store, "_procs", {})

    def tearDown(self):
        for obj, name, old in reversed(self._saved):
            setattr(obj, name, old)
        model_store._scan_cache.clear()

    def patch(self, obj, name, value):
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def touch(self, rel: str, size: int = 1024) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        return p

    # ---------- 状态 ----------
    def test_absent_when_dir_missing(self):
        item = model_store.get_item("wan-ti2v-5b")
        got = model_store.scan(item)
        self.assertEqual(got["state"], "absent")
        self.assertEqual(got["size_gb"], 0.0)

    def test_partial_when_files_but_no_index(self):
        """目录里有东西不代表能用：没 model_index.json 就是下了一半。"""
        self.touch("wan-ti2v-5b/transformer/diffusion_pytorch_model.safetensors", 65536)
        got = model_store.scan(model_store.get_item("wan-ti2v-5b"))
        self.assertEqual(got["state"], "partial")
        self.assertGreater(model_store._dir_bytes(self.root / "wan-ti2v-5b"), 0)

    def test_marker_alone_is_not_enough(self):
        """刚开下就显示「已就绪」是真机踩到的坑。

        HF 仓库根的 model_index.json 只有几百字节，snapshot 一开始就落地；
        只看它会让界面报 ready，引擎加载却报缺权重。所以还要过体积下限。
        """
        self.touch("wan-ti2v-5b/model_index.json", 512)
        got = model_store.scan(model_store.get_item("wan-ti2v-5b"))
        self.assertEqual(got["state"], "partial")
        self.assertFalse(model_store.wan_ready("ti2v-5b"))

    def test_ready_when_marker_present(self):
        import dataclasses

        # 完整权重要几十 GB，测试里用 min_gb=0 的副本验证 marker 分支
        item = dataclasses.replace(model_store.get_item("wan-ti2v-5b"), min_gb=0.0)
        self.touch("wan-ti2v-5b/model_index.json", 512)
        got = model_store.scan(item, fresh=True)
        self.assertEqual(got["state"], "ready")

    def test_wan_ready_accepts_both_id_spellings(self):
        """hw_probe 给的是 ti2v-5b，界面/配置里可能写 wan-ti2v-5b，别认错。"""
        self.assertFalse(model_store.wan_ready("ti2v-5b"))
        self.assertFalse(model_store.wan_ready("wan-t2v-a14b"))
        self.assertFalse(model_store.wan_ready("t2v-a14b"))

    def test_two_wan_folders_judged_separately(self):
        """两档权重是两个目录：一个目录有东西不能让另一个也「就绪」。"""
        self.touch("wan-ti2v-5b/model_index.json", 512)
        self.assertFalse(model_store.wan_ready("t2v-a14b"))
        got = model_store.scan(model_store.get_item("wan-t2v-a14b"), fresh=True)
        self.assertEqual(got["state"], "absent")

    def test_dir_bytes_is_recursive(self):
        self.touch("a/b/c.bin", 100)
        self.touch("a/d.bin", 200)
        self.assertEqual(model_store._dir_bytes(self.root / "a"), 300)
        # 不存在的目录给 0，不抛
        self.assertEqual(model_store._dir_bytes(self.root / "nope"), 0)

    # ---------- 下载边界 ----------
    def test_unknown_model_rejected(self):
        ok, msg = model_store.start("wan-9b")
        self.assertFalse(ok)
        self.assertIn("未知模型", msg)

    def test_no_auto_download_on_snapshot(self):
        """单纯看一眼状态不能把下载拉起来——这是本次改动的核心约束。"""
        model_store.snapshot()
        self.assertEqual(model_store._tasks, {})
        self.assertEqual(model_store._procs, {})

    def test_only_one_task_at_a_time(self):
        with mock.patch.object(model_store, "_run", lambda *a: None):
            ok, _ = model_store.start("wan-ti2v-5b")
            self.assertTrue(ok)
            ok2, msg2 = model_store.start("anime")
            self.assertFalse(ok2)
            self.assertIn("已有下载任务", msg2)
            # 同一个模型重复点也不能再起一个线程
            ok3, msg3 = model_store.start("wan-ti2v-5b")
            self.assertFalse(ok3)
            self.assertIn("正在下载", msg3)

    def test_cancel_without_task_is_reported(self):
        ok, msg = model_store.cancel("anime")
        self.assertFalse(ok)
        self.assertIn("没有正在进行", msg)

    def test_snapshot_shape_for_template(self):
        """前端铺开读这些字段，少一个就是白块。"""
        data = model_store.snapshot()
        for key in ("models_dir", "disk_free_gb", "downloading", "items"):
            self.assertIn(key, data)
        ids = [i["id"] for i in data["items"]]
        self.assertIn("wan-ti2v-5b", ids)
        for item in data["items"]:
            for key in ("id", "label", "detail", "engine", "repo", "approx_gb",
                        "state", "size_gb", "path", "task"):
                self.assertIn(key, item)
            self.assertIn(item["state"], ("ready", "partial", "absent"))

    def test_progress_is_measured_not_invented(self):
        """进度分子必须是磁盘真实增量，别编百分比。"""
        import dataclasses

        # 用一个很小的体积估计值，免得为了 25% 真写 8GB 测试文件
        item = dataclasses.replace(model_store.get_item("wan-ti2v-5b"), approx_gb=0.001)
        task = model_store.Task(model_id=item.id, size_start=0)
        before = task.to_dict(item)
        self.assertEqual(before["percent"], 0.0)
        self.touch("wan-ti2v-5b/blob.bin", int(item.approx_gb * 2**30 / 4))
        after = task.to_dict(item)
        self.assertGreater(after["percent"], 20)
        self.assertLess(after["percent"], 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
