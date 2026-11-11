"""白模替换提示词单测（第二十批）。

只验证提示词组装与职责模型——不涉及 ComfyUI、不出片。
**未执行**：白模生成链路（LoadVideo → SAM3DBody → Render → SaveVideo）本机未实跑，
链路参数（动态 COMBO 的 render_style）未经真实提交验证。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import white_model  # noqa: E402


class WhiteModelPromptTest(unittest.TestCase):
    def test_nine_sections_all_present(self):
        p = white_model.build_white_model_prompt()
        self.assertEqual(len(white_model.SECTIONS), 9)
        for _no, name in white_model.SECTIONS:
            self.assertIn(f"【{name}】", p, f"缺段: {name}")

    def test_section_order_is_stable(self):
        p = white_model.build_white_model_prompt()
        positions = [p.index(f"【{name}】") for _no, name in white_model.SECTIONS]
        self.assertEqual(positions, sorted(positions), "段顺序被改变")

    def test_duties_state_not_owns(self):
        """每份素材都必须写明「不采用」什么——这是整套写法真正起作用的部分。
        只写"参考什么"，模型会把白模的脸、原图的站姿一起参考进去。"""
        p = white_model.build_white_model_prompt()
        self.assertIn("不采用", p)
        d = white_model.duties("参考视频1", "图1", "图2", True, True)
        for key in ("action", "identity", "environment"):
            self.assertTrue(d[key]["owns"], f"{key} 缺 owns")
            self.assertTrue(d[key]["not_owns"], f"{key} 缺 not_owns")

    def test_white_model_appearance_must_be_replaced(self):
        """「不能只给白模换色」这条必须落到提示词里——否则最容易的错解就成立了。"""
        p = white_model.build_white_model_prompt(has_identity=True)
        self.assertIn("光头", p)
        self.assertIn("不能仅给白模换色", p)

    def test_environment_cannot_be_pasted_as_static_background(self):
        p = white_model.build_white_model_prompt(has_environment=True)
        self.assertIn("不能将场景参考图作为静态背景贴入", p)
        self.assertIn("视差", p)

    def test_missing_identity_rewrites_section(self):
        """没挂人物图时不能留一句「见参考图」——那会让模型去找一张不存在的图。

        注意：「不采用白模人物的面容/光头」这句在第 02 段仍然**应当**存在，
        因为 @视频1 无论有没有人物图都不能贡献外观；错的是第 03 段还去引用图1。
        """
        p = white_model.build_white_model_prompt(has_identity=False)
        self.assertIn("本次没有人物参考图", p)
        self.assertIn("外观以本段描述为准", p)
        # 第 03/06 段不得再出现对图1的引用（第 02 段的职责句里也不该有）
        self.assertNotIn("图1", p, "没挂人物图却仍引用了图1")

    def test_missing_environment_rewrites_section(self):
        p = white_model.build_white_model_prompt(has_environment=False)
        self.assertIn("本次没有场景参考图", p)

    def test_custom_refs_are_used_verbatim(self):
        """素材称呼必须与用户看到的一致：名字对不上，模型会以为在说另一份素材。"""
        p = white_model.build_white_model_prompt(
            action_ref="@视频1", identity_ref="角色定妆图", environment_ref="场景图A")
        self.assertIn("@视频1", p)
        self.assertIn("角色定妆图", p)
        self.assertIn("场景图A", p)

    def test_extra_constraint_appended_last(self):
        p = white_model.build_white_model_prompt(extra="本镜头必须保持 9:16 竖屏")
        self.assertIn("本镜头必须保持 9:16 竖屏", p)
        self.assertGreater(p.index("补充约束"), p.index("【声音与收束】"))

    def test_character_note_from_card(self):
        note = white_model.identity_note_from_character(
            {"appearance": "黑长直少女", "outfit": "银黑长裙"})
        self.assertIn("黑长直少女", note)
        self.assertIn("银黑长裙", note)
        self.assertEqual(white_model.identity_note_from_character(None), "")
        self.assertEqual(white_model.identity_note_from_character({}), "")

    def test_scene_note_from_card(self):
        note = white_model.environment_note_from_scene(
            {"environment": "庭院入口", "lighting": "黄昏侧逆光"})
        self.assertIn("庭院入口", note)
        self.assertIn("黄昏侧逆光", note)
        self.assertEqual(white_model.environment_note_from_scene(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
