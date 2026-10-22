"""生成准备与持久化：在任务可执行之前固定参数、图片条件和工作流。"""
import json
import os
import uuid
from pathlib import Path

from . import comfyui
from .workflow_capabilities import (
    capability_from_template, check_missing_models, check_missing_nodes, mode_supported,
)


def prepare_comfy_workflow(params: dict, *, client=None, template=None, output_dir=None) -> tuple:
    from .config import OUTPUTS_DIR
    from .generation_assets import upload_condition
    output_dir = Path(output_dir or OUTPUTS_DIR)
    client = client or comfyui.ComfyUIClient(params.get("comfy_url") or None)
    params["comfy_url"] = client.base_url
    if not client.is_available():
        raise ValueError(f"连不上 ComfyUI 服务器（{client.base_url}），请先启动并确认地址")
    template = template if template is not None else comfyui.resolve_template_for(
        params.get("engine") or "", params.get("mode") or "t2v")
    if not isinstance(template.get("prompt"), dict) or not template["prompt"]:
        raise ValueError("工作流模板为空或缺少 prompt 节点图")
    from . import freevideo
    native = freevideo.is_freevideo(template)
    if native:
        freevideo.normalize_request(params)
        info = client.object_info(node_names=["FreeVideoGenerate"])
        schema = ((info or {}).get("FreeVideoGenerate") or {}).get("input") or {}
        available = set(schema.get("required") or {}) | set(schema.get("optional") or {})
        needed = {"text", "width", "height", "seconds", "seed", "base_steps", "two_pass", "frames", "preview", "refine_from"}
        if not needed.issubset(available):
            raise ValueError(f"FreeVideo 节点接口不兼容，缺少输入：{'、'.join(sorted(needed - available))}")
    mode = params.get("mode") or "t2v"
    cap = capability_from_template(template)
    if cap:
        if not mode_supported(cap, mode):
            raise ValueError(f"当前工作流不支持「{mode}」模式；声明支持：{'、'.join(cap['supports'])}")
        missing, _ = check_missing_nodes(client, cap["required_nodes"])
        if missing:
            raise ValueError(f"当前工作流缺少 ComfyUI 节点：{'、'.join(missing)}")
        missing, _ = check_missing_models(client, cap["required_models"])
        if missing:
            raise ValueError(f"当前工作流需要以下模型但未安装：{'、'.join(missing)}")

    mapping = template.get("_aivideo_map") or {}
    for key, target in mapping.items():
        if not isinstance(target, dict) or str(target.get("node")) not in template["prompt"] or not target.get("input"):
            raise ValueError(f"工作流参数映射无效：{key}，请检查节点与输入字段")
    first_key = "first_frame_image" if mapping.get("first_frame_image") else "reference_image"
    if mode != "t2v" and not mapping.get(first_key):
        raise ValueError("当前工作流模板未映射首帧节点，无法执行图片条件模式")
    if mode == "first_last":
        if not mapping.get("last_frame_image"):
            raise ValueError("当前工作流模板未映射尾帧节点，无法执行首尾帧模式")
        if mapping[first_key] == mapping["last_frame_image"]:
            raise ValueError("首帧与尾帧不能映射到同一输入字段")

    inject = dict(params)
    if native:
        inject["seconds"] = params["num_frames"] / 24
    if mode != "t2v":
        first = params.get("first_frame_image") or params.get("reference_image")
        if not first:
            raise ValueError("图片条件模式需要设置参考图/首帧图片")
        inject[first_key] = first
        # 两个首帧别名只有被映射时才注入，始终使用同一张图。
        for key in ("reference_image", "first_frame_image"):
            if mapping.get(key):
                inject[key] = first
        if mode == "first_last" and not params.get("last_frame_image"):
            raise ValueError("首尾帧模式需要同时设置尾帧图片")
    uploaded = {}
    for key in ("reference_image", "first_frame_image", "last_frame_image"):
        if inject.get(key) and mapping.get(key):
            url = inject[key]
            if url not in uploaded:
                try:
                    uploaded[url] = upload_condition(params, client, url, key, output_dir)
                except Exception as exc:
                    raise ValueError(f"参考图上传 ComfyUI 失败: {exc}") from exc
            inject[key] = uploaded[url]
    graph = comfyui.build_shot_workflow(inject, template=template)
    if native:
        graph = freevideo.attach_characters(graph, params, client,
            upload_image=lambda url: upload_condition(params, client, url, "character_reference", output_dir))
    return graph, template


def persist_prepared_job(database, params: dict, output_dir: Path, *, graph=None, template=None) -> dict:
    """原子写快照后单事务建任务；文件失败不留下可恢复的 pending 任务。"""
    if graph is None:
        return database.create_job(params)
    job_id = uuid.uuid4().hex
    snapshot = Path(output_dir) / "workflows" / f"{job_id}.json"
    temporary = snapshot.with_suffix(".tmp")
    payload = comfyui.build_snapshot_payload(graph, job_id, template=template)
    payload["comfy_url"] = params.get("comfy_url") or ""
    try:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(snapshot)
        return database.create_job({**params, "workflow_snapshot": str(snapshot)}, job_id=job_id)
    except BaseException:
        temporary.unlink(missing_ok=True)
        snapshot.unlink(missing_ok=True)
        raise
