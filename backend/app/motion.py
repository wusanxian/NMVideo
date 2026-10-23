"""动作模板与构图约束（改造.md PR-10 后半：角色与镜头连续性）

职责：
- 动作模板库（对峙/追击/格挡/收招等内置预设 + 项目级自定义模板）
- 镜头构图约束归一（人物左右站位、朝向——可编辑约束，存 shots.composition JSON）
- 生成时把「动作模板 + 站位/朝向 + 镜头运动」拼进提示词

冻结语义：约束在 generate_shot 拼装阶段进入 params["prompt"]，随任务快照
入队冻结——之后修改模板/构图不影响已入队任务的可复现（升级/重放用版本里
已拼好的 prompt，不再重新拼装）。
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional


def _db():
    """惰性获取 db 单例——db.py 迁移期会导入本模块做种子，此时 db 还未初始化完。"""
    from .db import db
    return db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# =====================================================================
# 内置动作模板（武侠向预设；action_prompt 用英文——视频模型友好）
# =====================================================================

BUILTIN_TEMPLATES = [
    {
        "id": "mt-builtin-standoff",
        "name": "对峙",
        "action_prompt": ("two opponents facing off, slowly closing in, "
                          "blades lowered but ready, tense standoff, eyes locked"),
        "default_camera_movement": "固定",
    },
    {
        "id": "mt-builtin-chase",
        "name": "追击",
        "action_prompt": ("high-speed pursuit, one warrior chasing another "
                          "across the scene, dynamic movement, dust kicking up"),
        "default_camera_movement": "跟镜",
    },
    {
        "id": "mt-builtin-block",
        "name": "格挡",
        "action_prompt": ("clashing blades, one warrior blocking an incoming "
                          "strike, sparks flying, sharp impact moment"),
        "default_camera_movement": "手持",
    },
    {
        "id": "mt-builtin-recover",
        "name": "收招",
        "action_prompt": ("follow-through and recovery, the warrior sheathing "
                          "the weapon and settling back into stance, motion "
                          "slowing to a stop, steady breath"),
        "default_camera_movement": "固定",
    },
]


def seed_builtin_templates(db_obj=None) -> None:
    """内置模板种子（INSERT OR IGNORE 幂等；迁移与空库首次启动都安全）。"""
    target = db_obj if db_obj is not None else _db()
    now = _utcnow()
    for t in BUILTIN_TEMPLATES:
        target.execute(
            "INSERT OR IGNORE INTO motion_templates "
            "(id, project_id, name, action_prompt, default_camera_movement, "
            " is_builtin, created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
            (t["id"], "", t["name"], t["action_prompt"],
             t["default_camera_movement"], now, now),
        )


# =====================================================================
# CRUD
# =====================================================================

def get_template(template_id: str) -> Optional[dict]:
    return _db().fetch_one("SELECT * FROM motion_templates WHERE id = ?", (template_id,))


def list_templates(project_id: str = "") -> list:
    """内置全局模板 + 指定项目的自定义模板（project_id 为空只返回内置）。"""
    if project_id:
        return _db().fetch_all(
            "SELECT * FROM motion_templates WHERE project_id = '' OR project_id = ? "
            "ORDER BY is_builtin DESC, created_at", (project_id,))
    return _db().fetch_all(
        "SELECT * FROM motion_templates WHERE project_id = '' "
        "ORDER BY is_builtin DESC, created_at")


def create_template(project_id: str, name: str, action_prompt: str,
                    default_camera_movement: str = "") -> dict:
    project_id = (project_id or "").strip()
    if not project_id:
        # project_id='' 是内置模板保留桶，自定义模板必须挂项目
        raise ValueError("自定义模板必须指定所属项目（project_id）")
    name = (name or "").strip()
    if not name:
        raise ValueError("模板名称不能为空")
    if len(name) > 60:
        raise ValueError("模板名称过长（≤60 字符）")
    action_prompt = (action_prompt or "").strip()
    if not action_prompt:
        raise ValueError("动作描述不能为空")
    tid = f"mt_{uuid.uuid4().hex[:8]}"
    now = _utcnow()
    _db().execute(
        "INSERT INTO motion_templates "
        "(id, project_id, name, action_prompt, default_camera_movement, "
        " is_builtin, created_at, updated_at) VALUES (?,?,?,?,?,0,?,?)",
        (tid, project_id, name, action_prompt,
         (default_camera_movement or "").strip(), now, now),
    )
    return get_template(tid)


def update_template(template_id: str, **fields) -> Optional[dict]:
    tpl = get_template(template_id)
    if not tpl:
        return None
    if tpl["is_builtin"]:
        raise ValueError("内置模板不可修改，可复制为项目自定义模板后再编辑")
    allowed = {"name", "action_prompt", "default_camera_movement"}
    updates = {}
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        v = str(v).strip()
        if k == "name" and not v:
            raise ValueError("模板名称不能为空")
        if k == "action_prompt" and not v:
            raise ValueError("动作描述不能为空")
        if len(v) > 2000:
            raise ValueError(f"{k} 过长（≤2000 字符）")
        updates[k] = v
    if not updates:
        return tpl
    updates["updated_at"] = _utcnow()
    sql = ", ".join(f"{k} = ?" for k in updates)
    _db().execute(f"UPDATE motion_templates SET {sql} WHERE id = ?",
               (*updates.values(), template_id))
    return get_template(template_id)


def delete_template(template_id: str) -> None:
    tpl = get_template(template_id)
    if not tpl:
        return
    if tpl["is_builtin"]:
        raise ValueError("内置模板不可删除")
    # 引用该模板的镜头容忍悬空（生成时查不到就跳过），不阻塞删除
    _db().execute("DELETE FROM motion_templates WHERE id = ?", (template_id,))


# =====================================================================
# 构图约束归一（shots.composition JSON）
# =====================================================================

POSITIONS = ("left", "center", "right")
FACINGS = ("toward_camera", "back_to_camera", "toward_left", "toward_right",
           "profile")

_POSITION_ZH = {"left": "画面左侧", "center": "画面中央", "right": "画面右侧"}
_FACING_ZH = {
    "toward_camera": "面向镜头",
    "back_to_camera": "背对镜头",
    "toward_left": "面向画面左侧",
    "toward_right": "面向画面右侧",
    "profile": "侧身对镜头",
}


def normalize_composition(value) -> str:
    """构图约束归一 → JSON 字符串。

    兼容 None / '' / '{}' / dict / 已序列化字符串（升级路径回灌），
    与 quality.normalize_generation 对 reference_images 的宽容策略一致。
    非法成员值直接拒绝（不静默丢弃——拼错站位比缺站位更危险）。
    """
    if value is None:
        return "{}"
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return "{}"
        try:
            value = json.loads(s)
        except (ValueError, TypeError):
            raise ValueError("构图约束格式错误：应为 JSON 对象")
    if not isinstance(value, dict):
        raise ValueError("构图约束格式错误：应为对象 {character_positions, character_facing}")
    clean = {}
    positions = value.get("character_positions")
    if positions:
        if not isinstance(positions, dict):
            raise ValueError("character_positions 应为 {角色id: left|center|right}")
        for cid, pos in positions.items():
            if pos is None:
                continue
            if pos not in POSITIONS:
                raise ValueError(
                    f"站位约束非法：{pos}（可选 {'/'.join(POSITIONS)}）")
            clean.setdefault("character_positions", {})[str(cid)] = pos
    facings = value.get("character_facing")
    if facings:
        if not isinstance(facings, dict):
            raise ValueError("character_facing 应为 {角色id: 朝向枚举}")
        for cid, face in facings.items():
            if face is None:
                continue
            if face not in FACINGS:
                raise ValueError(
                    f"朝向约束非法：{face}（可选 {'/'.join(FACINGS)}）")
            clean.setdefault("character_facing", {})[str(cid)] = face
    return json.dumps(clean, ensure_ascii=False)


def decode_composition(shot: dict) -> dict:
    """shot.composition JSON → dict（脏值按空处理，读取不抛异常）。"""
    raw = shot.get("composition")
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
        return out if isinstance(out, dict) else {}
    except (ValueError, TypeError):
        return {}


# =====================================================================
# 提示词拼装（generate_shot 时调用；产出随 params["prompt"] 冻结）
# =====================================================================

def compose_motion_prompt(shot: dict, characters: list) -> str:
    """把动作模板 + 站位/朝向 + 镜头运动拼成约束段。

    返回空串表示无任何约束（不污染提示词）。角色名取自 characters
    （生成时已绑定的角色卡），约束里引用了未绑定角色时跳过该条。
    """
    name_by_id = {c["id"]: c.get("name") or c["id"] for c in characters or []}
    segments = []

    # 动作模板（悬空容忍：模板已删则跳过）
    tpl_id = (shot.get("motion_template_id") or "").strip()
    if tpl_id:
        tpl = get_template(tpl_id)
        if tpl and tpl.get("action_prompt"):
            segments.append(f"动作·{tpl['name']}: {tpl['action_prompt']}")

    comp = decode_composition(shot)
    pos_map = comp.get("character_positions") or {}
    face_map = comp.get("character_facing") or {}
    for cid, pos in pos_map.items():
        name = name_by_id.get(cid)
        if name and pos in _POSITION_ZH:
            segments.append(f"{name}位于{_POSITION_ZH[pos]}")
    for cid, face in face_map.items():
        name = name_by_id.get(cid)
        if name and face in _FACING_ZH:
            segments.append(f"{name}{_FACING_ZH[face]}")

    # 镜头运动（PR-10 明确点名的可编辑约束；既有字段首次接入提示词）
    move = (shot.get("camera_movement") or "").strip()
    if move and move != "固定":
        segments.append(f"镜头{move}")

    return "；".join(segments)
