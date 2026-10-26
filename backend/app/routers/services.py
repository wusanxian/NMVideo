"""模型服务路由：ComfyUI / LLM / 图像生成 / TTS 四类服务的
状态汇总、配置管理、连通测试，以及 AI 编剧 / 提示词优化 / 图像生成能力端点。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import imagegen, llm, settings as settings_module, tts
from ..comfyui import ComfyUIClient, ComfyUIError, apply_preset, list_presets

router = APIRouter(prefix="/api", tags=["services"])


# =====================================================================
# 服务状态汇总
# =====================================================================
@router.get("/services/status")
def services_status():
    """四类模型服务健康状态（前端设置页总览）"""
    client = ComfyUIClient()
    comfy_ok = client.is_available()
    tts_ok, tts_note = tts.tts_available()
    return {
        "comfyui": {
            "configured": True,
            "online": comfy_ok,
            "base_url": client.base_url,
            "note": "" if comfy_ok else "未检测到 ComfyUI，启动后可连接",
        },
        "llm": {
            **llm.status(),
            "online": llm.status()["configured"],
            "note": "支持 DeepSeek/通义/Kimi/Ollama 等 OpenAI 兼容端点",
        },
        "imagegen": {
            **imagegen.status(),
            "online": imagegen.status()["configured"],
            "note": "支持任意 OpenAI 兼容 images 端点",
        },
        "tts": {
            "configured": True,
            "online": tts_ok,
            "note": tts_note,
        },
    }


# =====================================================================
# 配置读写
# =====================================================================
class LLMConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class ImageGenConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class ComfyConfig(BaseModel):
    comfy_url: str = ""


@router.get("/services/config")
def get_all_config():
    """读全部服务配置（api_key 打码返回）"""
    s = settings_module.get_settings()

    def mask(v: str) -> str:
        if not v:
            return ""
        if len(v) <= 8:
            return "****"
        return v[:4] + "****" + v[-4:]

    return {
        "llm": {**s.get("llm", {}), "api_key": mask(s.get("llm", {}).get("api_key", ""))},
        "imagegen": {**s.get("imagegen", {}), "api_key": mask(s.get("imagegen", {}).get("api_key", ""))},
        "comfy_url": s.get("comfy_url", ""),
    }


@router.put("/services/llm")
def put_llm_config(data: LLMConfig):
    return settings_module.update_settings({"llm": data.model_dump()})


@router.put("/services/imagegen")
def put_imagegen_config(data: ImageGenConfig):
    return settings_module.update_settings({"imagegen": data.model_dump()})


@router.put("/services/comfy")
def put_comfy_config(data: ComfyConfig):
    return settings_module.update_settings({"comfy_url": data.comfy_url})


# =====================================================================
# MCP（AI 助手接入）
# =====================================================================
@router.get("/services/mcp")
def get_mcp_state():
    """MCP 接入状态（token 打码；完整值走 reveal）。"""
    from .. import mcp_server

    return mcp_server.get_state()


@router.post("/services/mcp/reveal")
def reveal_mcp_token():
    """返回完整 MCP 连接地址与 token（供前端「复制」按钮；本机 UI 场景）。"""
    from .. import mcp_server

    return {"endpoint_path": "/mcp", "token": mcp_server.reveal_token()}


class MCPConfig(BaseModel):
    enabled: bool
    regenerate: bool = False


@router.put("/services/mcp")
def put_mcp_config(data: MCPConfig):
    """启用/停用 MCP 端点；regenerate=true 时强制换新 token。"""
    from .. import mcp_server

    return mcp_server.set_enabled(data.enabled, data.regenerate)


# =====================================================================
# 连通测试
# =====================================================================
@router.post("/services/llm/test")
def test_llm():
    return llm.test_connection()


@router.post("/services/imagegen/test")
def test_imagegen():
    return imagegen.test_connection()


# =====================================================================
# LLM 能力：AI 编剧 / 提示词优化 / 台词润色
# =====================================================================
class ScriptRequest(BaseModel):
    premise: str = Field(..., min_length=2)
    genre: str = ""
    style: str = ""
    scene_count: int = Field(2, ge=1, le=12)
    shot_per_scene: int = Field(2, ge=1, le=8)


class PolishRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    shot_size: str = ""
    dialogue: str = ""


class DialoguePolishRequest(BaseModel):
    text: str = Field(..., min_length=1)


@router.post("/llm/script")
def llm_write_script(data: ScriptRequest):
    try:
        script = llm.write_script(
            data.premise, genre=data.genre, style=data.style,
            scene_count=data.scene_count, shot_per_scene=data.shot_per_scene,
        )
        return {"script": script}
    except llm.LLMError as e:
        raise HTTPException(400, str(e))


@router.post("/llm/polish")
def llm_polish_prompt(data: PolishRequest):
    try:
        return {"prompt": llm.polish_prompt(
            data.prompt, shot_size=data.shot_size, dialogue=data.dialogue)}
    except llm.LLMError as e:
        raise HTTPException(400, str(e))


@router.post("/llm/dialogue")
def llm_polish_dialogue(data: DialoguePolishRequest):
    try:
        return {"text": llm.polish_dialogue(data.text)}
    except llm.LLMError as e:
        raise HTTPException(400, str(e))


# =====================================================================
# 图像生成能力：角色参考图 / 场景概念图 / 镜头首帧
# =====================================================================
class ImageGenRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    size: str = Field("1024x1024", pattern=r"^\d+x\d+$")
    n: int = Field(1, ge=1, le=4)


@router.post("/imagegen/generate")
def imagegen_generate(data: ImageGenRequest):
    try:
        return {"urls": imagegen.generate_image(data.prompt, size=data.size, n=data.n)}
    except imagegen.ImageGenError as e:
        raise HTTPException(400, str(e))


# =====================================================================
# ComfyUI 工作流预设
# =====================================================================
@router.get("/comfy/presets")
def comfy_presets():
    return {"presets": list_presets()}


@router.get("/comfy/capabilities")
def comfy_capabilities():
    """工作流能力中心（PR-03/PR-04）：各模板/预设的能力自述 + 实时缺节点
    / 缺模型探测 + 本机 GPU 资源提示。ComfyUI 不可达时 node_probe_ok=False
    （如实报告「无法探测」，不虚构结果）。"""
    from .. import hw_probe
    from ..workflow_capabilities import (
        _iter_loader_options,
        check_missing_nodes,
        load_all_capabilities,
        missing_models_from_info,
    )

    items = load_all_capabilities()
    client = ComfyUIClient()
    online = False
    try:
        online = client.is_available(timeout=2.5)
    except Exception:
        online = False
    # 缺模型探测需要全量 object_info（各 loader 的选项清单）——所有条目共享
    # 一次拉取；失败按「无法探测」处理，不虚构清单
    full_info = None
    if online and any(
        (it.get("capability") or {}).get("required_models") for it in items
    ):
        try:
            full_info = client.object_info()
        except Exception:
            full_info = None
    info_usable = full_info is not None and bool(_iter_loader_options(full_info))
    for item in items:
        cap = item.get("capability")
        missing, probe_ok = [], False
        missing_models, models_probe_ok = [], False
        has_models = bool(cap and cap.get("required_models"))
        if cap and cap.get("required_nodes"):
            if online:
                missing, probe_ok = check_missing_nodes(client, cap["required_nodes"])
        else:
            probe_ok = True  # 无 required_nodes 声明：无需探测
        if has_models:
            if online and info_usable:
                missing_models, models_probe_ok = (
                    missing_models_from_info(full_info, cap["required_models"]), True
                )
        else:
            models_probe_ok = True
        item["missing_nodes"] = missing
        item["node_probe_ok"] = probe_ok
        item["node_probe_done"] = bool(online)
        item["missing_models"] = missing_models
        item["models_probe_ok"] = models_probe_ok
        item["models_probe_done"] = bool(online and (info_usable or not has_models))
    gpu = None
    try:
        hw = hw_probe.probe()
        gpu = {"name": hw.gpu_name or "未检测到独显",
               "vram_gb": round(hw.vram_gb or 0, 1)}
    except Exception:
        gpu = None
    return {"probe_ok": online, "gpu": gpu, "items": items}


@router.post("/comfy/presets/{preset_id}/apply")
def comfy_apply_preset(preset_id: str):
    try:
        return apply_preset(preset_id)
    except ComfyUIError as e:
        raise HTTPException(400, str(e))
