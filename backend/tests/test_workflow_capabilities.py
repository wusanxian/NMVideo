"""PR-03：工作流能力中心——能力自述 + /object_info 探测 + 生成前阻断。

验收对照（图中 PR-03）：
- 自动检查所需 ComfyUI 节点与输入能力（缺节点/不支持输入 → 生成前 400 阻断）
- 明确标注本地免费工作流或付费 API（capabilities 端点 + preset _meta）
- 显存资源提示（RTX 4060 8GB 场景）
- 兼容：无 _meta 的模板零影响（默认模板/旧配置不阻断）
"""
import json

import pytest

from app.comfyui import ComfyUIError, ComfyUIClient

GOOD_PROMPT = "武侠对决（中景, 固定, 4秒）"


# =====================================================================
# 能力解析
# =====================================================================
def test_capability_from_template_with_meta():
    from app.workflow_capabilities import capability_from_template

    cap = capability_from_template({
        "_meta": {"name": "Wan", "execution_kind": "local",
                  "model_id": "wan2.2-ti2v-5b",
                  "required_nodes": ["UNETLoader", "VHS_VideoCombine"],
                  "supports": ["t2v"], "min_vram_gb": 8,
                  "vram_hint": "8GB 可跑"},
    })
    assert cap["execution_kind"] == "local"
    assert cap["execution_label"] == "本地免费"
    assert cap["model_id"] == "wan2.2-ti2v-5b"
    assert cap["required_nodes"] == ["UNETLoader", "VHS_VideoCombine"]
    assert cap["supports"] == ["t2v"]
    assert cap["min_vram_gb"] == 8


def test_capability_from_template_without_meta_returns_none():
    from app.workflow_capabilities import capability_from_template

    assert capability_from_template({"prompt": {}}) is None
    assert capability_from_template({"_meta": "not a dict"}) is None


def test_capability_defaults_are_conservative():
    """字段缺失取保守默认：local + 全模式支持（与旧行为一致，不额外阻断）。"""
    from app.workflow_capabilities import SUPPORTED_MODES, capability_from_template

    cap = capability_from_template({"_meta": {"name": "x"}})
    assert cap["execution_kind"] == "local"
    assert cap["supports"] == list(SUPPORTED_MODES)
    assert cap["required_nodes"] == []
    assert cap["min_vram_gb"] == 0


def test_load_all_capabilities_covers_presets():
    """默认 + 当前模板 + 4 个内置预设；MiniMax 标付费 API，Wan/AnimeDiff 标本地。"""
    from app.workflow_capabilities import load_all_capabilities

    items = {it["id"]: it for it in load_all_capabilities()}
    for pid in ("current", "default", "anime_diff", "minimax_h3_t2v",
                "minimax_h3_i2v", "wan_ti2v"):
        assert pid in items, f"缺 {pid}"
    assert items["default"]["capability"] is None, "默认模板无 _meta → 不参与阻断"
    assert items["minimax_h3_t2v"]["capability"]["execution_kind"] == "paid_api"
    assert items["minimax_h3_i2v"]["capability"]["execution_kind"] == "paid_api"
    assert items["wan_ti2v"]["capability"]["execution_kind"] == "local"
    assert items["anime_diff"]["capability"]["execution_kind"] == "local"


def test_vram_warning_for_8gb_and_cpu():
    """RTX 4060 8GB 场景：Wan(8GB) 不警告；CPU 无独显警告；API 类不警告。"""
    from app.workflow_capabilities import vram_warning

    wan = {"name": "Wan", "min_vram_gb": 8, "vram_hint": "x"}
    api_cap = {"name": "MiniMax", "min_vram_gb": 0, "vram_hint": ""}
    rtx4060 = {"vram_gb": 8.0, "gpu_name": "RTX 4060"}
    cpu = {"vram_gb": 0.0, "gpu_name": ""}
    assert vram_warning(wan, rtx4060) is None
    assert vram_warning(api_cap, cpu) is None
    msg = vram_warning(wan, cpu)
    assert msg and "8" in msg and "显存" in msg


# =====================================================================
# object_info 探测
# =====================================================================
class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = ""

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_object_info_filters_requested_nodes(monkeypatch):
    import app.comfyui as comfyui_mod

    full = {"UNETLoader": {"x": 1}, "VHS_VideoCombine": {"y": 2}, "Other": {"z": 3}}
    monkeypatch.setattr(comfyui_mod.requests, "get",
                        lambda url, timeout: _Resp(200, full))
    c = ComfyUIClient()
    info = c.object_info(node_names=["UNETLoader", "Missing", "VHS_VideoCombine"])
    assert set(info) == {"UNETLoader", "VHS_VideoCombine"}, "只返回存在且被请求的节点"
    assert "Missing" not in info
    everything = c.object_info()
    assert set(everything) == set(full)


def test_object_info_errors_raise_comfyui_error(monkeypatch):
    import app.comfyui as comfyui_mod

    c = ComfyUIClient()

    def _boom(url, timeout):
        raise comfyui_mod.requests.RequestException("连接失败")

    monkeypatch.setattr(comfyui_mod.requests, "get", _boom)
    with pytest.raises(ComfyUIError):
        c.object_info()
    monkeypatch.setattr(comfyui_mod.requests, "get",
                        lambda url, timeout: _Resp(500, {}))
    with pytest.raises(ComfyUIError):
        c.object_info()
    monkeypatch.setattr(comfyui_mod.requests, "get",
                        lambda url, timeout: _Resp(200, ValueError("bad json")))
    with pytest.raises(ComfyUIError):
        c.object_info()


def test_check_missing_nodes_semantics(monkeypatch):
    from app.workflow_capabilities import check_missing_nodes

    c = ComfyUIClient()
    monkeypatch.setattr(ComfyUIClient, "object_info",
                        lambda self, node_names=None: {"A": {}})
    missing, ok = check_missing_nodes(c, ["A", "B"])
    assert ok and missing == ["B"]
    missing, ok = check_missing_nodes(c, [])
    assert ok and missing == []
    monkeypatch.setattr(ComfyUIClient, "object_info",
                        lambda self, node_names=None: (_ for _ in ()).throw(ComfyUIError("x")))
    missing, ok = check_missing_nodes(c, ["A"])
    assert ok is False and missing == [], "探测失败不虚构缺失清单"


# =====================================================================
# capabilities 端点
# =====================================================================
def test_capabilities_endpoint_reports_missing_nodes(client, monkeypatch):
    import app.hw_probe as hw_mod
    from app.workflow_capabilities import load_all_capabilities

    class _P:
        gpu_name = "RTX 4060"
        vram_gb = 8.0

    monkeypatch.setattr(hw_mod, "probe", lambda: _P())
    # 模拟 ComfyUI 在线但缺 VHS_VideoCombine 节点
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)
    monkeypatch.setattr(ComfyUIClient, "object_info",
                        lambda self, node_names=None: {
                            n: {} for n in (node_names or []) if n != "VHS_VideoCombine"})

    r = client.get("/api/comfy/capabilities")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["probe_ok"] is True
    assert data["gpu"]["vram_gb"] == 8.0
    items = {it["id"]: it for it in data["items"]}
    wan = items["wan_ti2v"]
    assert wan["capability"]["execution_label"] == "本地免费"
    assert "VHS_VideoCombine" in wan["missing_nodes"]
    assert wan["node_probe_done"] is True
    assert items["default"]["node_probe_ok"] is True, "无声明无需探测"


def test_capabilities_endpoint_offline_reports_not_probed(client, monkeypatch):
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: False)
    import app.hw_probe as hw_mod

    class _P:
        gpu_name = ""
        vram_gb = 0.0

    monkeypatch.setattr(hw_mod, "probe", lambda: _P())
    r = client.get("/api/comfy/capabilities")
    assert r.status_code == 200
    data = r.json()
    assert data["probe_ok"] is False
    wan = next(it for it in data["items"] if it["id"] == "wan_ti2v")
    assert wan["node_probe_done"] is False
    assert wan["missing_nodes"] == [], "离线时不虚构缺失清单"


# =====================================================================
# 生成前阻断
# =====================================================================
def _patch_template(monkeypatch, with_meta=True):
    import app.comfyui as comfyui_mod

    tpl = {
        "prompt": {"2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}},
        "_aivideo_map": {},
    }
    if with_meta:
        tpl["_meta"] = {"name": "测试工作流", "execution_kind": "local",
                        "required_nodes": ["SomeCustomNode"], "supports": ["t2v"],
                        "min_vram_gb": 8, "vram_hint": "x"}
    monkeypatch.setattr(comfyui_mod, "load_workflow_template", lambda: tpl)


def _mk_shot(client, name):
    pid = client.post("/api/projects", json={"name": name, "description": ""}).json()["id"]
    client.post(f"/api/projects/{pid}/script/import",
                json={"text": "场景一：雨夜\n甲走在街上（远景, 固定, 4秒）", "mode": "rebuild"})
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["shots"][0]["id"]
    client.patch(f"/api/shots/{sid}", json={"prompt": GOOD_PROMPT})
    return sid


def test_generate_blocks_on_missing_required_node(client, fresh_db, monkeypatch):
    """模板声明 required_nodes 且 /object_info 探测缺失 → 400 带缺项，不留 job。"""
    _patch_template(monkeypatch, with_meta=True)
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)
    monkeypatch.setattr(ComfyUIClient, "object_info", lambda self, node_names=None: {})
    sid = _mk_shot(client, "cap-missing")
    before = len(fresh_db.list_jobs(limit=1000))
    r = client.post(f"/api/shots/{sid}/generate", json={"engine": "comfyui"})
    assert r.status_code == 400
    assert "SomeCustomNode" in r.json()["detail"]
    assert len(fresh_db.list_jobs(limit=1000)) == before


def test_generate_passes_when_nodes_present(client, fresh_db, monkeypatch):
    _patch_template(monkeypatch, with_meta=True)
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)
    monkeypatch.setattr(ComfyUIClient, "object_info",
                        lambda self, node_names=None:
                            {n: {} for n in (node_names or [])})
    sid = _mk_shot(client, "cap-ok")
    r = client.post(f"/api/shots/{sid}/generate", json={"engine": "comfyui"})
    assert r.status_code == 201, r.text


def test_generate_blocks_unsupported_mode(client, fresh_db, monkeypatch):
    """声明只支持 t2v 的工作流收到 i2v 请求 → 400 明示（不静默忽略参考图）。"""
    _patch_template(monkeypatch, with_meta=True)
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)
    sid = _mk_shot(client, "cap-mode")
    # 先满足输入检查（参考图已设），让请求走到能力声明检查
    client.patch(f"/api/shots/{sid}",
                 json={"reference_image": "/outputs/first.png"})
    r = client.post(f"/api/shots/{sid}/generate",
                    json={"engine": "comfyui", "mode": "i2v"})
    assert r.status_code == 400
    assert "不支持" in r.json()["detail"]


def test_generate_no_meta_template_unaffected(client, fresh_db, monkeypatch):
    """兼容回归：无 _meta 的模板（默认模板/旧配置）不做能力阻断。"""
    _patch_template(monkeypatch, with_meta=False)
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)
    monkeypatch.setattr(ComfyUIClient, "object_info",
                        lambda self, node_names=None: (_ for _ in ()).throw(
                            AssertionError("无 _meta 不应触发节点探测")))
    sid = _mk_shot(client, "cap-nometa")
    r = client.post(f"/api/shots/{sid}/generate", json={"engine": "comfyui"})
    assert r.status_code == 201, r.text


# =====================================================================
# PR-04 剩余：required_models 模型扫描
# =====================================================================
WAN_UNET = "wan2.2_ti2v_5B_fp16.safetensors"
INFO_WITH_LOADERS = {
    "UNETLoader": {"input": {"required": {
        "unet_name": [["v1-5-pruned-emaonly.safetensors", WAN_UNET], {}]}}},
    "CLIPLoader": {"input": {"required": {
        "clip_name": [["umt5_xxl_fp8_e4m3fn_scaled.safetensors"], {}]}}},
}
INFO_NO_OPTIONS = {"SomeNode": {"input": {"required": {"seed": ["INT", {"default": 0}]}}}}


def test_capability_includes_required_models():
    from app.workflow_capabilities import capability_from_template

    cap = capability_from_template({
        "_meta": {"required_nodes": ["UNETLoader"],
                  "required_models": [WAN_UNET, "wan2.2_vae.safetensors"]},
    })
    assert cap["required_models"] == [WAN_UNET, "wan2.2_vae.safetensors"]
    assert capability_from_template({"_meta": {"name": "x"}})["required_models"] == []


def test_check_missing_models_semantics(monkeypatch):
    from app.workflow_capabilities import check_missing_models

    c = ComfyUIClient()
    monkeypatch.setattr(ComfyUIClient, "object_info",
                        lambda self, node_names=None: INFO_WITH_LOADERS)
    missing, ok = check_missing_models(c, [WAN_UNET])
    assert ok and missing == [], "loader 选项里有 → 已安装"
    missing, ok = check_missing_models(c, ["not_installed.safetensors"])
    assert ok and missing == ["not_installed.safetensors"]
    missing, ok = check_missing_models(c, [])
    assert ok and missing == [], "未声明 → 不探测不阻断"
    monkeypatch.setattr(ComfyUIClient, "object_info",
                        lambda self, node_names=None: (_ for _ in ()).throw(
                            ComfyUIError("offline")))
    missing, ok = check_missing_models(c, [WAN_UNET])
    assert ok is False and missing == [], "探测失败不虚构清单"
    monkeypatch.setattr(ComfyUIClient, "object_info",
                        lambda self, node_names=None: INFO_NO_OPTIONS)
    missing, ok = check_missing_models(c, [WAN_UNET])
    assert ok is False and missing == [], "响应无 loader 选项 → 探测不可信"


def test_preset_meta_declares_required_models():
    """内置预设声明 required_models（与 required_nodes 并存，不改旧字段）。"""
    from app.workflow_capabilities import load_all_capabilities

    items = {it["id"]: it for it in load_all_capabilities()}
    wan_models = items["wan_ti2v"]["capability"]["required_models"]
    assert WAN_UNET in wan_models
    anime_models = items["anime_diff"]["capability"]["required_models"]
    assert "mm_sd_v15_v2.ckpt" in anime_models
    assert items["minimax_h3_t2v"]["capability"]["required_models"] == [], "付费 API 不声明本地模型"


def test_capabilities_endpoint_reports_missing_models(client, monkeypatch):
    import app.hw_probe as hw_mod

    class _P:
        gpu_name = "RTX 4060"
        vram_gb = 8.0

    monkeypatch.setattr(hw_mod, "probe", lambda: _P())
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)
    # 模拟只装了 SD1.5 checkpoint 的 ComfyUI（缺 Wan 模型）
    info_partial = {
        "UNETLoader": {"input": {"required": {
            "unet_name": [["v1-5-pruned-emaonly.safetensors"], {}]}}},
        "CLIPLoader": {"input": {"required": {
            "clip_name": [["umt5_xxl_fp8_e4m3fn_scaled.safetensors"], {}]}}},
        "VAELoader": {"input": {"required": {
            "vae_name": [["wan2.2_vae.safetensors"], {}]}}},
    }
    monkeypatch.setattr(ComfyUIClient, "object_info",
                        lambda self, node_names=None: info_partial)
    r = client.get("/api/comfy/capabilities")
    assert r.status_code == 200, r.text
    wan = next(it for it in r.json()["items"] if it["id"] == "wan_ti2v")
    assert WAN_UNET in wan["missing_models"]
    assert wan["models_probe_done"] is True
    assert "umt5_xxl_fp8_e4m3fn_scaled.safetensors" not in wan["missing_models"]


def test_generate_blocks_on_missing_required_model(client, fresh_db, monkeypatch):
    """模板声明 required_models 且探测缺失 → 400 明示未装模型，不留 job。"""
    import app.comfyui as comfyui_mod

    tpl = {
        "prompt": {"2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}},
        "_aivideo_map": {},
        "_meta": {"name": "Wan 测试", "execution_kind": "local",
                  "required_models": [WAN_UNET], "supports": ["t2v"]},
    }
    monkeypatch.setattr(comfyui_mod, "load_workflow_template", lambda: tpl)
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)
    # 模拟未装 Wan 模型的 ComfyUI（只有 SD1.5 checkpoint）
    monkeypatch.setattr(ComfyUIClient, "object_info",
                        lambda self, node_names=None: {
                            "UNETLoader": {"input": {"required": {
                                "unet_name": [["v1-5-pruned-emaonly.safetensors"], {}]}}},
                        })
    sid = _mk_shot(client, "cap-model-missing")
    before = len(fresh_db.list_jobs(limit=1000))
    r = client.post(f"/api/shots/{sid}/generate", json={"engine": "comfyui"})
    assert r.status_code == 400
    assert WAN_UNET in r.json()["detail"]
    assert len(fresh_db.list_jobs(limit=1000)) == before


def test_generate_passes_when_models_installed(client, fresh_db, monkeypatch):
    import app.comfyui as comfyui_mod

    tpl = {
        "prompt": {"2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}},
        "_aivideo_map": {},
        "_meta": {"name": "Wan 测试", "execution_kind": "local",
                  "required_models": [WAN_UNET], "supports": ["t2v"]},
    }
    monkeypatch.setattr(comfyui_mod, "load_workflow_template", lambda: tpl)
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)
    monkeypatch.setattr(ComfyUIClient, "object_info",
                        lambda self, node_names=None: INFO_WITH_LOADERS)
    sid = _mk_shot(client, "cap-model-ok")
    r = client.post(f"/api/shots/{sid}/generate", json={"engine": "comfyui"})
    assert r.status_code == 201, r.text
