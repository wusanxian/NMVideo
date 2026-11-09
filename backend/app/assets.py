"""角色与场景资产库数据层（第二批）

角色身份卡：外貌 / 服装 / 参考图（正侧背）/ 表情 / 固定音色 / 关联 LoRA。
场景资产字段挂在 scenes 表上（layout/style/reference_image，见 db.py 迁移）。

角色与分镜多对多关联：shots.character_ids 存 JSON id 列表。
生成时由路由层把角色外貌/服装拼进提示词，保证同一角色跨镜头外观一致。

第二阶段 2A：读/写经 db 统一接口，不再直接触摸 db._conn/db._lock。
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from .db import db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _row(sql: str, args: tuple = ()) -> Optional[dict]:
    return db.fetch_one(sql, args)


def _rows(sql: str, args: tuple = ()) -> list:
    return db.fetch_all(sql, args)


def _write(sql: str, args: tuple = ()):
    db.execute(sql, args)


ROLE_OPTIONS = ("主角", "配角", "群演", "旁白", "其他")

REF_TYPES = ("front", "side", "back", "expression", "other")
REF_TYPE_LABELS = {
    "front": "正面",
    "side": "侧面",
    "back": "背面",
    "expression": "表情",
    "other": "其他",
}


# =====================================================================
# Character
# =====================================================================
def create_character(project_id: str, **fields) -> dict:
    cid = _new_id("chr")
    now = _utcnow()
    row = {
        "id": cid,
        "project_id": project_id,
        "name": (fields.get("name") or "").strip() or "新角色",
        "role": fields.get("role") or "配角",
        "appearance": fields.get("appearance") or "",
        "outfit": fields.get("outfit") or "",
        "voice_id": fields.get("voice_id") or "",
        "voice_name": fields.get("voice_name") or "",
        "lora": fields.get("lora") or "",
        "expressions": json.dumps(fields.get("expressions") or [], ensure_ascii=False),
        "reference_images": json.dumps(
            fields.get("reference_images") or [], ensure_ascii=False
        ),
        "notes": fields.get("notes") or "",
        "created_at": now,
        "updated_at": now,
    }
    # 插入与读回同一事务：创建成功必得实际记录（2A）
    character = db.insert_and_get(
        "characters", list(row.keys()), tuple(row.values()), cid
    )
    return character


def get_character(character_id: str) -> Optional[dict]:
    c = _row("SELECT * FROM characters WHERE id = ?", (character_id,))
    if c:
        _decode(c)
    return c


def list_characters(project_id: str) -> list:
    chars = _rows(
        "SELECT * FROM characters WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    )
    for c in chars:
        _decode(c)
    return chars


def update_character(character_id: str, **fields) -> Optional[dict]:
    allowed = {
        "name", "role", "appearance", "outfit", "voice_id", "voice_name",
        "lora", "expressions", "reference_images", "notes",
    }
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not fields:
        return get_character(character_id)
    # 列表字段序列化为 JSON 存储
    for key in ("expressions", "reference_images"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    fields["updated_at"] = _utcnow()
    sql = f"UPDATE characters SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?"
    _write(sql, (*fields.values(), character_id))
    return get_character(character_id)


def delete_character(character_id: str) -> None:
    _write("DELETE FROM characters WHERE id = ?", (character_id,))
    # 从所有分镜的关联里摘除
    for shot in _rows("SELECT id, character_ids FROM shots"):
        try:
            ids = json.loads(shot["character_ids"] or "[]")
        except (ValueError, TypeError):
            continue
        if character_id in ids:
            ids = [i for i in ids if i != character_id]
            _write(
                "UPDATE shots SET character_ids = ? WHERE id = ?",
                (json.dumps(ids), shot["id"]),
            )


def characters_of_shot(shot: dict) -> list:
    """取分镜关联的角色完整卡（用于生成时拼提示词 / UI 展示）

    兼容两种形态：character_ids 已是 list（解码后）或 JSON 字符串（原始行）。
    """
    raw = shot.get("character_ids")
    if isinstance(raw, str):
        try:
            ids = json.loads(raw or "[]")
        except (ValueError, TypeError):
            ids = []
    elif isinstance(raw, list):
        ids = raw
    else:
        ids = []
    out = []
    for cid in ids:
        c = get_character(cid)
        if c:
            out.append(c)
    return out


def compose_prompt_with_characters(shot: dict, characters: list) -> str:
    """把角色外貌/服装描述拼到分镜提示词前部（跨镜头角色一致性的关键）"""
    base = (shot.get("prompt") or "").strip()
    if not characters:
        return base
    parts = []
    for c in characters:
        seg = []
        if c.get("appearance"):
            seg.append(c["appearance"].strip())
        if c.get("outfit"):
            seg.append(f"wearing {c['outfit'].strip()}")
        if seg:
            parts.append(f"{c['name']}: " + ", ".join(seg))
    if not parts:
        return base
    prefix = " | ".join(parts)
    return f"{prefix} || {base}" if base else prefix


def compose_prompt_with_scene(shot: dict, scene: dict) -> str:
    """把场景 environment/lighting 拼到分镜提示词尾部（跨镜头场景一致）。

    角色管「谁」，场景管「在哪 / 什么光」——两者正交、互不干扰。
    与 compose_prompt_with_characters 完全镜像，只是拼装段不同。
    """
    base = (shot.get("prompt") or "").strip()
    if not scene:
        return base
    seg = []
    if scene.get("environment"):
        seg.append(f"scene: {scene['environment'].strip()}")
    if scene.get("lighting"):
        seg.append(f"lighting: {scene['lighting'].strip()}")
    if not seg:
        return base
    suffix = " || ".join(seg)
    return f"{base} || {suffix}" if base else suffix


def generate_scene_backdrop(scene: dict) -> str:
    """根据场景环境 / 风格 / 光照 / 布局生成幕布（背景）图，回写 scene.reference_image。

    返回生成的 URL。依赖「模型服务」页配置的 imagegen（OpenAI 兼容 images 端点）。
    背景图刻意加负向语义的提示（空镜、无人物）以避免把角色画进幕布。
    """
    from . import imagegen
    from . import storyboard as sb

    env = (scene.get("environment") or "").strip()
    style = (scene.get("style") or "").strip()
    light = (scene.get("lighting") or "").strip()
    layout = (scene.get("layout") or "").strip()
    if not env and not style:
        raise ValueError("场景缺少 environment / style，无法生成幕布图")
    seg = [s for s in (env, style, light, layout) if s]
    prompt = "，".join(seg)
    prompt += ("，电影感环境背景，空镜，无人物，宽视角展现场景空间与氛围，"
               "高细节，用于动画分镜背景")
    urls = imagegen.generate_image(prompt=prompt, size="1280x720", n=1)
    if not urls:
        raise ValueError("图像服务未返回幕布图")
    url = urls[0]
    sb.update_scene(scene["id"], reference_image=url)
    return url


def _decode(c: dict) -> None:
    for key in ("expressions", "reference_images"):
        try:
            c[key] = json.loads(c.get(key) or "[]")
        except (ValueError, TypeError):
            c[key] = []
