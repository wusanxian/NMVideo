"""资产版本层单测（第十九批）。

覆盖：
1. 迁移建表建列 + 重复打开幂等
2. 新角色自动落 v1；编辑后落 v2 且 v1 内容不可变（历史不丢）
3. 未版本化（current_version=0）→ 不落版、就地编辑（**旧行为兼容**）
4. promote 只移动"当前"指针，不改版本内容
5. 幕布：收编旧 reference_image / 场景绑定 / 镜头绑定优先级
6. 删除阻断：被引用 → 拒绝并给出明细
7. 冻结快照：锁版本 / 未锁版本 / 服装 refs / 旧场景幕布

**未执行**：本机未跑真实生成任务、未验证工作流端到端出片、未跑前端。本文件只验证
数据层与冻结语义；ComfyUI / GPU / 真实出片链路不在本轮范围内。

建库要十几秒（大量建表 + 落盘），整类共用一份库，别每个用例重建
（与 test_watchdog_abort 的既有做法一致）。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import asset_versions as av  # noqa: E402
from app import assets, db as db_module, outfits, plates  # noqa: E402
from app import storyboard as sb  # noqa: E402

# 所有持有 db 引用的模块——每个模块各自 `from .db import db`，都要换掉
_DB_MODULES = (db_module, assets, av, outfits, plates, sb)


class AssetVersionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 独立临时库，绝不碰开发库 backend/aivideo.db
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls.tmp.name)
        cls.db = db_module.Database(cls.root / "test.db")
        cls._saved = []
        for module in _DB_MODULES:
            cls._saved.append((module, getattr(module, "db")))
            module.db = cls.db

    @classmethod
    def tearDownClass(cls):
        for module, old in reversed(cls._saved):
            module.db = old
        try:
            cls.db.close()
        except Exception:
            pass
        cls.tmp.cleanup()

    def setUp(self):
        # 每个用例给一个干净的项目命名空间（库共用，靠项目隔离）
        self.project = sb.create_project(f"版本层测试-{self._testMethodName}")

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _scene_shot(self, title="场景1"):
        scene = sb.create_scene(self.project["id"], title)
        shot = sb.create_shot(scene["id"], title="镜头1", prompt="一只猫")
        return scene, shot

    def _tables(self, db=None):
        d = db or self.db
        return {r["name"] for r in d.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}

    def _cols(self, table, db=None):
        d = db or self.db
        return {r["name"] for r in d.fetch_all(f"PRAGMA table_info({table})")}

    # ------------------------------------------------------------------
    # 1. 迁移
    # ------------------------------------------------------------------
    def test_migration_creates_tables_and_columns(self):
        names = self._tables()
        for table in ("character_versions", "outfits", "outfit_versions",
                      "plates", "plate_versions"):
            self.assertIn(table, names, f"缺表: {table}")

        self.assertIn("current_version", self._cols("characters"))
        self.assertIn("identity_assets", self._cols("characters"))
        self.assertIn("character_version_refs", self._cols("shots"))
        self.assertIn("outfit_refs", self._cols("shots"))
        self.assertIn("plate_id", self._cols("shots"))
        self.assertIn("plate_version", self._cols("shots"))
        self.assertIn("plate_version_id", self._cols("scenes"))
        self.assertIn("asset_versions_snapshot", self._cols("jobs"))

    def test_migration_is_idempotent(self):
        """同一路径重复打开不得报错，表与列仍在（老库可反复升级）。"""
        path = self.root / "again.db"
        first = db_module.Database(path)
        second = db_module.Database(path)   # 第二次走"列已存在"分支
        names = {r["name"] for r in second.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertIn("plate_versions", names)
        cols = {r["name"] for r in second.fetch_all("PRAGMA table_info(shots)")}
        self.assertIn("outfit_refs", cols)
        # 第二次打开不得重复造假数据：新表都应为空
        self.assertEqual(
            second.fetch_one("SELECT COUNT(*) AS c FROM plate_versions")["c"], 0)
        first.close()
        second.close()

    # ------------------------------------------------------------------
    # 2. 角色：新建落 v1，编辑落 v2，历史不可变
    # ------------------------------------------------------------------
    def test_new_character_gets_v1(self):
        c = assets.create_character(self.project["id"], name="江初",
                                    appearance="黑长发少女")
        self.assertEqual(c["current_version"], 1)
        versions = assets.list_character_versions(c["id"])
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["version"], 1)
        self.assertEqual(versions[0]["appearance"], "黑长发少女")

    def test_edit_creates_new_version_and_keeps_old(self):
        c = assets.create_character(self.project["id"], name="江初",
                                    appearance="黑长发少女", outfit="黑衣")
        assets.update_character(c["id"], outfit="白衣")

        after = assets.get_character(c["id"])
        self.assertEqual(after["current_version"], 2)

        versions = {v["version"]: v for v in assets.list_character_versions(c["id"])}
        self.assertEqual(versions[1]["outfit"], "黑衣")   # 旧版本原样保留
        self.assertEqual(versions[2]["outfit"], "白衣")
        self.assertEqual(after["outfit"], "白衣")          # 池行仍是可就地读的当前值

    def test_edit_without_change_makes_no_version(self):
        """PATCH 成同样内容不该造出空版本——否则版本号会被噪声撑爆。"""
        c = assets.create_character(self.project["id"], name="江初", outfit="黑衣")
        assets.update_character(c["id"], outfit="黑衣")
        self.assertEqual(assets.get_character(c["id"])["current_version"], 1)
        self.assertEqual(len(assets.list_character_versions(c["id"])), 1)

    # ------------------------------------------------------------------
    # 3. 未版本化 → 回落旧行为
    # ------------------------------------------------------------------
    def test_unversioned_character_falls_back(self):
        c = assets.create_character(self.project["id"], name="老角色", outfit="旧衣")
        # 忠实模拟改造前的老数据：没有版本行，且 current_version = 0
        self.db.execute("DELETE FROM character_versions WHERE character_id = ?",
                        (c["id"],))
        self.db.execute("UPDATE characters SET current_version = 0 WHERE id = ?",
                        (c["id"],))

        assets.update_character(c["id"], outfit="新衣")
        after = assets.get_character(c["id"])
        self.assertEqual(after["current_version"], 0)      # 不打版本
        self.assertEqual(after["outfit"], "新衣")           # 就地编辑照旧
        self.assertEqual(len(assets.list_character_versions(c["id"])), 0)

        # 未版本化角色一样能被冻结：靠"入队那一刻的当前值"兜住可复现性
        scene, shot = self._scene_shot()
        sb.update_shot(shot["id"], character_ids=[c["id"]])
        snap = av.build_freeze_snapshot(
            sb.get_shot(shot["id"]), sb.get_scene(scene["id"]),
            assets.characters_of_shot(sb.get_shot(shot["id"])))
        entry = snap["characters"][0]
        self.assertEqual(entry["version"], 0)
        self.assertEqual(entry["data"]["outfit"], "新衣")

    # ------------------------------------------------------------------
    # 4. promote
    # ------------------------------------------------------------------
    def test_promote_moves_pointer_only(self):
        c = assets.create_character(self.project["id"], name="江初", outfit="v1衣")
        assets.update_character(c["id"], outfit="v2衣")
        assets.promote_character_version(c["id"], 1)

        self.assertEqual(assets.get_character(c["id"])["current_version"], 1)
        v2 = av.get_version_by_no(av.KIND_CHARACTER, c["id"], 2)
        self.assertEqual(v2["outfit"], "v2衣")   # 版本内容一个字都没动

    # ------------------------------------------------------------------
    # 5. 幕布
    # ------------------------------------------------------------------
    def test_promote_backdrop_and_binding_priority(self):
        scene, shot = self._scene_shot()
        sb.update_scene(scene["id"], reference_image="/outputs/uploads/old.png")
        plate = plates.promote_scene_backdrop(scene["id"], name="院子入口")

        # 收编不改旧字段
        self.assertEqual(sb.get_scene(scene["id"])["reference_image"],
                         "/outputs/uploads/old.png")
        # 场景默认幕布指向 v1
        ver = av.get_version_by_no(av.KIND_PLATE, plate["id"], 1)
        self.assertEqual(self.db.fetch_one(
            "SELECT plate_version_id FROM scenes WHERE id = ?", (scene["id"],)
        )["plate_version_id"], ver["id"])
        self.assertEqual(
            av.plate_image_for_shot(sb.get_shot(shot["id"]),
                                    sb.get_scene(scene["id"])),
            "/outputs/uploads/old.png")

        # 换图 = 落新版本，v1 保持不可变
        av.create_version(av.KIND_PLATE, plate["id"], note="换图",
                          image_url="/outputs/uploads/new.png")
        self.assertEqual(
            av.get_version_by_no(av.KIND_PLATE, plate["id"], 1)["image_url"],
            "/outputs/uploads/old.png")
        # 场景绑的是 version id，不跟随漂移 → 仍解析到 v1
        self.assertEqual(
            av.plate_image_for_shot(sb.get_shot(shot["id"]),
                                    sb.get_scene(scene["id"])),
            "/outputs/uploads/old.png")

        # 镜头级绑定覆盖场景默认，且跟随当前版本 → v2
        plates.bind_shot_plate(shot["id"], plate["id"], 0)
        self.assertEqual(
            av.plate_image_for_shot(sb.get_shot(shot["id"]),
                                    sb.get_scene(scene["id"])),
            "/outputs/uploads/new.png")

    def test_legacy_scene_backdrop_recorded_without_fake_plate_id(self):
        """未收编的旧幕布：快照要如实记 image_url，但不能伪造 plate_id。"""
        scene, shot = self._scene_shot()
        sb.update_scene(scene["id"], reference_image="/outputs/uploads/legacy.png")
        snap = av.build_freeze_snapshot(
            sb.get_shot(shot["id"]), sb.get_scene(scene["id"]), [])
        self.assertEqual(snap["plate"]["image_url"], "/outputs/uploads/legacy.png")
        self.assertEqual(snap["plate"]["id"], "")
        self.assertEqual(snap["plate"]["source"], "scene_legacy")

    # ------------------------------------------------------------------
    # 6. 删除阻断
    # ------------------------------------------------------------------
    def test_delete_guard_blocks_referenced_character(self):
        c = assets.create_character(self.project["id"], name="江初")
        scene, shot = self._scene_shot()
        sb.update_shot(shot["id"], character_ids=[c["id"]])

        usages = av.list_usages(av.KIND_CHARACTER, c["id"])
        self.assertEqual(len(usages), 1)
        self.assertEqual(usages[0]["shot_id"], shot["id"])
        ok, detail = av.delete_guard(av.KIND_CHARACTER, c["id"])
        self.assertFalse(ok)
        self.assertEqual(len(detail), 1)

    def test_delete_guard_blocks_referenced_plate(self):
        scene, shot = self._scene_shot()
        plate = plates.create_plate(self.project["id"], name="站台",
                                    image_url="/outputs/uploads/p.png")
        plates.bind_shot_plate(shot["id"], plate["id"], 1)
        ok, usages = plates.delete_plate(plate["id"])
        self.assertFalse(ok)
        self.assertEqual(len(usages), 1)
        self.assertIn("镜头幕布", usages[0]["detail"])

    def test_delete_allowed_when_unreferenced(self):
        plate = plates.create_plate(self.project["id"], name="没人用",
                                    image_url="/outputs/uploads/x.png")
        ok, _ = plates.delete_plate(plate["id"])
        self.assertTrue(ok)
        self.assertIsNone(plates.get_plate(plate["id"]))

    # ------------------------------------------------------------------
    # 7. 冻结快照
    # ------------------------------------------------------------------
    def test_freeze_snapshot_pins_character_version(self):
        c = assets.create_character(self.project["id"], name="江初", outfit="黑衣")
        scene, shot = self._scene_shot()
        sb.update_shot(shot["id"], character_ids=[c["id"]])

        av.set_shot_refs(shot["id"], av.KIND_CHARACTER, {c["id"]: 1})
        assets.update_character(c["id"], outfit="白衣")   # 池推进到 v2

        snap = av.build_freeze_snapshot(
            sb.get_shot(shot["id"]), sb.get_scene(scene["id"]),
            assets.characters_of_shot(sb.get_shot(shot["id"])))
        entry = snap["characters"][0]
        self.assertEqual(entry["ref_version"], 1)
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["data"]["outfit"], "黑衣")   # 锁住旧版，不吃新值

    def test_freeze_snapshot_follows_current_when_unlocked(self):
        c = assets.create_character(self.project["id"], name="江初", outfit="黑衣")
        scene, shot = self._scene_shot()
        sb.update_shot(shot["id"], character_ids=[c["id"]])
        assets.update_character(c["id"], outfit="白衣")

        snap = av.build_freeze_snapshot(
            sb.get_shot(shot["id"]), sb.get_scene(scene["id"]),
            assets.characters_of_shot(sb.get_shot(shot["id"])))
        entry = snap["characters"][0]
        self.assertEqual(entry["ref_version"], 0)
        self.assertEqual(entry["version"], 2)
        self.assertEqual(entry["data"]["outfit"], "白衣")

    def test_freeze_snapshot_includes_outfit_and_schema_version(self):
        scene, shot = self._scene_shot()
        outfit = outfits.create_outfit(self.project["id"], name="黑银",
                                       reference_assets=["/outputs/uploads/o.png"])
        av.set_shot_refs(shot["id"], av.KIND_OUTFIT, {outfit["id"]: 1})

        snap = av.build_freeze_snapshot(
            sb.get_shot(shot["id"]), sb.get_scene(scene["id"]), [])
        self.assertEqual(snap["schema_version"], av.SCHEMA_VERSION)
        self.assertEqual(len(snap["outfits"]), 1)
        self.assertEqual(snap["outfits"][0]["data"]["reference_assets"],
                         ["/outputs/uploads/o.png"])

    def test_set_shot_refs_rejects_missing_version(self):
        """锁一个不存在的版本必须报错——否则会写出指向空版本的引用，
        生成时静默回落，用户却以为锁住了。"""
        c = assets.create_character(self.project["id"], name="江初")
        scene, shot = self._scene_shot()
        with self.assertRaises(ValueError):
            av.set_shot_refs(shot["id"], av.KIND_CHARACTER, {c["id"]: 99})

    # ------------------------------------------------------------------
    # 8. 未知类型
    # ------------------------------------------------------------------
    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            av.list_versions("unknown", "x")
        with self.assertRaises(ValueError):
            av.set_shot_refs("s", "unknown", {})

    # ------------------------------------------------------------------
    # 9. 服装池基本可用
    # ------------------------------------------------------------------
    def test_outfit_pool_and_delete_guard(self):
        outfit = outfits.create_outfit(self.project["id"], name="黑银服装")
        self.assertEqual(outfit["current_version"], 1)
        scene, shot = self._scene_shot()
        av.set_shot_refs(shot["id"], av.KIND_OUTFIT, {outfit["id"]: 1})
        ok, usages = outfits.delete_outfit(outfit["id"])
        self.assertFalse(ok)
        self.assertEqual(len(usages), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
