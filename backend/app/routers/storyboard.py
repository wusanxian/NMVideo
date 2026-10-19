"""分镜工作台 REST API：项目 / 场景 / 分镜 / 版本 / 时间线 / 导出 / ComfyUI"""
import json
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
    # 第三批（S3）：预览绑定——预览返回的 project_revision / content_hash 回传校验
    expected_revision: Optional[int] = None
    content_hash: Optional[str] = None
    # 破坏性重建（将删除已生成版本/时间线）必须显式确认；确认前服务端已自动备份
    confirm: bool = False


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
    first_frame_image: str = ""
    last_frame_image: str = ""


class ShotUpdate(BaseModel):
    title: Optional[str] = None
    dialogue: Optional[str] = None
    shot_size: Optional[str] = None
    camera_movement: Optional[str] = None
    duration_s: Optional[float] = Field(None, ge=0.5, le=60)
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    reference_image: Optional[str] = None
    # PR-02：独立首/尾帧（first_last 模式分别注入工作流首/尾帧节点）
    first_frame_image: Optional[str] = None
    last_frame_image: Optional[str] = None


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
    # 第三批：审核门分离——design=设计审核（生成前）/ video=视频审核（默认，兼容旧行为）
    stage: str = Field("video", pattern="^(design|video)$")
    # 提交时复核：客户端基于的采用版本，不一致 → 409（过期提交）
    expected_version_id: str = ""


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
    try:
        sb.delete_project(project_id)
    except sb.ReferenceConflictError as e:
        raise HTTPException(409, detail={"message": str(e), "references": e.details})
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

    - mode=preview：返回差异预览 + content_hash + project_revision，不写库
    - mode=append：标题相同的已有场景跳过，保留其镜头制作成果
    - mode=rebuild：覆盖重建（单事务，失败整体回滚）；有已生成版本/时间线时
      必须 confirm=true（服务端自动先备份），否则 409 返回将损失的明细；
      传入 expected_revision/content_hash 时校验过期提交（409/400）
    """
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    if data.mode == "preview":
        return sb.script_preview(project_id, data.text)
    try:
        return sb.import_script(
            project_id, data.text, mode=data.mode,
            expected_revision=data.expected_revision,
            content_hash=data.content_hash, confirm=data.confirm,
        )
    except sb.StalePreviewError as e:
        raise HTTPException(409, str(e))
    except sb.DestructiveImportError as e:
        raise HTTPException(409, detail={"message": str(e), "loss": e.details})
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
    try:
        sb.delete_scene(scene_id)
    except sb.ReferenceConflictError as e:
        raise HTTPException(409, detail={"message": str(e), "references": e.details})
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
    try:
        sb.delete_shot(shot_id)
    except sb.ReferenceConflictError as e:
        raise HTTPException(409, detail={"message": str(e), "references": e.details})
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

    # ---- 参考图 / 首尾帧 / 续接处理（PR-02：独立首帧+尾帧）----
    reference_image = (shot.get("reference_image") or "").strip()
    first_frame = (shot.get("first_frame_image") or "").strip()
    last_frame = (shot.get("last_frame_image") or "").strip()
    continue_from = (shot.get("continue_from") or "").strip()
    source_version_id = (shot.get("source_video_version_id") or "").strip()
    mode = data.mode or shot.get("mode") or "t2v"

    # 兼容旧数据：i2v / first_last 未设独立首帧时，reference_image 充当首帧
    # （旧 first_last 只有一张参考图，行为不变；新 UI 两个独立输入）
    if mode in ("i2v", "first_last") and not first_frame:
        first_frame = reference_image

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
        from ..managed_paths import VIDEO_SUFFIXES, resolve_managed_asset

        # base_dir 显式传本模块 OUTPUTS_DIR（与 refs_dir/snap_dir 同一绑定，
        # 历史上续接取帧就用模块级 OUTPUTS_DIR）
        src_path = resolve_managed_asset(src_ver["video_url"],
                                         allowed_types=VIDEO_SUFFIXES,
                                         base_dir=OUTPUTS_DIR)
        if not src_path or not src_path.exists():
            raise HTTPException(400, f"上一镜头视频缺失或不受管: {src_ver['video_url']}")
        refs_dir = OUTPUTS_DIR / "refs"
        refs_dir.mkdir(parents=True, exist_ok=True)
        frame_path = refs_dir / f"{shot_id}_from_{src_shot['id']}.png"
        try:
            extract_last_frame(src_path, frame_path)
        except Exception as e:
            raise HTTPException(400, f"抽取上一镜头末帧失败: {e}")
        reference_image = f"/outputs/refs/{frame_path.name}"
        source_version_id = src_ver["id"]  # PR-02：记录续接来源版本（可复现 + 过期提示）
        first_frame = reference_image      # 续接的首帧即来源末帧
        sb.update_shot(shot_id, mode="continuation",
                       continue_from=src_shot["id"], reference_image=reference_image,
                       source_video_version_id=source_version_id)
    elif mode in ("i2v", "first_last"):
        if not first_frame:
            raise HTTPException(400, f"模式「{mode}」需要先设置参考图/首帧图片")
        if mode == "first_last" and not last_frame:
            raise HTTPException(
                400, "首尾帧模式需要同时设置尾帧图片（PR-02：首帧与尾帧分别独立输入）"
            )

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
    # PR-02：入队即冻结输入资产（首/尾帧与续接来源版本）——
    # 记录的是本机 /outputs/ URL（非 ComfyUI input 引用名），保证可复现
    params["first_frame_image"] = first_frame
    params["last_frame_image"] = last_frame
    params["source_video_version_id"] = source_version_id
    # PR-04：入队即冻结角色卡快照（生成时 compose_prompt 所用的角色数据）——
    # 之后修改角色卡不影响本任务的复现语义；完成后随版本存档（角色版本锁定）
    params["characters_snapshot"] = json.dumps([
        {k: c.get(k) for k in ("id", "name", "role", "appearance", "outfit",
                               "lora", "expressions", "reference_images",
                               "voice_id", "voice_name")}
        for c in characters
    ], ensure_ascii=False)

    # ---- P0-02：ComfyUI 备料先行，全部成功后才创建 job ----
    # 旧顺序先 create_job 再上传参考图：上传失败（HTTPException 400）不回滚，
    # 留下无 prompt_id 的 pending 僵尸任务，重启后还会被恢复器捡起来重跑。
    # 现在模板加载/参考图上传/工作流构建/预检都在建 job 前完成，
    # 任一失败 → 400 且 DB 不落任何可执行任务。
    graph = None
    if data.engine == "comfyui":
        from ..comfyui import (
            build_snapshot_payload,
            build_shot_workflow,
            load_workflow_template,
            upload_reference_image,
        )

        template = load_workflow_template()
        prompt_graph = template.get("prompt")
        if not isinstance(prompt_graph, dict) or not prompt_graph:
            raise HTTPException(
                400, "工作流模板为空或缺少 prompt 节点图，请在 ComfyUI 设置里检查"
            )
        # PR-03：模板声明了能力元数据（_meta）时，点击生成前即阻断——
        # 不支持的输入模式 / 缺失的 ComfyUI 节点。无 _meta 的模板
        # （默认模板/旧配置）零影响，行为与旧版完全一致。
        from ..workflow_capabilities import (
            capability_from_template,
            check_missing_nodes,
            mode_supported,
        )

        cap = capability_from_template(template)
        if cap:
            if not mode_supported(cap, mode):
                raise HTTPException(
                    400, f"当前工作流（{cap['name'] or '未命名'}）不支持「{mode}」"
                         f"模式；声明支持：{'、'.join(cap['supports'])}。"
                         "请在 ComfyUI 设置里改用匹配的模板"
                )
            if cap["required_nodes"]:
                missing, probe_ok = check_missing_nodes(client, cap["required_nodes"])
                if missing:
                    raise HTTPException(
                        400, f"当前工作流缺少 ComfyUI 节点：{'、'.join(missing)}。"
                             "请安装对应自定义节点（可在「模型服务」页的工作流"
                             "能力检查里查看详情）后重试"
                    )
        template_map = template.get("_aivideo_map") or {}
        # PR-02：first_last 需要模板真实支持两张关键帧——尾帧节点未映射
        # 或首帧无任何可走节点时明确失败，不静默忽略（静默丢图会让用户
        # 以为尾帧生效了）
        if mode == "first_last":
            if not template_map.get("last_frame_image"):
                raise HTTPException(
                    400, "当前工作流模板未映射尾帧节点（_aivideo_map 缺 "
                         "last_frame_image），无法执行首尾帧模式；请在 ComfyUI "
                         "设置里改用支持首尾帧的模板"
                )
            if not template_map.get("first_frame_image") and not template_map.get(
                "reference_image"
            ):
                raise HTTPException(
                    400, "当前工作流模板未映射首帧节点（_aivideo_map 缺 "
                         "first_frame_image / reference_image），无法执行首尾帧模式"
                )
        # 图片路由：
        # - t2v：无图
        # - i2v：沿用既有链路 reference_image（行为不变）
        # - continuation：来源末帧走 reference_image；模板另映射了
        #   first_frame_image 时同值注入（映射哪个用哪个）
        # - first_last：首帧优先走 first_frame_image 专用节点；模板只映射
        #   reference_image 时兼容旧式单节点模板（首帧经 reference_image 注入）；
        #   尾帧走 last_frame_image 专用节点
        ref_for_graph = reference_image
        first_for_graph = ""
        last_for_graph = ""
        if mode == "first_last":
            if template_map.get("first_frame_image"):
                first_for_graph = first_frame
            else:
                ref_for_graph = first_frame  # 旧式模板：首帧经参考图节点
            last_for_graph = last_frame
        elif mode == "continuation":
            first_for_graph = first_frame
        # 2C：工作流消费图片时，上传到 ComfyUI input 目录并用返回名注入
        # （LoadImage 只能引用 input 目录内的文件，本机路径对 ComfyUI 无意义）
        if ref_for_graph and template_map.get("reference_image"):
            try:
                ref_for_graph = upload_reference_image(client, ref_for_graph)
            except Exception as e:
                raise HTTPException(400, f"参考图上传 ComfyUI 失败: {e}")
        for label, value, mapped_key in (
            ("首帧", first_for_graph, "first_frame_image"),
            ("尾帧", last_for_graph, "last_frame_image"),
        ):
            if not value or not template_map.get(mapped_key):
                continue
            try:
                uploaded = upload_reference_image(client, value)
            except Exception as e:
                raise HTTPException(400, f"{label}图片上传 ComfyUI 失败: {e}")
            if mapped_key == "first_frame_image":
                first_for_graph = uploaded
            else:
                last_for_graph = uploaded
        graph = build_shot_workflow({
            "prompt": params["prompt"],
            "negative_prompt": params["negative_prompt"],
            "width": params["width"], "height": params["height"],
            "num_frames": params["num_frames"], "seed": params["seed"],
            "steps": params["steps"], "guidance": params["guidance"],
            "reference_image": ref_for_graph,
            "first_frame_image": first_for_graph,
            "last_frame_image": last_for_graph,
        }, template=template)
        if not graph:
            raise HTTPException(
                400, "工作流模板为空或缺少 prompt 节点图，请在 ComfyUI 设置里检查"
            )

    from .. import db as db_module

    job = db_module.db.create_job(params)
    job_id = job["id"]
    # 2B：ComfyUI 入队即固化工作流快照——排队期间用户在设置里改模板，
    # 不影响已排队任务（执行时优先读快照；快照写盘失败不阻断，执行时兜底重建）
    # PR-06：快照带 schema_version + 节点图 sha256，执行时校验，损坏/版本
    # 不符明确失败，不静默替换
    if graph is not None:
        try:
            snap_dir = OUTPUTS_DIR / "workflows"
            snap_dir.mkdir(parents=True, exist_ok=True)
            snap = snap_dir / f"{job_id}.json"
            snap.write_text(
                json.dumps(build_snapshot_payload(graph, job_id),
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            db_module.db.update_job(job_id, workflow_snapshot=str(snap))
        except Exception as e:
            print(f"[storyboard] ComfyUI 工作流快照写入失败（执行时兜底）: {e}")
    await job_queue.enqueue(job_id)
    sb.set_shot_status(shot_id, "queued", job_id=job_id, review_stage="generation")
    return db_module.db.get_job(job_id)


@router.post("/shots/{shot_id}/review")
def review_shot(shot_id: str, data: ShotReview):
    """审核（三态分离 + 质检前置 + 原子写入）。

    - stage=video（默认）：approve 先对候选版本做只读质检，error 级阻断时
      HTTP 422 带完整检查结果，且镜头状态与 current_version_id 均保持原值；
      通过后在同一事务原子写「采用指针 + 状态 + 审核记录」。
      expected_version_id 与当前采用版本不一致 → 409（过期提交）。
    - stage=design：设计审核（生成前），只检查创作参数；
      approve → review_stage=design_ok；rework → review_stage=design。
    - rework：打回重做，检查结果留痕不阻断。
    """
    if not sb.get_shot(shot_id):
        raise HTTPException(404, "分镜不存在")
    try:
        return sb.review_shot(
            shot_id, data.action, version_id=data.version_id,
            comment=data.comment, stage=data.stage,
            expected_version_id=data.expected_version_id,
        )
    except sb.ReviewBlockedError as e:
        raise HTTPException(422, detail={
            "message": str(e), "issues": e.issues, "score": e.score,
        })
    except (sb.StaleSubmissionError, sb.ReferenceConflictError) as e:
        raise HTTPException(409, detail={"message": str(e), "references": getattr(e, "details", {})})
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/shots/{shot_id}/versions")
def list_versions(shot_id: str):
    if not sb.get_shot(shot_id):
        raise HTTPException(404, "分镜不存在")
    return sb.list_versions(shot_id)


@router.post("/shots/{shot_id}/versions/{version_id}/adopt")
def adopt_version(shot_id: str, version_id: str):
    if not sb.get_shot(shot_id):
        raise HTTPException(404, "分镜不存在")
    version = sb.get_version(version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    if version["shot_id"] != shot_id:
        raise HTTPException(400, "版本不属于该镜头，无法采用")
    try:
        sb.adopt_version(shot_id, version_id)
    except sb.ReferenceConflictError as e:
        raise HTTPException(409, detail={"message": str(e), "references": e.details})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return sb.get_shot(shot_id)


@router.delete("/shots/{shot_id}/versions/{version_id}")
def delete_version(shot_id: str, version_id: str):
    if not sb.get_shot(shot_id):
        raise HTTPException(404, "分镜不存在")
    version = sb.get_version(version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    if version["shot_id"] != shot_id:
        raise HTTPException(400, "版本不属于该镜头，无法删除")
    try:
        sb.delete_version(shot_id, version_id)
    except sb.ReferenceConflictError as e:
        raise HTTPException(409, detail={"message": str(e), "references": e.details})
    except ValueError as e:
        raise HTTPException(400, str(e))
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
