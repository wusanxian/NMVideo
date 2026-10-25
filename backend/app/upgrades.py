"""从预览任务生成高清候选，保留原镜头、角色和工作流来源。"""
import json
from pathlib import Path

from .comfyui import build_shot_workflow, verify_snapshot_payload
from .engines import is_comfy_engine
from .quality import QUALITY_PRESETS, normalize_generation


def prepare_upgrade(source: dict, preset: str = "", *, output_dir=None) -> tuple:
    if source["status"] != "done" or not source.get("preview"):
        raise ValueError("仅已完成的预览任务可以升级渲染")
    fields = (
        "engine", "prompt", "negative_prompt", "width", "height", "num_frames",
        "fps", "steps", "guidance", "seed", "shot_id", "reference_image", "mode",
        "first_frame_image", "last_frame_image", "continue_from",
        "source_video_version_id", "characters_snapshot",
        "reference_images",  # PR-08 后半：升级保留多参考快照（JSON 字符串）
        "generation_inputs", "comfy_url",
        "use_character_images", "use_character_lora",
    )
    params = {key: source[key] for key in fields if source.get(key) is not None}
    selected = preset or source.get("preset") or ""
    if selected and selected not in QUALITY_PRESETS:
        raise ValueError(f"未知档位: {selected}")
    try:
        target = json.loads(source.get("generation_target") or "{}")
    except (ValueError, TypeError) as exc:
        raise ValueError("原预览的目标参数损坏，请先检查任务记录") from exc
    if not isinstance(target, dict):
        raise ValueError("原预览的目标参数无效")
    numeric_fields = ("width", "height", "num_frames", "fps", "steps", "guidance")
    if target and (not preset or (source.get("preview_kind") == "native" and preset == source.get("preset"))):
        params.update({key: target[key] for key in numeric_fields if key in target})
    elif selected:
        params.update({key: QUALITY_PRESETS[selected][key] for key in numeric_fields})
    else:
        raise ValueError("旧预览未保存目标参数，请选择升级质量档位")
    params.update(preview=False, preset=selected, parent_job_id=source["id"])
    # PR-N08：AnimeDiff 引擎护栏软化——预览可升级到引擎上限内的最大参数
    #（32 帧 / 640×640 面积），而不是整条升级链路 400 死路。
    # 其他引擎保持严格校验（normalize_generation 统一拦）。
    if params.get("engine") == "anime":
        clamped = []
        frames = int(params["num_frames"])
        if frames > 32:
            clamped.append(f"帧数 {frames}→32")
            params["num_frames"] = 32
        area = params["width"] * params["height"]
        if area > 640 * 640:
            scale = (640 * 640 / area) ** 0.5
            new_w = max(256, int(params["width"] * scale) // 8 * 8)
            new_h = max(256, int(params["height"] * scale) // 8 * 8)
            while new_w * new_h > 640 * 640 and new_h > 256:
                new_h -= 8
            clamped.append(
                f"分辨率 {params['width']}×{params['height']}→{new_w}×{new_h}")
            params["width"], params["height"] = new_w, new_h
        if clamped:
            params["upgrade_clamped"] = "；".join(clamped)
    # 兼容旧数据：h3 在「归位 ComfyUI 通道」之前是原生推理，那批预览没有工作流
    # 快照。升级时退回原生兜底引擎（h3_native），别让历史预览彻底升不上去。
    # 有快照的 h3（本批之后的 ComfyUI 通道）正常走上面的快照复用。
    if (params.get("engine") == "h3"
            and not Path(source.get("workflow_snapshot") or "").is_file()):
        params["engine"] = "h3_native"
    params = normalize_generation(params, params)

    graph, template = None, None
    # 只有 ComfyUI 通道有工作流快照可复用；兜底通道按原参数重新生成。
    # 用 params["engine"] 而不是 source["engine"]：上面可能刚把旧 h3 预览
    # 降级成 h3_native（旧数据没有快照），这里必须跟着走兜底分支。
    if is_comfy_engine(params.get("engine") or source["engine"]):
        path = Path(source.get("workflow_snapshot") or "")
        if not path.is_file():
            raise ValueError("原预览的工作流快照缺失，无法保留原输入；请恢复快照")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("原预览的工作流快照不可读") from exc
        frozen_graph = verify_snapshot_payload(payload, path)
        from .comfyui import ComfyUIClient
        params["comfy_url"] = ComfyUIClient(
            params.get("comfy_url") or payload.get("comfy_url") or None
        ).base_url
        original = payload.get("template")
        if not isinstance(original, dict) or not original.get("_aivideo_map"):
            raise ValueError("旧快照未保存参数映射，无法可靠升级；请使用新版重新生成预览")
        template = {**original, "prompt": frozen_graph}
        from .config import OUTPUTS_DIR
        from .generation_assets import restore_conditions
        restore_conditions(params, frozen_graph, template, output_dir or OUTPUTS_DIR)
        # 仅更新采样参数；沿用原快照中已上传的图片引用、LoRA 和其他条件。
        inject = {key: params[key] for key in numeric_fields}
        inject["seed"] = params["seed"]
        if source.get("preview_kind") == "native":
            if preset and preset != source.get("preset"):
                raise ValueError("原生二采沿用预览自己的目标尺寸；改变档位请新建生成任务")
            mapping = template["_aivideo_map"]
            if not all(key in mapping for key in ("preview", "refine_from")):
                raise ValueError("原预览缺少原生二采映射")
            output = json.loads(source.get("native_output") or "{}")
            if not output.get("filename") or output.get("type", "output") != "output":
                raise ValueError("原预览缺少 ComfyUI 输出引用，无法继续二采")
            inject["preview"] = False
            inject["refine_from"] = "/".join(filter(None, (output.get("subfolder"), output["filename"])))
        unmapped = [key for key in numeric_fields
                    if params[key] != source[key] and key not in template["_aivideo_map"]]
        if unmapped:
            raise ValueError(f"原工作流未映射升级参数：{'、'.join(unmapped)}，无法可靠升级")
        graph = build_shot_workflow(inject, template=template)
    return params, graph, template
