"""幕布资产池（第十九批）

改造前 `scene.reference_image` 是一张图：场景里再生成一次就覆盖，没有历史，
也不能被多个场景/镜头复用。

本模块把幕布拆成独立资产：`plates`（池）+ `plate_versions`（不可变版本）。
版本机制统一走 `asset_versions`。

**兼容**：`scene.reference_image` 继续存在、继续被读取——它现在是被
`asset_versions` 当作"未收编的旧幕布"降级处理的兜底来源。因此：

- 老项目不动任何数据，生成结果不变；
- 要用版本化幕布，走 `promote_scene_backdrop()` 把旧图收编成 plate v1，
  或直接建新 plate。

版本解析优先级（见 asset_versions._resolve_plate_for_shot）：
镜头 `shots.plate_version` > 场景默认 `scenes.plate_version_id` > 旧 `scene.reference_image`。
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import asset_versions as av
from .db import db

FORM_FLAT = "flat"          # 平铺（普通横/竖图）
FORM_EQUIRECT = "equirect"  # 720° 全景（等距柱状）
FORMS = (FORM_FLAT, FORM_EQUIRECT)

STATUS_ACTIVE = "active"
STATUS_DRAFT = "draft"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return f"plt_{uuid.uuid4().hex[:8]}"


def create_plate(project_id: str, name: str = "", form: str = FORM_FLAT,
                 image_url: str = "", width: int = 0, height: int = 0,
                 sha256: str = "", note: str = "", status: str = STATUS_ACTIVE) -> dict:
    """新建幕布池 + v1。image_url 为空则先建一个只有名字的空幕布（草稿）。"""
    pid = _new_id()
    now = _utcnow()
    db.insert_and_get(
        "plates",
        ["id", "project_id", "name", "form", "current_version", "status",
         "created_at", "updated_at"],
        (pid, project_id, (name or "").strip() or "新幕布",
         form if form in FORMS else FORM_FLAT, 0, status, now, now),
        pid,
    )
    av.create_version(av.KIND_PLATE, pid, note=note or "初始版本",
                      image_url=image_url or "", width=int(width or 0),
                      height=int(height or 0), sha256=sha256 or "")
    return get_plate(pid)


def get_plate(plate_id: str) -> Optional[dict]:
    p = db.fetch_one("SELECT * FROM plates WHERE id = ?", (plate_id,))
    if p:
        p["current_version"] = av.get_current_version(av.KIND_PLATE, plate_id)
        p["usage_count"] = len(av.list_usages(av.KIND_PLATE, plate_id))
    return p


def list_plates(project_id: str, status: Optional[str] = None) -> list:
    if status:
        rows = db.fetch_all(
            "SELECT * FROM plates WHERE project_id = ? AND status = ? "
            "ORDER BY updated_at DESC",
            (project_id, status),
        )
    else:
        rows = db.fetch_all(
            "SELECT * FROM plates WHERE project_id = ? ORDER BY updated_at DESC",
            (project_id,),
        )
    for p in rows:
        p["usage_count"] = len(av.list_usages(av.KIND_PLATE, p["id"]))
    return rows


def update_plate(plate_id: str, **fields) -> Optional[dict]:
    """改名 / 改形态 / 改状态。**换图请走 `av.create_version`**。"""
    allowed = {"name", "form", "status"}
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "form" in fields and fields["form"] not in FORMS:
        raise ValueError(f"未知幕布形态: {fields['form']}（可选：{'/'.join(FORMS)}）")
    if not fields:
        return get_plate(plate_id)
    fields["updated_at"] = _utcnow()
    sql = f"UPDATE plates SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?"
    db.execute(sql, (*fields.values(), plate_id))
    return get_plate(plate_id)


def delete_plate(plate_id: str) -> tuple:
    """删除幕布。被镜头/场景引用则拒绝（返回 (False, usages)），不静默摘引用。"""
    ok, usages = av.delete_guard(av.KIND_PLATE, plate_id)
    if not ok:
        return False, usages
    db.execute("DELETE FROM plates WHERE id = ?", (plate_id,))
    return True, []


def bind_shot_plate(shot_id: str, plate_id: str, version_no: int = 0) -> None:
    """把幕布绑定到镜头（version_no=0 表示跟随该幕布的 current_version）。

    只写 `shots.plate_id` / `shots.plate_version`，不改任何旧列；
    已入队任务不受影响——它们用的是自己冻结的那一版。
    """
    db.execute(
        "UPDATE shots SET plate_id = ?, plate_version = ? WHERE id = ?",
        (plate_id or "", int(version_no or 0), shot_id),
    )


def bind_scene_plate(scene_id: str, version_id: str = "") -> None:
    """设/清场景默认幕布（存 `plate_versions.id`，不是版本号——场景不跟随漂移）。

    传空串 = 清除默认幕布（镜头未单独绑定时回落旧 `scene.reference_image`）。
    """
    db.execute(
        "UPDATE scenes SET plate_version_id = ? WHERE id = ?",
        (version_id or "", scene_id),
    )


def promote_scene_backdrop(scene_id: str, name: str = "", form: str = FORM_FLAT,
                           sha256: str = "") -> dict:
    """把现有 `scene.reference_image` 收编成幕布资产（v1），并设为该场景默认幕布。

    **只读旧字段、只写新表与新列**——不改 `scene.reference_image` 的值，
    所以即便收编后立刻回滚代码，旧行为也完全不变。
    """
    scene = db.fetch_one("SELECT * FROM scenes WHERE id = ?", (scene_id,))
    if not scene:
        raise ValueError(f"场景不存在: {scene_id}")
    url = (scene.get("reference_image") or "").strip()
    if not url:
        raise ValueError("该场景还没有幕布图（scene.reference_image 为空），无法收编")
    plate = create_plate(
        scene["project_id"],
        name=name or (scene.get("title") or "幕布"),
        form=form, image_url=url, sha256=sha256,
        note=f"由场景 {scene_id} 的既有幕布收编",
    )
    ver = av.get_version_by_no(av.KIND_PLATE, plate["id"], plate["current_version"])
    if ver:
        bind_scene_plate(scene_id, ver["id"])
    return plate


def to_json(value) -> str:
    return json.dumps(value, ensure_ascii=False)
