"""生成准备与持久化：在任务可执行之前固定参数、图片条件和工作流。"""
import json
import os
import re
import uuid
from pathlib import Path

from . import comfyui
from .workflow_capabilities import (
    capability_from_template, check_missing_models, check_missing_nodes, mode_supported,
)


def prepare_comfy_workflow(params: dict, *, client=None, template=None, output_dir=None) -> tuple:
    from .config import OUTPUTS_DIR
    from .generation_assets import upload_condition, upload_condition_video
    output_dir = Path(output_dir or OUTPUTS_DIR)
    client = client or comfyui.ComfyUIClient(params.get("comfy_url") or None)
    params["comfy_url"] = client.base_url
    if not client.is_available():
        raise ValueError(f"连不上 ComfyUI 服务器（{client.base_url}），请先启动并确认地址")
    template = template if template is not None else comfyui.resolve_template_for(
        params.get("engine") or "", params.get("mode") or "t2v")
    if not isinstance(template.get("prompt"), dict) or not template["prompt"]:
        raise ValueError("工作流模板为空或缺少 prompt 节点图")
    from . import h3_protocol
    native = h3_protocol.is_h3_protocol(template)
    h3_available: set = set()
    if native:
        h3_protocol.normalize_request(params)
        info = client.object_info(node_names=["FreeVideoGenerate"])
        schema = ((info or {}).get("FreeVideoGenerate") or {}).get("input") or {}
        h3_available = set(schema.get("required") or {}) | set(schema.get("optional") or {})
        # 必需集合只放「没有就跑不了」的输入。档位里新用到的 refine_steps /
        # upscale_factor / step_cache 在旧版插件里可能不存在，缺了应该降级
        # （跳过这几个输入）而不是把整个 H3 引擎判成「接口不兼容」。
        needed = {"text", "width", "height", "seconds", "seed", "base_steps", "two_pass", "frames", "preview", "refine_from"}
        if not needed.issubset(h3_available):
            raise ValueError(f"H3 节点接口不兼容，缺少输入：{'、'.join(sorted(needed - h3_available))}")
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
    if mode not in ("t2v", "v2v", "whitemodel") and not mapping.get(first_key):
        raise ValueError("当前工作流模板未映射首帧节点，无法执行图片条件模式")
    if mode == "first_last":
        if not mapping.get("last_frame_image"):
            raise ValueError("当前工作流模板未映射尾帧节点，无法执行首尾帧模式")
        if mapping[first_key] == mapping["last_frame_image"]:
            raise ValueError("首帧与尾帧不能映射到同一输入字段")
    if mode == "v2v":
        # 白模替换（视频参考）：参考视频(运动) + 角色参考图(外观) 二者缺一不可。
        # 视频参考不进入「首帧」语义——v2v 的参考视频是运动条件而非首帧图。
        if not mapping.get("reference_video"):
            raise ValueError("当前工作流模板未映射参考视频节点，无法执行视频参考（白模替换）模式")
        if not mapping.get("reference_image"):
            raise ValueError("当前工作流模板未映射参考图节点，无法执行视频参考（白模替换）模式")
    if mode == "whitemodel":
        # 白模生成：输入只有「参考视频」，产物就是灰白素模视频本身。
        # 这一步还没有最终的服装/外形，所以**不要**角色参考图——
        # 套用 v2v 的校验（必须有角色图）在语义上是错的。
        if not mapping.get("reference_video"):
            raise ValueError("当前工作流模板未映射参考视频节点，无法执行白模生成模式")

    inject = dict(params)
    if native:
        inject["seconds"] = params["num_frames"] / 24
        # 旧版插件没有这仨输入：去掉就不会往节点塞不认识的字段
        for key in ("refine_steps", "upscale_factor", "step_cache"):
            if h3_available and key not in h3_available:
                inject.pop(key, None)
    if mode in ("v2v", "whitemodel"):
        # 视频输入模式：
        #   v2v（白模替换）—— 白模视频(运动) + 角色图(外观)，缺一不可；
        #   whitemodel（白模生成）—— 只要一段原始视频，产物即灰白素模视频。
        rv = params.get("reference_video")
        if not rv:
            raise ValueError(
                "视频参考（白模替换）模式需要设置白模视频（运动参考）"
                if mode == "v2v" else
                "白模生成模式需要设置参考视频（要转成白模的原始视频）"
            )
        ri = params.get("reference_image") if mode == "v2v" else ""
        if mode == "v2v" and not ri:
            raise ValueError("视频参考（白模替换）模式需要设置角色参考图（外观参考）")
        inject["reference_video"] = rv
        if ri:
            inject["reference_image"] = ri
        # 视频走视频专用上传分支（VHS_LoadVideo / LoadVideo 读 ComfyUI input 目录）
        try:
            rv_remote = upload_condition_video(params, client, rv, "reference_video", output_dir)
        except Exception as exc:
            raise ValueError(f"白模视频上传 ComfyUI 失败: {exc}") from exc
        inject["reference_video"] = rv_remote
        if ri:
            # 角色图走图片上传分支（LoadImage / ComfyUI input 目录）
            try:
                inject["reference_image"] = upload_condition(
                    params, client, ri, "reference_image", output_dir)
            except Exception as exc:
                raise ValueError(f"角色参考图上传 ComfyUI 失败: {exc}") from exc
    elif mode != "t2v":
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
    if mode not in ("v2v", "whitemodel"):
        # 图片条件模式（i2v / first_last / continuation）上传首/尾帧参考图。
        # 多参考图（reference_image_2+）在下方单独处理；视频输入模式的
        # 参考图/视频已在上方分支上传，不在此重复（whitemodel 本就没有角色图，
        # 若这里不加排除，镜头残留的 reference_image 会被白传一遍）。
        for key in ("reference_image", "first_frame_image", "last_frame_image"):
            if inject.get(key) and mapping.get(key):
                url = inject[key]
                if url not in uploaded:
                    try:
                        uploaded[url] = upload_condition(params, client, url, key, output_dir)
                    except Exception as exc:
                        raise ValueError(f"参考图上传 ComfyUI 失败: {exc}") from exc
                inject[key] = uploaded[url]

    # ---- 多参考图（改造.md PR-08 后半）：注入模板声明的 reference_image_N 槽位 ----
    # 模板 _aivideo_map 里映射了 reference_image_2..N 即声明支持对应数量的额外
    # 参考图；给多了明确拒绝（不默默丢弃），模板不支持时同样阻断。
    extra_refs = params.get("reference_images") or "[]"
    if isinstance(extra_refs, str):
        try:
            extra_refs = json.loads(extra_refs)
        except (TypeError, ValueError):
            raise ValueError("reference_images 存档格式无效（应为 JSON 数组）")
    if extra_refs and mode == "whitemodel":
        # 白模生成不消费任何外观参考图：镜头上前序模式遗留的额外参考图
        # 不该让这一步报「模板不支持多参考图」。这里明确跳过而不是报错，
        # 因为它们对白模链路没有语义（白模的产物本来就丢掉外观）。
        extra_refs = []
    if extra_refs:
        extra_slots = [k for k in mapping if re.fullmatch(r"reference_image_\d+", k)]
        if not extra_slots:
            raise ValueError(
                "当前工作流模板不支持多参考图（未映射 reference_image_2+ 槽位）；"
                f"但提交了 {len(extra_refs)} 张额外参考图")
        usable = len(extra_slots) + 1  # 主参考图占 1 个槽
        if len(extra_refs) > len(extra_slots):
            raise ValueError(
                f"当前工作流模板最多支持 {usable} 张参考图（额外槽位 {len(extra_slots)} 个），"
                f"提交了 {len(extra_refs) + 1} 张")
        for i, url in enumerate(extra_refs, start=2):
            slot = f"reference_image_{i}"
            if not mapping.get(slot):
                break  # 槽位不连续时停止（模板只映射到 reference_image_2 之类）
            if url not in uploaded:
                try:
                    uploaded[url] = upload_condition(params, client, url, slot, output_dir)
                except Exception as exc:
                    raise ValueError(f"多参考图上传 ComfyUI 失败: {exc}") from exc
            inject[slot] = uploaded[url]

    graph = comfyui.build_shot_workflow(inject, template=template)
    if native:
        graph = h3_protocol.attach_characters(graph, params, client,
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
