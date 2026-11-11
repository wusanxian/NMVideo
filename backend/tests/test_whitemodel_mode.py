"""白模生成模式（whitemodel）单测（第二十一批）。

覆盖三件事：
1. 模式白名单：新增 whitemodel 后，**旧模板不能凭空宣称支持它**（假声明）。
2. 提交链路：白模生成只要参考视频、不要角色图；视频要真的被上传并注入节点图。
3. 预设资产：内置白模工作流的节点/映射/权重必须自洽（改一个就能被这条用例抓住）。

**未执行**：整条链路（LoadVideo→SAM3DBody→Render→SaveVideo）本机未实跑，
`render_style` 动态 COMBO 的真实提交形态未验证——见预设 `_meta.unverified_params`。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import comfyui, generation, resource_guard, workflow_capabilities  # noqa: E402
from app.routers.white_model import _combo_detail  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
PRESET = BACKEND / "comfy_workflows" / "presets" / "nmvideo_whitemodel.json"


class _FakeClient:
    """最小 ComfyUI 客户端：节点全在、模型枚举不可信（走 fail-open）。"""

    base_url = "http://fake"

    def is_available(self, timeout=2.5):
        return True

    def object_info(self, node_names=None):
        if node_names:
            return {n: {"input": {}} for n in node_names}
        return {}


def _load_preset() -> dict:
    return json.loads(PRESET.read_text(encoding="utf-8"))


class ModeWhitelistTest(unittest.TestCase):
    def test_whitemodel_is_a_known_mode(self):
        self.assertIn("whitemodel", workflow_capabilities.SUPPORTED_MODES)
        # 旧回落集合必须**不带** whitemodel，否则旧模板会被判成支持白模
        self.assertNotIn("whitemodel", workflow_capabilities.LEGACY_MODES)

    def test_legacy_template_does_not_claim_whitemodel(self):
        """只声明了 name、没写 supports 的老模板 → 回落到旧 5 模式。

        这是加模式时最容易埋的雷：回落成「全部模式」会让它在界面上看起来
        支持白模，提交时才失败（而且看起来像工作流的错）。
        """
        cap = workflow_capabilities.capability_from_template(
            {"prompt": {"1": {}}, "_meta": {"name": "老模板"}})
        self.assertNotIn("whitemodel", cap["supports"])
        self.assertEqual(tuple(cap["supports"]), workflow_capabilities.LEGACY_MODES)

    def test_unknown_supports_falls_back_to_legacy_not_all(self):
        cap = workflow_capabilities.capability_from_template(
            {"prompt": {"1": {}}, "_meta": {"supports": ["nonsense"]}})
        self.assertNotIn("whitemodel", cap["supports"])

    def test_preset_declares_only_whitemodel(self):
        cap = workflow_capabilities.capability_from_template(_load_preset())
        self.assertEqual(cap["supports"], ["whitemodel"])
        self.assertEqual(cap["execution_kind"], "local")


class PresetContractTest(unittest.TestCase):
    def test_preset_is_self_consistent(self):
        data = _load_preset()
        prompt = data["prompt"]
        classes = {n["class_type"] for n in prompt.values()}
        meta = data["_meta"]
        for node in meta["required_nodes"]:
            self.assertIn(node, classes, f"预设声明了 {node} 但节点图里没有")
        # 每个映射点都必须落在真实节点上，否则注入会静默失效
        for key, target in data["_aivideo_map"].items():
            self.assertIn(str(target["node"]), prompt, f"{key} 映射到不存在的节点")
            self.assertIn(target["input"], prompt[str(target["node"])]["inputs"],
                          f"{key} 映射到不存在的输入")

    def test_declared_model_is_the_one_the_loader_uses(self):
        data = _load_preset()
        loader = [n for n in data["prompt"].values()
                  if n["class_type"] == "SAM3DBody_Loader"][0]
        self.assertEqual(data["_meta"]["required_models"], [loader["inputs"]["model_file"]])

    def test_render_style_default_has_no_nested_inputs(self):
        """预设默认值必须是「能单独提交」的选项。

        动态 COMBO 里 mesh（真白模）带嵌套 shader 子参数，提交形态未验证；
        选它当默认会让首次使用直接卡在校验错误上。
        """
        data = _load_preset()
        style = data["_meta"]["render_style_default"]
        self.assertIn(style, data["_meta"]["render_style_options"])
        render = [n for n in data["prompt"].values()
                  if n["class_type"] == "SAM3DBody_Render"][0]
        self.assertEqual(render["inputs"]["render_style"], style)

    def test_preset_is_not_marked_verified(self):
        """没实跑过就不许标 verified——这是给人看的承诺，不是装饰。"""
        self.assertFalse(_load_preset()["_meta"]["verified"])
        self.assertTrue(_load_preset()["_meta"]["unverified_params"])


class PrepareWhitemodelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _params(self, **over):
        params = {
            "comfy_url": "http://fake", "mode": "whitemodel", "engine": "comfyui",
            "prompt": "", "negative_prompt": "", "width": 1920, "height": 1080,
            "num_frames": 240, "fps": 24, "steps": 25, "guidance": 7.5, "seed": 1,
            "reference_video": "/outputs/uploads/a.mp4",
        }
        params.update(over)
        return params

    def _patch_upload(self, calls):
        from app import generation_assets

        def fake_video(params, client, url, key, output_dir):
            calls.append(key)
            return "uploaded.mp4"

        old = generation_assets.upload_condition_video
        generation_assets.upload_condition_video = fake_video
        self.addCleanup(setattr, generation_assets, "upload_condition_video", old)

    def test_requires_reference_video(self):
        with self.assertRaises(ValueError) as ctx:
            generation.prepare_comfy_workflow(
                self._params(reference_video=""), client=_FakeClient(),
                template=_load_preset(), output_dir=self.tmp.name)
        self.assertIn("参考视频", str(ctx.exception))

    def test_does_not_require_character_image(self):
        """白模生成不该被「必须有角色参考图」拦住——那是白模替换的要求。"""
        calls = []
        self._patch_upload(calls)
        graph, _tpl = generation.prepare_comfy_workflow(
            self._params(reference_image=""), client=_FakeClient(),
            template=_load_preset(), output_dir=self.tmp.name)
        self.assertEqual(calls, ["reference_video"])
        self.assertEqual(graph["1"]["inputs"]["file"], "uploaded.mp4")

    def test_fps_is_injected_into_create_video(self):
        calls = []
        self._patch_upload(calls)
        graph, _tpl = generation.prepare_comfy_workflow(
            self._params(fps=30), client=_FakeClient(),
            template=_load_preset(), output_dir=self.tmp.name)
        self.assertEqual(graph["6"]["inputs"]["fps"], 30)

    def test_template_without_video_mapping_is_rejected(self):
        template = _load_preset()
        template["_aivideo_map"].pop("reference_video")
        with self.assertRaises(ValueError) as ctx:
            generation.prepare_comfy_workflow(
                self._params(), client=_FakeClient(),
                template=template, output_dir=self.tmp.name)
        self.assertIn("参考视频", str(ctx.exception))

    def test_extra_reference_images_do_not_block(self):
        """镜头上前序模式留下的额外参考图，不该让白模生成报「不支持多参考图」。"""
        calls = []
        self._patch_upload(calls)
        params = self._params(reference_images=json.dumps(["/outputs/x.png"]))
        graph, _tpl = generation.prepare_comfy_workflow(
            params, client=_FakeClient(), template=_load_preset(),
            output_dir=self.tmp.name)
        self.assertEqual(graph["1"]["inputs"]["file"], "uploaded.mp4")


class ComboShapeTest(unittest.TestCase):
    def test_plain_combo(self):
        info = {"N": {"input": {"required": {"m": [["a.pt", "b.pt"]]}}}}
        opts, nested = _combo_detail(info, "N", "m")
        self.assertEqual(opts, ["a.pt", "b.pt"])
        self.assertEqual(nested, {})

    def test_combo_with_options_dict(self):
        info = {"N": {"input": {"required": {"m": ["COMBO", {"options": ["a.pt"]}]}}}}
        opts, nested = _combo_detail(info, "N", "m")
        self.assertEqual(opts, ["a.pt"])
        self.assertEqual(nested, {})

    def test_dynamic_combo_reports_nested_inputs(self):
        """动态 COMBO：mesh 需要嵌套 shader 子参数，silhouette 不需要。

        只报「有哪些选项」会让调用方以为每个都能单独提交。
        """
        info = {"SAM3DBody_Render": {"input": {"required": {"render_style": [
            "COMFY_DYNAMICCOMBO_V3",
            {"options": [
                {"key": "mesh", "inputs": {"shader": ["COMBO", {"options": ["x"]}]}},
                {"key": "silhouette"},
                {"key": "openpose_2d"},
            ]},
        ]}}}}
        opts, nested = _combo_detail(info, "SAM3DBody_Render", "render_style")
        self.assertEqual(opts, ["mesh", "silhouette", "openpose_2d"])
        self.assertEqual(nested, {"mesh": ["shader"]})

    def test_absent_input_is_empty(self):
        self.assertEqual(_combo_detail({}, "N", "m"), ([], {}))


class ComboOptionsTest(unittest.TestCase):
    def test_four_shapes(self):
        co = workflow_capabilities.combo_options
        self.assertEqual(co([["a.pt", "b.pt"]]), ["a.pt", "b.pt"])
        self.assertEqual(co(["a.pt", "b.pt"]), ["a.pt", "b.pt"])
        self.assertEqual(co(["COMBO", {"options": ["a.pt"]}]), ["a.pt"])
        self.assertEqual(
            co(["COMFY_DYNAMICCOMBO_V3", {"options": [{"key": "mesh"}, {"key": "silhouette"}]}]),
            ["mesh", "silhouette"])
        self.assertEqual(co([]), [])
        self.assertEqual(co(["COMBO"]), [])   # 只有标签没有 options：不把标签当选项

    def test_model_behind_combo_shape_is_recognized(self):
        """权重清单是 ["COMBO", {"options": [...]}] 时必须能认出来。

        回归用例：`_iter_loader_options` 原先只认 `[[...]]`，于是
        SAM3DBody 的权重（COMBO 形状）永远"未安装"，白模工作流会在
        权重齐备的机器上被提交前拦下——报的还是"请先安装模型"。
        """
        info = {"SAM3DBody_Loader": {"input": {"required": {
            "model_file": ["COMBO", {"options": ["sam_3d_body_dinov3_bf16.safetensors"]}],
        }}}}
        missing = workflow_capabilities.missing_models_from_info(
            info, ["sam_3d_body_dinov3_bf16.safetensors"])
        self.assertEqual(missing, [])

    def test_real_missing_model_is_still_reported(self):
        info = {"AnyLoader": {"input": {"required": {"ckpt_name": [["other.pt"]]}}}}
        missing = workflow_capabilities.missing_models_from_info(
            info, ["sam_3d_body_dinov3_bf16.safetensors"])
        self.assertEqual(missing, ["sam_3d_body_dinov3_bf16.safetensors"])


class ResourceProfileTest(unittest.TestCase):
    def test_whitemodel_does_not_scale_with_pixels(self):
        """白模链路不是扩散采样：同一段 1080p 素材不能被估成 17GB。
        用 comfyui 通用档估算会在 8GB 卡上误拒（这是按扩散经验套非扩散链路的错）。
        """
        big = {"mode": "whitemodel", "width": 1920, "height": 1080, "num_frames": 360}
        small = {"mode": "whitemodel", "width": 512, "height": 288, "num_frames": 24}
        need_big = resource_guard.estimate("comfyui", big)
        need_small = resource_guard.estimate("comfyui", small)
        self.assertLess(need_big["vram"], 8.0)
        self.assertAlmostEqual(need_big["vram"], need_small["vram"], places=1)
        # 同一份参数换成 t2v 就必须按扩散档算（否则等于把守卫关掉了）
        t2v = dict(big, mode="t2v")
        self.assertGreater(resource_guard.estimate("comfyui", t2v)["vram"],
                           need_big["vram"])


class UploadWhitelistTest(unittest.TestCase):
    def test_video_suffixes_accepted(self):
        # 策略是纯数据模块：本机缺 python-multipart 时 routers.assets 导不进来，
        # 但「收不收视频」这条规则不该因此变成不可测。
        from app import upload_policy
        for suffix in (".mp4", ".mov", ".webm"):
            self.assertEqual(upload_policy.classify(suffix), upload_policy.KIND_VIDEO)
        self.assertEqual(upload_policy.classify(".png"), upload_policy.KIND_MEDIA)
        self.assertEqual(upload_policy.classify(".exe"), "")
        self.assertEqual(upload_policy.classify(""), "")
        # 视频上限必须比图片/音频宽：15s 1080p 就能超过 50MB
        self.assertGreater(upload_policy.limit_mb(upload_policy.KIND_VIDEO),
                           upload_policy.limit_mb(upload_policy.KIND_MEDIA))


if __name__ == "__main__":
    unittest.main(verbosity=2)
