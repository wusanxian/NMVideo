"""将实际消费的图片固化到内容寻址副本，记录生成来源。"""
import hashlib
import json
from pathlib import Path

from . import comfyui
from .managed_paths import IMAGE_SUFFIXES, resolve_managed_asset


def upload_condition(params: dict, client, url: str, role: str, output_dir: Path) -> str:
    source = resolve_managed_asset(url, allowed_types=IMAGE_SUFFIXES, base_dir=output_dir)
    record = {"source_url": url, "role": role}
    upload_url = url
    if source and source.is_file():
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        frozen = Path(output_dir) / "assets" / f"{digest}{source.suffix.lower()}"
        frozen.parent.mkdir(parents=True, exist_ok=True)
        # 同一内容只写一次；校验现有副本，避免复用已损坏的资产。
        if not frozen.exists() or hashlib.sha256(frozen.read_bytes()).hexdigest() != digest:
            frozen.write_bytes(content)
        upload_url = f"/outputs/assets/{frozen.name}"
        record.update(sha256=digest, frozen_url=upload_url)
    remote = comfyui.upload_reference_image(client, upload_url)
    record["comfy_input"] = remote
    manifest = json.loads(params.get("generation_inputs") or "[]")
    manifest.append(record)
    params["generation_inputs"] = json.dumps(manifest, ensure_ascii=False)
    return remote


def restore_conditions(params: dict, graph: dict, template: dict, output_dir: Path) -> None:
    """升级时重新上传冻结副本，避免依赖服务端尚未清理的 input 文件。"""
    records = json.loads(params.get("generation_inputs") or "[]")
    client = comfyui.ComfyUIClient(params.get("comfy_url") or None)
    for record in records:
        if not record.get("sha256"):
            continue  # 兼容没有内容指纹的旧任务；仍沿用原节点图。
        frozen = resolve_managed_asset(record.get("frozen_url") or "", allowed_types=IMAGE_SUFFIXES,
                                       base_dir=output_dir)
        if not frozen or not frozen.is_file() or hashlib.sha256(frozen.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("原预览的冻结图片缺失或内容已改变，无法可靠升级")
        old = record["comfy_input"]
        remote = comfyui.upload_reference_image(client, record["frozen_url"])
        for node in graph.values():
            if node.get("class_type") == "LoadImage" and node.get("inputs", {}).get("image") == old:
                node["inputs"]["image"] = remote
        for role in ("reference_image", "first_frame_image", "last_frame_image"):
            target = template.get("_aivideo_map", {}).get(role) or {}
            inputs = graph.get(str(target.get("node")), {}).get("inputs", {})
            if inputs.get(target.get("input")) == old:
                inputs[target["input"]] = remote
        record["comfy_input"] = remote
    params["generation_inputs"] = json.dumps(records, ensure_ascii=False)
