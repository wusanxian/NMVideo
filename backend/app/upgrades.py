"""从预览任务生成高清候选，保留原镜头、角色和工作流来源。"""
import json
from pathlib import Path

from .comfyui import build_shot_workflow, verify_snapshot_payload
from .quality import QUALITY_PRESETS, normalize_generation


def prepare_upgrade(source: dict, preset: str = "", *, output_dir=None) -> tuple:
    if source["status"] != "done" or not source.get("preview"):
        raise ValueError("仅已完成的预览任务可以升级渲染")
    fields = (
        "engine", "prompt", "negative_prompt", "width", "height", "num_frames",
        "fps", "steps", "guidance", "seed", "shot_id", "reference_image", "mode",
        "first_frame_image", "last_frame_image", "continue_from",
        "source_video_version_id", "characters_snapshot",
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
    params = normalize_generation(params, params)

    graph, template = None, None
    if source["engine"] == "comfyui":
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
