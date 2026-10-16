"""镜头质量审核检查（第二批）

审核前的自动体检：对分镜的创作完整度做规则检查，输出
[{level: "error"|"warn"|"info", code, message}]，供审核弹窗展示，
error 级问题标记「不建议通过」。

注意：本模块只管「镜头质量检查」；生成参数档位预设在 quality.py。
"""
from . import assets
from . import tracks

MODE_LABELS = {
    "t2v": "文生视频",
    "i2v": "图生视频",
    "first_last": "首尾帧",
    "continuation": "视频续接",
}


def check_shot(shot: dict, characters: list = None, voices: list = None) -> list:
    """对单个分镜做质量检查"""
    if characters is None:
        characters = assets.characters_of_shot(shot)
    if voices is None:
        voices = tracks.list_voices_of_shot(shot["id"])
    issues: list = []

    dur = float(shot.get("duration_s") or 0)
    if dur < 1.0:
        issues.append({"level": "warn", "code": "short",
                       "message": f"时长仅 {dur}s，短于 1 秒，观众可能看不清画面"})
    elif dur > 12.0:
        issues.append({"level": "warn", "code": "long",
                       "message": f"时长 {dur}s 偏长，注意观看疲劳与生成耗时"})

    prompt = (shot.get("prompt") or "").strip()
    if len(prompt) < 8:
        issues.append({"level": "error", "code": "prompt_thin",
                       "message": "提示词过短（少于 8 字），画面不可控"})

    # 对白与配音一致性
    dialogue = (shot.get("dialogue") or "").strip()
    if dialogue and not voices:
        issues.append({"level": "warn", "code": "no_voice",
                       "message": "有对白但未生成配音，导出成品将没有声音"})
    if voices and not dialogue:
        issues.append({"level": "info", "code": "voice_no_dialogue",
                       "message": "有配音但对白文本为空（可能是旁白）"})

    # 角色一致性
    if not characters:
        issues.append({"level": "info", "code": "no_character",
                       "message": "未关联角色卡，跨镜头人物外观可能不一致"})
    else:
        missing_ref = [c["name"] for c in characters if not c.get("reference_images")]
        if missing_ref:
            issues.append({"level": "warn", "code": "no_ref_image",
                           "message": f"角色 {'、'.join(missing_ref)} 没有参考图，"
                                      "外观一致性主要靠文字描述维持"})

    # 版本与参数记录
    versions = shot.get("versions") or []
    if not versions:
        issues.append({"level": "error", "code": "no_version",
                       "message": "没有任何生成版本，无法审核"})
    elif not shot.get("current_version"):
        issues.append({"level": "error", "code": "no_current",
                       "message": "未选择采用版本"})

    # 参考图模式提示
    mode = shot.get("mode") or "t2v"
    if mode in ("i2v", "first_last", "continuation") and not (shot.get("reference_image") or "").strip():
        issues.append({"level": "error", "code": "no_ref_for_mode",
                       "message": f"当前模式（{MODE_LABELS.get(mode, mode)}）需要参考图/首帧，尚未设置"})

    return issues


def quality_score(issues: list) -> dict:
    """汇总：{"level": "ok"|"warn"|"error", "error": n, "warn": n, "info": n}"""
    n_err = sum(1 for i in issues if i["level"] == "error")
    n_warn = sum(1 for i in issues if i["level"] == "warn")
    n_info = sum(1 for i in issues if i["level"] == "info")
    level = "error" if n_err else ("warn" if n_warn else "ok")
    return {"level": level, "error": n_err, "warn": n_warn, "info": n_info}
