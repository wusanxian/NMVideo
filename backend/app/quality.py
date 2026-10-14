"""质量档位预设（借鉴 FreeVideo 四档设计）

档位映射到具体生成参数。两遍采样：预览固定走「轻量」档，
满意后用同 seed 升级到目标档位（画廊「升级渲染」按钮）。
"""

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
