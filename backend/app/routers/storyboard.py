"""分镜工作台 REST API：项目 / 场景 / 分镜 / 版本 / 时间线 / 导出 / ComfyUI"""
import random
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import storyboard as sb
from ..comfyui import (
    ComfyUIClient,
    ComfyUIError,
    DEFAULT_WORKFLOW,
    load_workflow_template,
    save_workflow_template,
)
from ..config import OUTPUTS_DIR
from ..engines import ENGINES
from ..export import start_export
from ..queue_manager import job_queue

router = APIRouter(prefix="/api", tags=["storyboard"])


# =====================================================================
# 模型
# =====================================================================
class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


class ScriptImport(BaseModel):
    text: str = Field(..., min_length=1)
    # rebuild=覆盖重建（事务化，失败整体回滚）；append=保留已有场景追加；
    # preview=只做差异预览不写库
    mode: str = Field("rebuild", pattern="^(rebuild|append|preview)$")


class SceneCreate(BaseModel):
    title: str = "新场景"
    description: str = ""
    environment: str = ""
    lighting: str = ""


class SceneUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    environment: Optional[str] = None
    lighting: Optional[str] = None


class ShotCreate(BaseModel):
    title: str = ""
    dialogue: str = ""
    shot_size: str = "中景"
    camera_movement: str = "固定"
    duration_s: float = Field(3.0, ge=0.5, le=60)
    prompt: str = ""
    negative_prompt: str = ""
    reference_image: str = ""


class ShotUpdate(BaseModel):
    title: Optional[str] = None
    dialogue: Optional[str] = None
    shot_size: Optional[str] = None
    camera_movement: Optional[str] = None
    duration_s: Optional[float] = Field(None, ge=0.5, le=60)
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    reference_image: Optional[str] = None


class ShotGenerate(BaseModel):
    engine: str = "anime"
    width: int = Field(512, ge=256, le=1344)
    height: int = Field(512, ge=256, le=768)
    num_frames: int = Field(16, ge=8, le=121)
    fps: int = Field(8, ge=4, le=30)
    steps: int = Field(25, ge=5, le=100)
    guidance: float = Field(7.5, ge=1.0, le=20.0)
    seed: int = -1
    preset: str = ""
    preview: bool = False
    mode: str = Field("t2v", pattern="^(t2v|i2v|first_last|continuation)$")
    continue_from: str = ""


class ShotReview(BaseModel):
    action: str = Field(..., pattern="^(approve|rework)$")
    version_id: str = ""
    comment: str = ""


class ReorderBody(BaseModel):
    ids: list


class TimelineUpdate(BaseModel):
    trim_in_s: Optional[float] = Field(None, ge=0)
    trim_out_s: Optional[float] = Field(None, ge=0)
    transition: Optional[str] = Field(None, pattern="^(none|fade)$")


class WorkflowUpdate(BaseModel):
    prompt: dict
    _aivideo_map: Optional[dict] = None


# =====================================================================
# Project
# =====================================================================
@router.get("/projects")
def list_projects():
    return sb.list_projects()


@router.post("/projects", status_code=201)
def create_project(data: ProjectCreate):
    return sb.create_project(data.name, data.description)


@router.get("/projects/{project_id}")
def get_project(project_id: str):
    p = sb.get_project(project_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    p["scenes"] = sb.list_scenes(project_id)
    return p


@router.patch("/projects/{project_id}")
def update_project(project_id: str, data: ProjectUpdate):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return sb.update_project(
        project_id, **{k: v for k, v in data.model_dump(exclude_unset=True).items()}
    )


@router.delete("/projects/{project_id}")
def delete_project(project_id: str):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    sb.delete_project(project_id)
    return {"ok": True}


# =====================================================================
# 剧本
# =====================================================================
@router.get("/projects/{project_id}/script")
def get_script(project_id: str):
    p = sb.get_project(project_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    return {"script": p["script"]}


@router.put("/projects/{project_id}/script")
def put_script(project_id: str, data: ScriptImport):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    sb.update_project(project_id, script=data.text)
    return {"ok": True}


@router.post("/projects/{project_id}/script/import")
def import_script(project_id: str, data: ScriptImport):
    """AI 拆分：解析剧本文本 → 重建/追加场景与分镜。

    - mode=preview：返回差异预览（现有数量 vs 新解析数量、标题撞车列表），不写库
    - mode=append：标题相同的已有场景跳过，保留其镜头制作成果
    - mode=rebuild：覆盖重建（单个事务，失败整体回滚）
    """
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    if data.mode == "preview":
        return sb.script_preview(project_id, data.text)
    try:
        return sb.import_script(project_id, data.text, mode=data.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))


# =====================================================================
# Scene
# =====================================================================
@router.post("/projects/{project_id}/scenes", status_code=201)
def create_scene(project_id: str, data: SceneCreate):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return sb.create_scene(
        project_id, data.title, data.description, data.environment, data.lighting
    )


@router.get("/projects/{project_id}/scenes")
def list_scenes(project_id: str):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return sb.list_scenes(project_id)


@router.patch("/scenes/{scene_id}")
def update_scene(scene_id: str, data: SceneUpdate):
    sc = sb.get_scene(scene_id)
    if not sc:
        raise HTTPException(404, "场景不存在")
    return sb.update_scene(
        scene_id, **{k: v for k, v in data.model_dump(exclude_unset=True).items()}
    )


@router.delete("/scenes/{scene_id}")
def delete_scene(scene_id: str):
    if not sb.get_scene(scene_id):
        raise HTTPException(404, "场景不存在")
    sb.delete_scene(scene_id)
    return {"ok": True}


@router.post("/scenes/{scene_id}/shots/reorder")
def reorder_shots(scene_id: str, data: ReorderBody):
    sb.reorder_shots(scene_id, data.ids)
    return {"ok": True}


# =====================================================================
# Shot
# =====================================================================
@router.post("/scenes/{scene_id}/shots", status_code=201)
def create_shot(scene_id: str, data: ShotCreate):
    if not sb.get_scene(scene_id):
        raise HTTPException(404, "场景不存在")
    return sb.create_shot(scene_id, **data.model_dump())


@router.get("/shots/{shot_id}")
def get_shot(shot_id: str):
    shot = sb.get_shot(shot_id)
    if not shot:
        raise HTTPException(404, "分镜不存在")
    return shot


@router.patch("/shots/{shot_id}")
def update_shot(shot_id: str, data: ShotUpdate):
    if not sb.get_shot(shot_id):
        raise HTTPException(404, "分镜不存在")
    fields = {k: v for k, v in data.model_dump(exclude_unset=True).items()}
    shot = sb.update_shot(shot_id, **fields)
    # 改提示词等创作参数后，回到草稿态重新生成（已通过的不动）
    if shot and shot["status"] == "rework":
        sb.set_shot_status(shot_id, "draft")
        shot = sb.get_shot(shot_id)
    return shot


@router.delete("/shots/{shot_id}")
def delete_shot(shot_id: str):
    if not sb.get_shot(shot_id):
        raise HTTPException(404, "分镜不存在")
    sb.delete_shot(shot_id)
    return {"ok": True}


@router.post("/shots/{shot_id}/generate", status_code=201)
async def generate_shot(shot_id: str, data: ShotGenerate):
    """分镜发起生成：走统一任务队列（本地引擎或 ComfyUI）

    增强（第二批）：
    - 角色卡绑定：自动把角色外貌/服装拼进提示词，保证跨镜头人物一致
    - 视频续接：自动抽取上一镜头末帧作为首帧参考
    """
    from .. import assets
    from ..video import extract_last_frame

    shot = sb.get_shot(shot_id)
    if not shot:
        raise HTTPException(404, "分镜不存在")
    if shot["status"] in ("queued", "generating"):
        raise HTTPException(409, "该分镜正在生成中，请稍候")
    if not shot["prompt"].strip():
        raise HTTPException(400, "请先填写分镜提示词")
    if data.engine not in ENGINES:
        raise HTTPException(400, f"未知引擎: {data.engine}")

    # ---- 参考图 / 续接处理 ----
    reference_image = (shot.get("reference_image") or "").strip()
    continue_from = (shot.get("continue_from") or "").strip()
    mode = data.mode or shot.get("mode") or "t2v"

    if mode == "continuation":
        # 续接：取 continue_from 镜头（默认上一镜头）的末帧做首帧
        src_shot = None
        if continue_from and sb.get_shot(continue_from):
            src_shot = sb.get_shot(continue_from)
        else:
            scene = sb.get_scene(shot["scene_id"])
            siblings = sb.list_shots(scene["id"]) if scene else []
            prev = [s for s in siblings if s["order_index"] < shot["order_index"]]
            if prev:
                src_shot = prev[-1]
        if not src_shot or not src_shot.get("current_version_id"):
            raise HTTPException(
                400, "续接模式需要上一镜头已有生成版本（作为首帧），请先完成上一镜头"
            )
        src_ver = sb.get_version(src_shot["current_version_id"])
        if not src_ver or not src_ver.get("video_url"):
            raise HTTPException(400, "上一镜头的当前版本没有视频文件")
        src_path = OUTPUTS_DIR / src_ver["video_url"][len("/outputs/"):]
        if not src_path.exists():
            raise HTTPException(400, f"上一镜头视频缺失: {src_path}")
        refs_dir = OUTPUTS_DIR / "refs"
        refs_dir.mkdir(parents=True, exist_ok=True)
        frame_path = refs_dir / f"{shot_id}_from_{src_shot['id']}.png"
        try:
            extract_last_frame(src_path, frame_path)
        except Exception as e:
            raise HTTPException(400, f"抽取上一镜头末帧失败: {e}")
        reference_image = f"/outputs/refs/{frame_path.name}"
        sb.update_shot(shot_id, mode="continuation",
                       continue_from=src_shot["id"], reference_image=reference_image)
    elif mode in ("i2v", "first_last"):
        if not reference_image:
            raise HTTPException(400, f"模式「{mode}」需要先设置参考图/首帧图片")

    # ---- 提示词：角色一致性拼装 ----
    prompt = shot["prompt"]
    characters = assets.characters_of_shot(shot)
    if characters:
        prompt = assets.compose_prompt_with_characters(shot, characters)
    if mode == "continuation":
        prompt = (prompt + " || continuous shot, seamless continuation, "
                  "same character and scene as the previous shot").strip(" |")

    if data.engine == "comfyui":
        client = ComfyUIClient()
        if not client.is_available():
            raise HTTPException(
                400,
                f"连不上 ComfyUI 服务器（{client.base_url}）：请先在 ComfyUI 设置里"
                "确认地址并启动 ComfyUI",
            )

    if data.seed < 0:
        data.seed = random.randint(0, 2**31 - 1)

    params = data.model_dump()
    params["prompt"] = prompt
    params["negative_prompt"] = shot.get("negative_prompt") or ""
    params["shot_id"] = shot_id
    params["reference_image"] = reference_image
    params["mode"] = mode
    params["continue_from"] = continue_from

    job_id = await job_queue.submit(params)
    sb.set_shot_status(shot_id, "queued", job_id=job_id)
    from .. import db as db_module

    return db_module.db.get_job(job_id)


@router.post("/shots/{shot_id}/review")
def review_shot(shot_id: str, data: ShotReview):
    """审核：approve=通过 / rework=打回重做（带质量检查结果与意见留痕）

    质检前置：先跑 check_shot，存在 error 级阻断性问题时**拒绝通过**
    （HTTP 422 带完整检查结果），镜头状态保持不变、阻断记录写入审核历史。
    """
    from ..shotcheck import check_shot, quality_score

    shot = sb.get_shot(shot_id)
    if not shot:
        raise HTTPException(404, "分镜不存在")
    scene = sb.get_scene(shot["scene_id"])
    project_id = scene["project_id"] if scene else ""
    from_status = shot["status"]

    if data.action == "approve":
        # 版本归属校验：不能拿别的镜头的版本来通过审核
        if data.version_id:
            version = sb.get_version(data.version_id)
            if not version:
                raise HTTPException(404, "版本不存在")
            if version["shot_id"] != shot_id:
                raise HTTPException(400, "版本不属于该镜头，无法采用")
            sb.adopt_version(shot_id, data.version_id)
        elif not shot["current_version_id"]:
            raise HTTPException(400, "还没有生成任何版本，无法审核通过")

        # ---- 质检前置：基于采用后的最新镜头状态做检查 ----
        fresh = sb.get_shot(shot_id)
        issues = check_shot(fresh)
        blocking = [i for i in issues if i["level"] == "error"]
        if blocking:
            sb.log_review(
                shot_id, project_id, action="approve_blocked",
                from_status=from_status, to_status=from_status,
                version_id=data.version_id or shot.get("current_version_id") or "",
                comment=data.comment or "存在阻断性问题，审核未通过",
                checks=issues,
            )
            raise HTTPException(422, detail={
                "message": "存在阻断性问题，不能通过审核；请先解决后再试",
                "issues": issues,
                "score": quality_score(issues),
            })

        to_status = "approved"
        sb.set_shot_status(shot_id, "approved")
    else:
        # 打回重做：也做一次检查（结果仅作留痕，不阻断）
        issues = check_shot(shot)
        to_status = "rework"
        sb.set_shot_status(shot_id, "rework")

    sb.log_review(
        shot_id, project_id, action=data.action,
        from_status=from_status, to_status=to_status,
        version_id=data.version_id or shot.get("current_version_id") or "",
        comment=data.comment, checks=issues,
    )
    result = sb.get_shot(shot_id)
    result["quality"] = {"issues": issues, "score": quality_score(issues)}
    return result


@router.get("/shots/{shot_id}/versions")
def list_versions(shot_id: str):
    if not sb.get_shot(shot_id):
        raise HTTPException(404, "分镜不存在")
    return sb.list_versions(shot_id)


@router.post("/shots/{shot_id}/versions/{version_id}/adopt")
def adopt_version(shot_id: str, version_id: str):
    version = sb.get_version(version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    if version["shot_id"] != shot_id:
        raise HTTPException(400, "版本不属于该镜头，无法采用")
    sb.adopt_version(shot_id, version_id)
    return sb.get_shot(shot_id)


@router.delete("/shots/{shot_id}/versions/{version_id}")
def delete_version(shot_id: str, version_id: str):
    version = sb.get_version(version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    if version["shot_id"] != shot_id:
        raise HTTPException(400, "版本不属于该镜头，无法删除")
    sb.delete_version(shot_id, version_id)
    return {"ok": True}


# =====================================================================
# Timeline
# =====================================================================
@router.get("/projects/{project_id}/timeline")
def list_timeline(project_id: str):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return sb.list_timeline(project_id)


@router.post("/projects/{project_id}/timeline/sync")
def sync_timeline(project_id: str):
    """把已生成镜头同步进时间线"""
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return sb.sync_timeline(project_id)


@router.patch("/timeline/{item_id}")
def update_timeline_item(item_id: str, data: TimelineUpdate):
    item = sb.update_timeline_item(
        item_id, **{k: v for k, v in data.model_dump(exclude_unset=True).items()}
    )
    if not item:
        raise HTTPException(404, "时间线片段不存在")
    return item


@router.post("/projects/{project_id}/timeline/reorder")
def reorder_timeline(project_id: str, data: ReorderBody):
    return sb.reorder_timeline(project_id, data.ids)


@router.delete("/timeline/{item_id}")
def remove_timeline_item(item_id: str):
    sb.remove_timeline_item(item_id)
    return {"ok": True}


# =====================================================================
# Export
# =====================================================================
@router.post("/projects/{project_id}/export", status_code=201)
def create_export(project_id: str):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    exp = sb.create_export(project_id)
    start_export(exp["id"])
    return exp


@router.get("/projects/{project_id}/exports")
def list_exports(project_id: str):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return sb.list_exports(project_id)


@router.get("/exports/{export_id}")
def get_export(export_id: str):
    exp = sb.get_export(export_id)
    if not exp:
        raise HTTPException(404, "导出记录不存在")
    return exp


# =====================================================================
# ComfyUI 设置
# =====================================================================
@router.get("/comfy/status")
def comfy_status():
    client = ComfyUIClient()
    ok = client.is_available()
    out = {"available": ok, "url": client.base_url, "queue": None}
    if ok:
        try:
            q = client.get_queue()
            out["queue"] = {
                "running": len(q.get("queue_running") or []),
                "pending": len(q.get("queue_pending") or []),
            }
        except Exception:
            pass
    return out


@router.get("/comfy/workflow")
def get_workflow():
    return load_workflow_template()


@router.put("/comfy/workflow")
def put_workflow(data: WorkflowUpdate):
    payload = {"prompt": data.prompt}
    if data._aivideo_map is not None:
        payload["_aivideo_map"] = data._aivideo_map
    try:
        return save_workflow_template(payload)
    except ComfyUIError as e:
        raise HTTPException(400, str(e))


@router.delete("/comfy/workflow")
def reset_workflow():
    """删除自定义模板，恢复内置默认工作流"""
    if DEFAULT_WORKFLOW.exists():
        DEFAULT_WORKFLOW.unlink()
    return load_workflow_template()
