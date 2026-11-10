"""服装资产池（第十九批）

改造前"服装"只存在于 `characters.outfit` 的一个字符串里：同一个角色想换装，
只能改角色卡，等于把角色身份和当次穿着绑死。

本模块把服装拆成**独立资产**：

- `outfits`：池。`character_id` 为空表示项目通用服装（群演、配角复用）。
- `outfit_versions`：不可变版本（参考图 / 配色 / 可选独立网格 / 材质）。

版本机制不在本模块重复实现——统一走 `asset_versions`（同一套语义、同一套冻结）。

**兼容**：不改 `characters.outfit` 的行为。它继续正常存在、继续被
`compose_prompt_with_characters` 使用；服装池是新增能力，两者并存。
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import asset_versions as av
from .db import db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return f"out_{uuid.uuid4().hex[:8]}"


def _decode(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return row
    try:
        row["reference_assets"] = json.loads(row.get("reference_assets") or "[]")
    except (ValueError, TypeError):
        row["reference_assets"] = []
    return row


def create_outfit(project_id: str, name: str = "", character_id: str = "",
                  **fields) -> dict:
    """新建服装池记录，并落 v1（版本化从第一件衣服就生效）。"""
    oid = _new_id()
    now = _utcnow()
    rec = db.insert_and_get(
        "outfits",
        ["id", "project_id", "character_id", "name", "current_version",
         "created_at", "updated_at"],
        (oid, project_id, character_id or "", (name or "").strip() or "新服装", 0,
         now, now),
        oid,
    )
    if fields.get("create_initial_version", True):
        av.create_version(av.KIND_OUTFIT, oid, note="初始版本", **{
            k: v for k, v in fields.items() if k != "create_initial_version"
        })
    return get_outfit(oid)


def get_outfit(outfit_id: str) -> Optional[dict]:
    o = db.fetch_one("SELECT * FROM outfits WHERE id = ?", (outfit_id,))
    if o:
        o["current_version"] = av.get_current_version(av.KIND_OUTFIT, outfit_id)
    return o


def list_outfits(project_id: str, character_id: Optional[str] = None) -> list:
    if character_id is None:
        rows = db.fetch_all(
            "SELECT * FROM outfits WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        )
    else:
        rows = db.fetch_all(
            "SELECT * FROM outfits WHERE project_id = ? AND character_id = ? "
            "ORDER BY created_at",
            (project_id, character_id),
        )
    return rows


def update_outfit(outfit_id: str, **fields) -> Optional[dict]:
    """改名 / 改归属。**内容变化请走 `av.create_version`**，本函数不动版本内容。"""
    allowed = {"name", "character_id"}
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not fields:
        return get_outfit(outfit_id)
    fields["updated_at"] = _utcnow()
    sql = f"UPDATE outfits SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?"
    db.execute(sql, (*fields.values(), outfit_id))
    return get_outfit(outfit_id)


def delete_outfit(outfit_id: str) -> tuple:
    """删除服装。被镜头引用则拒绝（返回 (False, usages)），不静默摘引用。"""
    ok, usages = av.delete_guard(av.KIND_OUTFIT, outfit_id)
    if not ok:
        return False, usages
    db.execute("DELETE FROM outfits WHERE id = ?", (outfit_id,))
    return True, []
