"""PR-N09：FreeVideo H3 一等引擎——注册、模板按引擎自动路由、备料契约、describe 探测。

模型：MiniMax H3（OpenVDN VDN-H3，8 步采样）。执行通道复用 ComfyEngine；
模板固定走 freevideo_h3_* 内置预设，与「当前模板」解耦。
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import comfyui
from app.engines import ENGINES, H3Engine, list_engines, validate_engine_mode
from app.generation import prepare_comfy_workflow

# FreeVideoGenerate 官方输入契约（generation.py 备料所需的 needed 集合）
_H3_SCHEMA = {"FreeVideoGenerate": {"input": {"required": [
    "text", "width", "height", "seconds", "seed", "base_steps",
    "two_pass", "frames", "preview", "refine_from"]}},
    # i2v/first_last 预设的 required_nodes 还包含 LoadImage
    "LoadImage": {"input": {"required": ["image"]}}}


def _mock_client(object_info_payload=None):
    c = MagicMock()
    c.is_available.return_value = True
    c.base_url = "http://127.0.0.1:8188"
    payload = object_info_payload if object_info_payload is not None else _H3_SCHEMA

    def _info(node_names=None):
        if node_names:
            return {k: payload[k] for k in node_names if k in payload}
        return dict(payload)

    c.object_info.side_effect = _info
    return c


def _t2v_params(**over):
    p = {
        "engine": "h3", "mode": "t2v", "preview": False,
        "prompt": "一只白猫走过屋顶", "negative_prompt": "",
        "width": 832, "height": 480, "num_frames": 81, "fps": 24,
        "steps": 30, "guidance": 5.0, "seed": 42,
    }
    p.update(over)
    return p


# ---------------------------------------------------------------------
# 注册与模式校验
# ---------------------------------------------------------------------

def test_h3_registered_with_all_modes():
    assert "h3" in ENGINES and ENGINES["h3"] is H3Engine
    engines = {e["id"]: e for e in list_engines()}
    assert engines["h3"]["supported_modes"] == [
        "t2v", "i2v", "first_last", "continuation"]
    for mode in ("t2v", "i2v", "first_last", "continuation"):
        validate_engine_mode("h3", mode)  # 不抛即通过


def test_resolve_template_routes_by_mode():
    for mode, preset in (("t2v", "freevideo_h3_t2v"),
                         ("i2v", "freevideo_h3_i2v"),
                         ("continuation", "freevideo_h3_i2v"),
                         ("first_last", "freevideo_h3_first_last")):
        tpl = comfyui.resolve_template_for("h3", mode)
        assert tpl["_meta"]["model_id"] == "vdn-minimax-h3"
        assert tpl["_meta"]["name"].startswith("FreeVideo H3")
        assert mode in tpl["_meta"]["supports"]
    # 其他引擎沿用当前模板（历史行为不变）
    assert comfyui.resolve_template_for("comfyui", "t2v") == \
        comfyui.load_workflow_template()
    assert comfyui.resolve_template_for("anime", "t2v") == \
        comfyui.load_workflow_template()


# ---------------------------------------------------------------------
# 备料契约：模板自动路由 + FreeVideo 参数归一
# ---------------------------------------------------------------------

def test_prepare_uses_h3_contract(tmp_path):
    client = _mock_client()
    params = _t2v_params()
    graph, template = prepare_comfy_workflow(params, client=client,
                                             output_dir=tmp_path)
    # 模板来自 h3 预设而非当前模板
    assert template["_meta"]["model_id"] == "vdn-minimax-h3"
    gen = graph["1"]["inputs"]
    # normalize_request 契约：8 步、帧数 5+17n（81 → 5+17*5=90）、fps=24、32 对齐
    assert gen["base_steps"] == 8
    assert gen["frames"] == 90
    assert params["fps"] == 24
    assert params["width"] % 32 == 0 and params["height"] % 32 == 0
    assert gen["preview"] is False
    assert gen["text"] == "一只白猫走过屋顶"


def test_prepare_h3_ignores_current_template(tmp_path, monkeypatch):
    """即使用户把当前模板 apply 成别的预设，h3 引擎仍走 H3 契约。"""
    monkeypatch.setattr(comfyui, "load_workflow_template",
                        lambda: {"prompt": {"9": {"class_type": "EmptyImage",
                                                    "inputs": {}}}})
    client = _mock_client()
    graph, template = prepare_comfy_workflow(_t2v_params(), client=client,
                                             output_dir=tmp_path)
    assert template["_meta"]["model_id"] == "vdn-minimax-h3"
    assert graph["1"]["class_type"] == "FreeVideoGenerate"


def test_prepare_h3_rejects_incompatible_node(tmp_path):
    client = _mock_client(object_info_payload={})  # 装了插件但接口对不上
    with pytest.raises(ValueError, match="接口不兼容"):
        prepare_comfy_workflow(_t2v_params(), client=client,
                               output_dir=tmp_path)


def test_prepare_h3_i2v_requires_reference(tmp_path):
    client = _mock_client()
    with pytest.raises(ValueError, match="图片条件"):
        prepare_comfy_workflow(_t2v_params(mode="i2v"), client=client,
                               output_dir=tmp_path)


def test_prepare_non_h3_unchanged(tmp_path, monkeypatch):
    """comfyui 引擎的备料不受本批影响：仍走当前模板、不做 H3 归一。"""
    monkeypatch.setattr(comfyui, "load_workflow_template",
                        lambda: {"prompt": {}, "_aivideo_map": {}})
    client = _mock_client()
    with pytest.raises(ValueError, match="模板为空"):
        prepare_comfy_workflow(_t2v_params(engine="comfyui"), client=client,
                               output_dir=tmp_path)


# ---------------------------------------------------------------------
# describe 探测与执行代理
# ---------------------------------------------------------------------

def test_describe_reports_missing_plugin():
    engine = H3Engine()
    engine._comfy.client = _mock_client(object_info_payload={})
    d = engine.describe()
    assert d["id"] == "h3" and d["status"] == "unavailable"
    assert "FreeVideo" in d["note"] and "custom_nodes" in d["note"]


def test_describe_ready_with_plugin():
    engine = H3Engine()
    engine._comfy.client = _mock_client()
    d = engine.describe()
    assert d["status"] == "ready" and d["note"] == ""


def test_describe_without_comfyui():
    engine = H3Engine()
    engine._comfy.client = MagicMock(is_available=lambda: False,
                                     base_url="http://127.0.0.1:9999")
    d = engine.describe()
    assert d["status"] == "unavailable" and "未连接到 ComfyUI" in d["note"]


def test_generate_delegates_to_comfy_channel():
    engine = H3Engine()
    sent = {}

    def fake_generate(**kwargs):
        sent.update(kwargs)
        return [Path("/tmp/out.mp4")]

    engine._comfy.generate = fake_generate
    out = engine.generate(
        prompt="p", negative_prompt="n", width=832, height=480,
        num_frames=90, fps=24, steps=8, guidance=5.0, seed=1,
        frames_dir=Path("/tmp/f"), on_progress=lambda *a: None,
        is_cancelled=lambda: False, workflow_snapshot="snap.json",
        on_submitted=lambda pid, cid: None)
    assert out == [Path("/tmp/out.mp4")]
    assert sent["workflow_snapshot"] == "snap.json"
    assert sent["prompt"] == "p"
