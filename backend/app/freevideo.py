"""FreeVideo H3 的节点协议适配，与 Wan 的采样规则分开处理。

协议来源：FlashML-org/FreeVideo freevideo_engine/comfy_nodes.py（2026-10-20）。
FreeVideo 自行管理模型、内存与原生两遍采样；本模块只构建 ComfyUI 输入。
"""
import copy
import json
import math

from . import comfyui


def is_freevideo(template: dict) -> bool:
    return (template.get("_meta") or {}).get("model_id") == "vdn-minimax-h3"


def normalize_request(params: dict) -> None:
    if params.get("preview"):
        params.update(json.loads(params.get("generation_target") or "{}"))
    params["width"] = math.ceil(params["width"] / 32) * 32
    params["height"] = math.ceil(params["height"] / 32) * 32
    requested_frames = params["num_frames"] / params["fps"] * 24
    params["num_frames"] = 5 + 17 * max(2, math.ceil((requested_frames - 5) / 17))
    params["fps"] = 24
    params["steps"] = 8
    params["preview_kind"] = "native" if params.get("preview") else "rerun"
    if params.get("preview"):
        params["generation_target"] = json.dumps({key: params[key] for key in
            ("width", "height", "num_frames", "fps", "steps", "guidance")})


def attach_characters(graph: dict, params: dict, client, *, upload_image=None) -> dict:
    """将冻结角色卡里的参考图与 LoRA 接入官方 Reference / LoRA 节点。"""
    graph = copy.deepcopy(graph)
    upload_image = upload_image or (lambda url: comfyui.upload_reference_image(client, url))
    generators = [key for key, node in graph.items() if node.get("class_type") == "FreeVideoGenerate"]
    if len(generators) != 1:
        raise ValueError("FreeVideo 预设需要且只能包含一个 FreeVideoGenerate 输出节点")
    characters = json.loads(params.get("characters_snapshot") or "[]")
    images = []
    loras = []
    for character in characters:
        for value in ((character.get("reference_images") or []) if params.get("use_character_images", True) else []):
            url = value.get("url", "") if isinstance(value, dict) else value
            if url and url not in images:
                images.append(url)
        if params.get("use_character_lora", True) and character.get("lora") and character["lora"] not in loras:
            loras.append(character["lora"])
    if len(images) > 32:
        raise ValueError("FreeVideo 最多支持 32 个参考输入，请减少角色参考图")
    target = graph[generators[0]]["inputs"]
    if images and (target.get("first") or target.get("last")) and not target.get("continue_from"):
        raise ValueError("FreeVideo 不支持首尾帧与角色参考图混用，请关闭本次角色参考图或改用文生视频参考模式；角色 LoRA 可继续使用")
    required = (["LoadImage", "FreeVideoReference"] if images else []) + (["FreeVideoLoRA"] if loras else [])
    if required:
        from .workflow_capabilities import check_missing_nodes, check_missing_models
        missing, ok = check_missing_nodes(client, required)
        if not ok or missing:
            raise ValueError(f"无法确认角色条件节点可用：{'、'.join(missing or required)}")
        missing, ok = check_missing_models(client, loras)
        if loras and (not ok or missing):
            raise ValueError(f"无法确认角色 LoRA 已安装：{'、'.join(missing or loras)}")

    def add_node(kind, inputs):
        index = 1
        while str(index) in graph:
            index += 1
        key = str(index)
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    reference = target.get("references")
    for url in images:
        image = add_node("LoadImage", {"image": upload_image(url)})
        inputs = {"image": image}
        if reference:
            inputs["previous"] = reference
        reference = add_node("FreeVideoReference", inputs)
    if images:
        target["references"] = reference
    adapter = target.get("loras")
    for name in loras:
        inputs = {"lora": name, "strength": 1.0}
        if adapter:
            inputs["previous"] = adapter
        adapter = add_node("FreeVideoLoRA", inputs)
    if loras:
        target["loras"] = adapter
    return graph
