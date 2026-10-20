"""质量档位预设（借鉴 FreeVideo 四档设计）

档位映射到具体生成参数。两遍采样：预览固定走「轻量」档，
满意后用同 seed 升级到目标档位（画廊「升级渲染」按钮）。
"""
import json

QUALITY_PRESETS = {
    "lite": {
        "name": "轻量",
        "desc": "480p 快速预览，试构图/提示词",
        "width": 832, "height": 480, "num_frames": 33, "fps": 16,
        "steps": 20, "guidance": 5.0,
    },
    "standard": {
        "name": "标准",
        "desc": "480p 完整片段，日常够用",
        "width": 832, "height": 480, "num_frames": 81, "fps": 24,
        "steps": 30, "guidance": 5.0,
    },
    "fine": {
        "name": "精细",
        "desc": "720p 高质量，耗时显著增加",
        "width": 1280, "height": 720, "num_frames": 81, "fps": 24,
        "steps": 40, "guidance": 5.0,
    },
    "ultra": {
        "name": "极致",
        "desc": "720p 长片段，8GB 卡上以小时计",
        "width": 1280, "height": 720, "num_frames": 121, "fps": 24,
        "steps": 50, "guidance": 5.0,
    },
}

# 预览档：轻量的再减半（分辨率 416x256、帧数 17、步数 12）
# 注意：height 不能低于 API 校验下限 256（AnimeDiff 也会吸附到 256）
PREVIEW_OVERRIDE = {"width": 416, "height": 256, "num_frames": 17, "steps": 12}


def apply_preset(job_params: dict, preset_id: str) -> dict:
    """把档位参数合并进任务参数。

    优先级：任务显式传入 > 档位 > 引擎默认。
    调用方负责先剥离「仅为默认值」的字段（model_dump(exclude_unset=True)）。
    """
    preset = QUALITY_PRESETS.get(preset_id)
    if not preset:
        return job_params
    merged = dict(job_params)
    merged.update({k: v for k, v in preset.items() if k not in ("name", "desc")})
    return merged


def list_presets() -> list:
    return [{"id": k, **v} for k, v in QUALITY_PRESETS.items()]


def normalize_generation(defaults: dict, explicit: dict) -> dict:
    """两个生成入口共用：显式值 > 档位 > 默认值，预览最后覆盖。

    在提交前执行引擎的尺寸约束，使存档参数与实际执行一致。
    """
    params = dict(defaults)
    preset = params.get("preset") or ""
    if preset:
        if preset not in QUALITY_PRESETS:
            raise ValueError(f"未知档位: {preset}")
        params = apply_preset(params, preset)
    params.update(explicit)
    if params.get("preview"):
        target_params = {**params, "preview": False}
        target = normalize_generation(target_params, target_params)
        params["generation_target"] = json.dumps({
            key: target[key] for key in ("width", "height", "num_frames", "fps", "steps", "guidance")
        })
        params.update(PREVIEW_OVERRIDE)
    engine = params.get("engine")
    if engine == "anime":
        if params["num_frames"] > 32 or params["width"] * params["height"] > 640 * 640:
            raise ValueError("AnimeDiff 建议不超过 32 帧、640×640 像素；请降低参数或改用 Wan/ComfyUI")
        params["num_frames"] = max(8, params["num_frames"] // 2 * 2)
        for key in ("width", "height"):
            params[key] = max(256, params[key] // 8 * 8)
    elif engine == "wan22":
        params["num_frames"] = 4 * ((params["num_frames"] - 1) // 4) + 1
        for key in ("width", "height"):
            params[key] = max(128, params[key] // 16 * 16)
    return params
