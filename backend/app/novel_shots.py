"""PR-N04：结构化分镜——AI 前期制作的第三道工序。

职责边界（第一期）：
- 结构化分镜：LLM 把「审核通过的动漫剧本 + 故事圣经」展开为逐镜头草案
  （场景分组 + 景别/机位运动/画面动作/对白/出场人物/走位出入画/情绪/时长/
  生成模式建议），JSON 校验 + 失败重试一次；
- 模式建议合法性：t2v/i2v/first_last/continuation 四模式之一，非法值回落
  t2v；首镜头强制 t2v（没有可续接的前序画面）；
- 编辑/排序：所有字段白名单可改，支持镜头重排（顺序即故事顺序）；
- 审核版本：approve 存不可变快照（整计划 + 全部镜头），审核后改动回草稿；
- 交接：审核通过的分镜计划 → 写入目标项目的 Scene/Shot（含角色卡自动建档
  与 character_ids 关联、模式、时长、景别、对白），**只建数据不启动生成**；
  幂等（plan.project_id 记录去向，项目被删可重建）；
- 定位原小说：镜头可挂章节快照（chapter_refs），随生成请求由 LLM 附带。

不自动启动视频生成；不触碰已有项目数据（交接目标项目若已有制作成果，
明确 400 拒绝并说明原因，绝不静默覆盖）。
"""
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import llm
from . import novel as novel_mod
from . import novel_bible as bible_mod
from . import novel_script as script_mod
from .db import db

_MAX_TOKENS = 6000
# 单镜头时长护栏（秒）：视频生成单段常规区间，防 LLM 给出 0.5 秒或 60 秒
_MIN_DUR, _MAX_DUR = 1, 15
_MAX_ITEMS = 120

# 与分镜工作台 shots 表/镜头编辑器对齐的枚举（既有约定，不复辟新词表）
SHOT_SIZES = ("远景", "全景", "中景", "近景", "特写")
CAMERA_MOVES = ("固定", "推镜", "拉镜", "摇镜", "移镜", "跟镜", "环绕", "手持")
# LLM 可能输出裸运动字（推/拉/摇/移/跟）→ 归一为工作台存储值
_CAMERA_ALIAS = {"推": "推镜", "拉": "拉镜", "摇": "摇镜",
                 "移": "移镜", "跟": "跟镜"}
MODES = ("t2v", "i2v", "first_last", "continuation")

_PLAN_STATUSES = ("draft", "approved")


class ShotPlanError(Exception):
    """结构化分镜业务错误（message 面向用户，status 为 HTTP 语义）"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# =====================================================================
# 基础工具
# =====================================================================
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


def _s(value, limit: int = 500) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _dedupe(items: list) -> list:
    out = []
    for it in items:
        if it and it not in out:
            out.append(it)
    return out


def _cfg_model() -> str:
    try:
        return llm._cfg().get("model", "")
    except Exception:
        return ""


def _chapter_ref(ch: dict) -> dict:
    return {"chapter_id": ch["id"], "title": ch["title"],
            "start": ch["start"], "end": ch["end"]}


# =====================================================================
# LLM：结构化分镜（prompt + JSON 提取/校验 + 重试）
# =====================================================================
_SHOTPLAN_SYSTEM = (
    "你是动画分镜师。用户给出动漫剧本、人物设定与场景设定，"
    "请把剧本展开为逐镜头分镜表。只输出 JSON，不要任何解释：\n"
    '{"scenes": [{"title": "场景名", "shots": ['
    '{"title": "镜头名（10字内）", "shot_size": "中景", '
    '"camera_movement": "推", "action": "画面动作描述（60字内）", '
    '"dialogue": "对白（无则空串）", "characters": ["标准人名"], '
    '"position": "走位/出入画（如：从左入画走向画面中心）", '
    '"emotion": "情绪（如：紧张）", "duration_sec": 4, '
    '"mode": "t2v"}]}]}\n'
    "规则：\n"
    "1. shot_size 从 远景/全景/中景/近景/特写 选；camera_movement 从 "
    "固定/推镜/拉镜/摇镜/移镜/跟镜 选；duration_sec 取 2-8 秒。\n"
    "2. mode 生成模式：剧本第一个镜头用 t2v；需要参考人物图的镜头用 i2v；"
    "明确需要首尾两帧的用 first_last；与上一镜头画面续接的用 continuation"
    "（同一场景内动作连续的镜头优先 continuation）。\n"
    "3. characters 只能用设定里的标准人名；对白保留剧本原文关键句。\n"
    "4. 每场景 2-6 个镜头；动作描写具体可拍（肢体动作/镜头运动/视线），"
    "不要写抽象心理活动。\n"
    "5. 场景划分与剧本一致，场景名沿用剧本题名。"
)


def _extract_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON 对象（容忍 ```json 代码块包裹与前后杂音）。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t.strip())
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM 输出中找不到 JSON 对象")
    try:
        data = json.loads(t[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 输出不是合法 JSON：{e}") from e
    if not isinstance(data, dict):
        raise ValueError("LLM 输出的 JSON 顶层不是对象")
    return data


def _coerce_items(data: dict) -> list:
    """分镜 JSON schema 校验：场景分组 + 镜头字段，容错转换。

    返回扁平镜头列表（seq 连续）；字段越界值回落安全默认；
    完全非法的镜头丢弃。人物名不做白名单强校验（未知人名保留，
    由调用方附提示—— bible 之外的名字可能是 LLM 合理补充）。
    """
    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        raise ValueError("JSON 缺少 scenes 数组")
    items: list = []
    seq = 0
    for sc in scenes:
        if not isinstance(sc, dict):
            continue
        scene_title = _s(sc.get("title"), 120) or "新场景"
        shots = sc.get("shots")
        if not isinstance(shots, list):
            continue
        for sh in shots:
            if not isinstance(sh, dict):
                continue
            seq += 1
            if seq > _MAX_ITEMS:
                break
            action = _s(sh.get("action"), 300)
            title = _s(sh.get("title"), 40)
            if not action and not title:
                continue  # 空镜头没有保留价值
            size = _s(sh.get("shot_size"), 10)
            move = _s(sh.get("camera_movement"), 10)
            move = _CAMERA_ALIAS.get(move, move)
            # 工作台运动约定：非法值回落「固定」（与镜头编辑器选项表一致）
            if move not in CAMERA_MOVES:
                move = "固定"
            chars = sh.get("characters")
            names = _dedupe(
                [_s(c, 40) for c in chars if isinstance(chars, list)]
            )
            try:
                duration = float(sh.get("duration_sec"))
            except (TypeError, ValueError):
                duration = 3.0
            duration = round(max(_MIN_DUR, min(_MAX_DUR, duration)), 1)
            mode = _s(sh.get("mode"), 20)
            if mode not in MODES:
                mode = "t2v"
            items.append({
                "seq": seq,
                "scene_title": scene_title,
                "title": title or f"镜头{seq}",
                "shot_size": size if size in SHOT_SIZES else "中景",
                "camera_movement": move or "固定",
                "action": action,
                "dialogue": _s(sh.get("dialogue"), 300),
                "character_names": names,
                "position": _s(sh.get("position"), 120),
                "emotion": _s(sh.get("emotion"), 40),
                "duration_sec": duration,
                "mode": mode,
            })
    if not items:
        raise ValueError("JSON 中没有解析出任何镜头")
    # 首镜头强制 t2v：没有前序画面可续接/参考
    items[0]["mode"] = "t2v"
    # 重排序号（丢弃空镜头后保持连续）
    for i, it in enumerate(items, 1):
        it["seq"] = i
    return items


def _shotplan_user_payload(novel: dict, episode: dict, script: dict,
                           bible: dict, chapters: list) -> str:
    """分镜生成 payload：动漫剧本 + 圣经人物/场景切片 + 节拍。"""
    bible_head = bible.get("bible", {})
    payload = {
        "episode_title": episode.get("title", ""),
        "episode_logline": episode.get("logline", ""),
        "beats": episode.get("beats") or [],
        "story_summary": bible_head.get("summary", ""),
        "characters": [
            {"name": c["name"], "role": c.get("role", ""),
             "appearance": c.get("appearance", ""), "clothing": c.get("clothing", ""),
             "catchphrase": c.get("catchphrase", "")}
            for c in bible.get("characters", [])[:40]
        ],
        "scenes": [
            {"name": s["name"], "description": s.get("description", ""),
             "atmosphere": s.get("atmosphere", "")}
            for s in bible.get("scenes", [])[:30]
        ],
        "script": (script.get("content") or "")[:_MAX_TOKENS],
        "chapter_titles": [
            {"title": c["title"], "id": c["id"], "start": c["start"], "end": c["end"]}
            for c in chapters[:50]
        ],
    }
    # 章节名集合仅供 LLM 参考（分镜不挂章节也行——章节定位由调用方补）
    return json.dumps(payload, ensure_ascii=False)


def generate_plan(novel_id: str, episode_id: str) -> dict:
    """LLM 生成结构化分镜草案（替换该分集既有计划；版本随旧计划级联删除）。

    依赖：审核通过的动漫剧本（保证「审核后才进分镜」的工序次序）+ 故事圣经。
    """
    novel = novel_mod.get_novel(novel_id, with_content=True)
    if not novel:
        raise ShotPlanError("小说不存在", 404)
    episode = _row("SELECT * FROM novel_episodes WHERE id = ? AND novel_id = ?",
                   (episode_id, novel_id))
    if not episode:
        raise ShotPlanError("分集不存在", 404)
    episode = script_mod._decode_json_cols(episode, ("chapter_refs", "beats"))
    script = _row("SELECT * FROM novel_scripts WHERE episode_id = ? AND novel_id = ?",
                  (episode_id, novel_id))
    if not script:
        raise ShotPlanError("剧本尚未生成：请先「生成动漫剧本」并审核通过")
    if (script["status"] or "") != "approved":
        raise ShotPlanError("剧本尚未审核通过：结构化分镜以审核态剧本为输入"
                            "（保证工序次序：剧本审核 → 分镜）")
    bible = bible_mod.get_bible(novel_id)
    if not bible:
        raise ShotPlanError("故事圣经尚未生成：请先运行「AI 章节分析」并"
                            "「生成故事圣经」（分镜以圣经保证人物/场景一致）")
    if not llm.is_configured():
        raise ShotPlanError(
            "LLM 服务未配置：请到「模型服务」页填写 base_url 和模型名"
            "（支持 DeepSeek / 通义 / Kimi / 本地 Ollama 等 OpenAI 兼容端点）")

    chapters = novel_mod.list_chapters(novel_id)
    # PR-N05：有进行中的提交批次时禁止重建（重建会级联删掉批次条目，
    # 正在跑的批次会失去镜头映射）
    active_sub = _row(
        "SELECT id FROM novel_submissions WHERE episode_id = ?"
        " AND status IN ('pending', 'running', 'cancelling') LIMIT 1",
        (episode_id,))
    if active_sub:
        raise ShotPlanError(
            "该分集有进行中的生成批次（预览/正式），请先取消或等待完成"
            "后再重新生成分镜", 409)
    messages = [
        {"role": "system", "content": _SHOTPLAN_SYSTEM},
        {"role": "user", "content": _shotplan_user_payload(
            novel, episode, script, bible, chapters)},
    ]
    raw = llm.chat(messages, temperature=0.5, max_tokens=_MAX_TOKENS)
    try:
        items = _coerce_items(_extract_json(raw))
    except ValueError:
        retry = messages + [
            {"role": "assistant", "content": raw[:500]},
            {"role": "user", "content":
                "上一次输出无法解析为分镜 JSON。请只输出符合上述格式的 JSON "
                "对象，不要任何解释、不要代码块。"},
        ]
        raw2 = llm.chat(retry, temperature=0.2, max_tokens=_MAX_TOKENS)
        try:
            items = _coerce_items(_extract_json(raw2))
        except ValueError as e:
            raise ShotPlanError(
                f"LLM 输出无法解析为分镜 JSON（重试后仍失败：{e}），请重试") from e

    # 章节快照：镜头级定位（LLM 未给章节时回落分集章节快照，保证可定位）
    ep_refs = episode.get("chapter_refs") or []
    chapter_by_title = {c["title"]: c for c in chapters}
    now = _utcnow()
    plan_id = _new_id("nsp")
    # PR-N06：冻结生成时的圣经版本——后续校验据此判断「圣经已更新，
    # 分镜引用的人物/场景信息可能过期」
    plan_bible_version = int(bible["bible"].get("version") or 0)
    with db.transaction() as conn:
        # 替换式生成：旧计划连带镜头/版本一起删（与分集刷新同语义）
        conn.execute("DELETE FROM novel_shot_plans WHERE episode_id = ?",
                     (episode_id,))
        conn.execute(
            "INSERT INTO novel_shot_plans (id, novel_id, episode_id, script_id,"
            " status, version, shot_count, total_duration_sec, model,"
            " bible_version, created_at, updated_at)"
            " VALUES (?,?,?,?,'draft',0,?,?,?,?,?,?)",
            (plan_id, novel_id, episode_id, script["id"], len(items),
             round(sum(i["duration_sec"] for i in items), 1),
             _s(_cfg_model(), 80), plan_bible_version, now, now),
        )
        for it in items:
            conn.execute(
                "INSERT INTO novel_shot_items (id, plan_id, seq, scene_title,"
                " title, shot_size, camera_movement, action, dialogue,"
                " character_names, position, emotion, duration_sec, mode,"
                " chapter_refs, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_new_id("nsi"), plan_id, it["seq"], it["scene_title"],
                 it["title"], it["shot_size"], it["camera_movement"],
                 it["action"], it["dialogue"],
                 json.dumps(it["character_names"], ensure_ascii=False),
                 it["position"], it["emotion"], it["duration_sec"], it["mode"],
                 json.dumps(ep_refs, ensure_ascii=False), now, now),
            )
    return get_plan(novel_id, episode_id)


# =====================================================================
# 查询
# =====================================================================
def get_plan(novel_id: str, episode_id: str) -> Optional[dict]:
    """分镜计划详情（含全部镜头，按 seq 排序；未生成过 → None）。"""
    plan = _row("SELECT * FROM novel_shot_plans WHERE episode_id = ?"
                " AND novel_id = ?", (episode_id, novel_id))
    if not plan:
        return None
    plan = dict(plan)
    plan["items"] = _decode_items(
        _rows("SELECT * FROM novel_shot_items WHERE plan_id = ?"
              " ORDER BY seq, created_at", (plan["id"],)))
    plan["versions"] = _rows(
        "SELECT version, note, created_at FROM novel_shot_plan_versions"
        " WHERE plan_id = ? ORDER BY version DESC", (plan["id"],))
    project = None
    if plan.get("project_id"):
        from . import storyboard as sb_mod
        project = sb_mod.get_project(plan["project_id"])
    plan["project"] = ({"id": project["id"], "name": project.get("name", "")}
                       if project else None)
    return plan


def _decode_items(rows: list) -> list:
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["character_names"] = json.loads(d.get("character_names") or "[]")
        except (ValueError, TypeError):
            d["character_names"] = []
        try:
            d["chapter_refs"] = json.loads(d.get("chapter_refs") or "[]")
        except (ValueError, TypeError):
            d["chapter_refs"] = []
        out.append(d)
    return out


# =====================================================================
# 镜头编辑 / 排序
# =====================================================================
_ITEM_FIELDS = ("scene_title", "title", "action", "dialogue", "position",
                "emotion", "note")


def _touch_plan_draft(plan_id: str) -> None:
    """已审核计划被改动 → 回草稿态（版本历史不变）。"""
    _write("UPDATE novel_shot_plans SET status='draft', updated_at=?"
           " WHERE id=? AND status='approved'", (_utcnow(), plan_id))


def update_item(novel_id: str, episode_id: str, item_id: str,
                fields: dict) -> dict:
    plan = _row("SELECT * FROM novel_shot_plans WHERE episode_id = ?"
                " AND novel_id = ?", (episode_id, novel_id))
    if not plan:
        raise ShotPlanError("分镜计划尚未生成：请先「生成结构化分镜」", 404)
    item = _row("SELECT * FROM novel_shot_items WHERE id = ? AND plan_id = ?",
                (item_id, plan["id"]))
    if not item:
        raise ShotPlanError("镜头不存在", 404)
    sets, args = [], []
    for f in _ITEM_FIELDS:
        if f not in fields or fields[f] is None:
            continue
        v = _s(fields[f], 500)
        if not v:
            continue
        sets.append(f"{f} = ?")
        args.append(v)
    if "shot_size" in fields:
        v = _s(fields.get("shot_size"), 10)
        if v in SHOT_SIZES:
            sets.append("shot_size = ?")
            args.append(v)
    if "camera_movement" in fields:
        v = _CAMERA_ALIAS.get(_s(fields.get("camera_movement"), 10),
                              _s(fields.get("camera_movement"), 10))
        if v in CAMERA_MOVES:
            sets.append("camera_movement = ?")
            args.append(v)
    if "duration_sec" in fields:
        try:
            v = round(max(_MIN_DUR, min(_MAX_DUR, float(fields["duration_sec"]))), 1)
        except (TypeError, ValueError):
            raise ShotPlanError("时长须为 1-15 秒的数字") from None
        sets.append("duration_sec = ?")
        args.append(v)
    if "mode" in fields:
        v = _s(fields.get("mode"), 20)
        if v in MODES:
            sets.append("mode = ?")
            args.append(v)
    if "character_names" in fields and isinstance(fields["character_names"], list):
        sets.append("character_names = ?")
        args.append(json.dumps(
            _dedupe([_s(c, 40) for c in fields["character_names"]]),
            ensure_ascii=False))
    if not sets:
        return _item_detail(item_id)
    sets.append("updated_at = ?")
    args.append(_utcnow())
    args.append(item_id)
    _write(f"UPDATE novel_shot_items SET {', '.join(sets)} WHERE id = ?",
           tuple(args))
    _sync_plan_totals(plan["id"])
    _touch_plan_draft(plan["id"])
    return _item_detail(item_id)


def _item_detail(item_id: str) -> dict:
    return _decode_items([_row("SELECT * FROM novel_shot_items WHERE id = ?",
                               (item_id,))])[0]


def _sync_plan_totals(plan_id: str) -> None:
    """镜头增删改后重算计划级统计（镜头数/总时长）。"""
    agg = _row("SELECT COUNT(*) AS c, COALESCE(SUM(duration_sec), 0) AS d"
               " FROM novel_shot_items WHERE plan_id = ?", (plan_id,))
    _write("UPDATE novel_shot_plans SET shot_count = ?, total_duration_sec = ?,"
           " updated_at = ? WHERE id = ?",
           (int((agg or {}).get("c", 0)), round(float((agg or {}).get("d", 0)), 1),
            _utcnow(), plan_id))


def reorder_item(novel_id: str, episode_id: str, item_id: str,
                 direction: str) -> dict:
    """镜头上移/下移一位（与相邻镜头交换 seq；顺序即故事顺序）。"""
    if direction not in ("up", "down"):
        raise ShotPlanError("direction 仅支持 up/down")
    plan = _row("SELECT * FROM novel_shot_plans WHERE episode_id = ?"
                " AND novel_id = ?", (episode_id, novel_id))
    if not plan:
        raise ShotPlanError("分镜计划尚未生成：请先「生成结构化分镜」", 404)
    item = _row("SELECT * FROM novel_shot_items WHERE id = ? AND plan_id = ?",
                (item_id, plan["id"]))
    if not item:
        raise ShotPlanError("镜头不存在", 404)
    seq = int(item["seq"])
    neighbor_seq = seq - 1 if direction == "up" else seq + 1
    neighbor = _row("SELECT * FROM novel_shot_items WHERE plan_id = ?"
                    " AND seq = ?", (plan["id"], neighbor_seq))
    if not neighbor:
        raise ShotPlanError("已经在边缘，无法继续移动")
    with db.transaction() as conn:
        # 先借位再交换（两行同值更新需防唯一感知，seq 无唯一约束但保持清晰）
        conn.execute("UPDATE novel_shot_items SET seq = -1, updated_at = ?"
                     " WHERE id = ?", (_utcnow(), item_id))
        conn.execute("UPDATE novel_shot_items SET seq = ?, updated_at = ?"
                     " WHERE id = ?", (seq, _utcnow(), neighbor["id"]))
        conn.execute("UPDATE novel_shot_items SET seq = ?, updated_at = ?"
                     " WHERE id = ?", (neighbor_seq, _utcnow(), item_id))
    _touch_plan_draft(plan["id"])
    return get_plan(novel_id, episode_id)


# =====================================================================
# 审核版本
# =====================================================================
def approve_plan(novel_id: str, episode_id: str, note: str = "") -> dict:
    """审核通过：整计划 + 全部镜头存为不可变版本快照，状态 → approved。"""
    plan = _row("SELECT * FROM novel_shot_plans WHERE episode_id = ?"
                " AND novel_id = ?", (episode_id, novel_id))
    if not plan:
        raise ShotPlanError("分镜计划尚未生成：请先「生成结构化分镜」", 404)
    items = _decode_items(
        _rows("SELECT * FROM novel_shot_items WHERE plan_id = ?"
              " ORDER BY seq", (plan["id"],)))
    if not items:
        raise ShotPlanError("分镜计划为空，无法审核")
    snapshot = {
        "episode_id": episode_id,
        "shot_count": len(items),
        "total_duration_sec": round(sum(i["duration_sec"] for i in items), 1),
        "items": [
            {k: it[k] for k in (
                "seq", "scene_title", "title", "shot_size", "camera_movement",
                "action", "dialogue", "character_names", "position", "emotion",
                "duration_sec", "mode", "chapter_refs")}
            for it in items
        ],
    }
    version = int(plan["version"] or 0) + 1
    now = _utcnow()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO novel_shot_plan_versions (id, plan_id, version,"
            " snapshot, note, created_at) VALUES (?,?,?,?,?,?)",
            (_new_id("npv"), plan["id"], version,
             json.dumps(snapshot, ensure_ascii=False), _s(note, 300), now),
        )
        conn.execute(
            "UPDATE novel_shot_plans SET status='approved', version=?,"
            " shot_count=?, total_duration_sec=?, updated_at=? WHERE id=?",
            (version, len(items), snapshot["total_duration_sec"], now,
             plan["id"]),
        )
    return {"version": version, "created_at": now, "snapshot": snapshot,
            "note": _s(note, 300)}


def get_plan_version(novel_id: str, episode_id: str, version: int) -> dict:
    plan = _row("SELECT * FROM novel_shot_plans WHERE episode_id = ?"
                " AND novel_id = ?", (episode_id, novel_id))
    if not plan:
        raise ShotPlanError("分镜计划尚未生成", 404)
    row = _row("SELECT * FROM novel_shot_plan_versions WHERE plan_id = ?"
               " AND version = ?", (plan["id"], int(version)))
    if not row:
        raise ShotPlanError(f"版本 v{version} 不存在", 404)
    try:
        snapshot = json.loads(row["snapshot"])
    except (ValueError, TypeError):
        raise ShotPlanError("版本快照损坏", 500) from None
    return {"version": row["version"], "note": row["note"],
            "created_at": row["created_at"], "snapshot": snapshot}


# =====================================================================
# 交接：审核通过的分镜 → 目标项目 Scene/Shot（只建数据，不启动生成）
# =====================================================================
def _project_has_outputs(project_id: str) -> dict:
    """目标项目是否已有制作成果（镜头版本/时间线/活跃任务）——有时拒绝交接。"""
    from . import storyboard as sb_mod
    scenes = sb_mod.list_scenes(project_id)
    shot_count = sum(len(sc.get("shots") or []) for sc in scenes)
    version_count = _row(
        "SELECT COUNT(*) AS c FROM shot_versions v"
        " JOIN shots s ON v.shot_id = s.id"
        " JOIN scenes sc ON s.scene_id = sc.id WHERE sc.project_id = ?",
        (project_id,))["c"]
    timeline_count = _row(
        "SELECT COUNT(*) AS c FROM timeline_items WHERE project_id = ?",
        (project_id,))["c"]
    active_jobs = _row(
        "SELECT COUNT(*) AS c FROM jobs j"
        " JOIN shots s ON j.shot_id = s.id"
        " JOIN scenes sc ON s.scene_id = sc.id"
        " WHERE sc.project_id = ?"
        " AND j.status IN ('pending', 'queued', 'submitting', 'running',"
        " 'needs_attention')",
        (project_id,))["c"]
    return {"scene_count": len(scenes), "shot_count": shot_count,
            "version_count": int(version_count or 0),
            "timeline_count": int(timeline_count or 0),
            "active_jobs": int(active_jobs or 0)}


def _ensure_project_characters(project_id: str, names: list, bible: dict) -> dict:
    """按圣经人物信息为项目自动建档角色卡（已存在则跳过）。

    返回 {标准名: character_id}——供镜头 character_ids 关联。
    """
    from . import assets as assets_mod
    existing = {c["name"]: c for c in assets_mod.list_characters(project_id)}
    bible_by_name = {c["name"]: c for c in bible.get("characters", [])}
    mapping = {}
    for name in names:
        if name in existing:
            mapping[name] = existing[name]["id"]
            continue
        info = bible_by_name.get(name) or {}
        ch = assets_mod.create_character(
            project_id,
            name=name,
            role=info.get("role") or "配角",
            appearance=info.get("appearance") or "",
            outfit=info.get("clothing") or "",
            notes=(f"来自小说改编（故事圣经）：{info.get('personality') or ''}"
                   ).strip(),
        )
        mapping[name] = ch["id"]
    return mapping


def _shot_prompt(item: dict, scene_desc: str, bible: dict) -> str:
    """组装镜头 prompt：画面动作 + 场景描述 + 出场人物外貌/服装（bible 锚）。"""
    parts = [item.get("action") or item.get("title") or ""]
    if scene_desc:
        parts.append(f"场景：{scene_desc}")
    char_map = {c["name"]: c for c in bible.get("characters", [])}
    for name in item.get("character_names") or []:
        c = char_map.get(name)
        if not c:
            continue
        traits = _dedupe([x for x in (c.get("appearance"), c.get("clothing")) if x])
        if traits:
            parts.append(f"{name}（{'，'.join(traits)}）")
    return "；".join(p for p in parts if p)[:1200]


def _scene_desc_for(scene_title: str, bible: dict) -> str:
    """场景描述匹配：精确名优先，退化到包含关系（LLM 常给场景名加修饰词）。"""
    scenes = {s["name"]: s.get("description", "") for s in bible.get("scenes", [])}
    if scene_title in scenes:
        return scenes[scene_title]
    for name, desc in scenes.items():
        if name and (name in scene_title or scene_title in name):
            return desc
    return ""


def validate_plan(novel_id: str, episode_id: str) -> dict:
    """分镜计划 ↔ 故事圣经一致性校验（只读，不阻断任何操作）。

    双向校验：
    - 分镜 → 圣经：每条镜头的 character_names 能否在圣经人物
      （标准名 + 别名）中解析；解析到别名的给出 canonical 归一提示；
      场景标题是否在圣经场景中（信息级）。
    - 圣经 → 分镜：计划生成时冻结的 bible_version 与当前版本比对
      （生成后圣经又审核过 → 人物锚定信息可能过期）；圣经处于草稿态
      （有未审核修改）也提示。

    全部结论为警告性提示：遵循「AI 结果保留原文来源 + 可人工修改 +
    可重新生成」原则，不做硬阻断——未知人名可能是 LLM 合理补充，
    由用户决定改镜头、改圣经别名还是重新生成。
    """
    novel = novel_mod.get_novel(novel_id)
    if not novel:
        raise ShotPlanError("小说不存在", 404)
    plan = _row("SELECT * FROM novel_shot_plans WHERE episode_id = ?"
                " AND novel_id = ?", (episode_id, novel_id))
    if not plan:
        raise ShotPlanError("分镜计划尚未生成", 404)
    plan = dict(plan)
    items = _decode_items(
        _rows("SELECT * FROM novel_shot_items WHERE plan_id = ?"
              " ORDER BY seq", (plan["id"],)))
    bible = bible_mod.get_bible(novel_id)
    characters = (bible or {}).get("characters", [])
    scenes = (bible or {}).get("scenes", [])
    bible_head = (bible or {}).get("bible", {})
    bible_version = int(bible_head.get("version") or 0)
    bible_status = bible_head.get("status", "")

    name_map: dict = {}  # 标准名/别名 → canonical
    for c in characters:
        canon = c.get("name") or ""
        if not canon:
            continue
        name_map[canon] = canon
        for a in c.get("aliases") or []:
            if a:
                name_map.setdefault(a, canon)
    scene_names = {s.get("name") for s in scenes if s.get("name")}

    plan_version = int(plan.get("bible_version") or 0)
    item_issues = []
    unmatched_characters: set = set()
    unmatched_scenes: set = set()
    alias_pairs: list = []
    for it in items:
        unknown, aliased = [], []
        for n in it.get("character_names") or []:
            canon = name_map.get(n)
            if canon is None:
                unknown.append(n)
                unmatched_characters.add(n)
            elif canon != n:
                aliased.append({"name": n, "canonical": canon})
                alias_pairs.append((n, canon))
        scene_unknown = []
        st = it.get("scene_title") or ""
        if st and st not in scene_names:
            scene_unknown.append(st)
            unmatched_scenes.add(st)
        if unknown or aliased or scene_unknown:
            item_issues.append({
                "item_id": it["id"], "seq": it["seq"],
                "title": it.get("title") or "",
                "unknown_characters": unknown,
                "alias_resolved": aliased,
                "unknown_scenes": scene_unknown,
            })

    warnings = []
    stale = False
    if not bible:
        warnings.append("故事圣经尚未生成：分镜人物无法与圣经锚定，"
                        "建议先运行「AI 章节分析」并「生成故事圣经」")
    else:
        if plan_version and bible_version != plan_version:
            stale = True
            warnings.append(
                f"圣经已更新（分镜生成于 v{plan_version}，当前 "
                f"v{bible_version}）：人物外貌/服装等锚定信息可能已过期，"
                "建议重新生成分镜或人工核对受影响镜头")
        # 草稿态只在「计划生成于已审核圣经、之后圣经又被改动」时提示：
        # 圣经从未审核（v0 草稿）是正常流水线状态，不算异常
        if bible_status != "approved" and plan_version > 0:
            warnings.append("圣经已回到草稿态（分镜生成后有未审核修改）："
                            "人物锚定以草稿为准，建议审核圣经后再交接/提交")
    if unmatched_characters:
        warnings.append(
            "以下人物名未在圣经中找到（LLM 补充写法或圣经改名）："
            + "、".join(sorted(unmatched_characters))
            + "。可修改镜头人物名，或把该称呼并入圣经人物别名")
    if alias_pairs:
        warnings.append(
            "以下称呼将按圣经别名解析（交接建档与提示词锚定走精确名，"
            "建议直接改用标准名）："
            + "、".join(f"{n}→{c}" for n, c in alias_pairs))
    # 场景标题是 LLM 的组织分组，几乎不会与圣经场景名精确相等——
    # 只做逐镜信息展示（item_issues.unknown_scenes），不算全局警告

    return {
        "ok": not warnings,
        "stale": stale,
        "plan_bible_version": plan_version,
        "bible_version": bible_version,
        "bible_status": bible_status,
        "checked_items": len(items),
        "unmatched_characters": sorted(unmatched_characters),
        "unmatched_scenes": sorted(unmatched_scenes),
        "item_issues": item_issues,
        "warnings": warnings,
    }


def handoff_plan(novel_id: str, episode_id: str) -> dict:
    """交接：审核通过的分镜计划 → 写入目标项目的 Scene/Shot。

    - 计划必须已审核（未审核 400）；
    - 目标项目：剧本已交接则用其项目（分镜是剧本的下一道工序，落在同一
      项目里替换剧本导入的基础分镜）；否则新建项目；
    - 目标项目已有镜头版本/时间线成果 → 400 拒绝（绝不静默覆盖，
      第一期不自动启动生成，用户可手动清理后再交接）；
    - 角色卡按圣经自动建档并关联 character_ids；
    - 重复交接不重复建项目，但按当前审核态计划重建分镜结构
      （rebuilt=True）——计划审核后改过再交接，项目分镜随之更新；
    - 幂等：plan.project_id 记录去向；项目被用户删了可重新交接。
    """
    from . import storyboard as sb_mod

    novel = novel_mod.get_novel(novel_id)
    if not novel:
        raise ShotPlanError("小说不存在", 404)
    plan = _row("SELECT * FROM novel_shot_plans WHERE episode_id = ?"
                " AND novel_id = ?", (episode_id, novel_id))
    if not plan:
        raise ShotPlanError("分镜计划尚未生成：请先「生成结构化分镜」", 404)
    if (plan["status"] or "") != "approved":
        raise ShotPlanError("分镜计划尚未审核通过：请先「审核通过」后再交接为项目分镜")
    items = _decode_items(
        _rows("SELECT * FROM novel_shot_items WHERE plan_id = ?"
              " ORDER BY seq", (plan["id"],)))
    if not items:
        raise ShotPlanError("分镜计划为空，无法交接")
    bible = bible_mod.get_bible(novel_id) or {"characters": [], "scenes": []}
    episode = _row("SELECT title, logline FROM novel_episodes WHERE id = ?",
                   (episode_id,))
    ep_title = (episode or {}).get("title", "")

    # PR-N07：幂等提交——同一计划版本重复交接直接回放上次结果，不重复重建
    #（幂等键 = 计划 id + 审核版本；版本随审核递增，草稿改动不可交接）
    idem_key = f"{plan['id']}:{int(plan.get('version') or 0)}"
    prev_commit = _row(
        "SELECT * FROM novel_handoffs WHERE idempotency_key = ?", (idem_key,))
    if prev_commit:
        if sb_mod.get_project(prev_commit["project_id"]):
            try:
                replayed = json.loads(prev_commit.get("result_json") or "{}")
            except (ValueError, TypeError):
                replayed = {}
            replayed.update({"already": True, "replayed": True})
            return replayed
        # 项目已被删除 → 幂等记录失效，按新交接重新建项目
        _write("DELETE FROM novel_handoffs WHERE id = ?", (prev_commit["id"],))

    # 目标项目解析（幂等）：计划记录的去向优先，其次剧本交接的项目
    project = None
    rebuilt = False
    if plan.get("project_id"):
        project = sb_mod.get_project(plan["project_id"])
        if not project:
            _write("UPDATE novel_shot_plans SET project_id = '' WHERE id = ?",
                   (plan["id"],))
        else:
            rebuilt = True  # 项目已在：按当前审核态计划重建其分镜结构
    if project is None:
        script = _row("SELECT project_id FROM novel_scripts WHERE episode_id = ?",
                      (episode_id,))
        if script and script.get("project_id"):
            project = sb_mod.get_project(script["project_id"])
            if project is not None:
                rebuilt = True  # 复用剧本交接建的项目（分镜是剧本下一道工序）
            else:
                _write("UPDATE novel_scripts SET project_id = '' WHERE id = ?",
                       (script["id"],))
    if project is None:
        name = f"《{novel.get('title', '')}》{ep_title}".strip()[:80] or "小说改编项目"
        project = sb_mod.create_project(name, (episode or {}).get("logline", ""))

    # 安全闸：目标项目已有制作成果 → 拒绝（不覆盖已审核成片/版本）
    outputs = _project_has_outputs(project["id"])
    if outputs["version_count"] or outputs["timeline_count"] or outputs["active_jobs"]:
        raise ShotPlanError(
            f"目标项目「{project.get('name', '')}」已有制作成果"
            f"（{outputs['version_count']} 个镜头版本、{outputs['timeline_count']} "
            f"个时间线片段、{outputs['active_jobs']} 个进行中任务），拒绝覆盖交接。"
            "请新建项目或手动清理后重试。")

    # PR-N07：只重建本改编创建的场景（origin=novel_script/novel_shot_plan）。
    # 项目里有用户手工场景 → 拒绝重建，绝不覆盖人工内容
    foreign = _rows(
        "SELECT id, title FROM scenes WHERE project_id = ?"
        " AND COALESCE(origin, '') NOT IN ('novel_script', 'novel_shot_plan')",
        (project["id"],))
    if foreign:
        titles = "、".join(f"「{s['title']}」" for s in foreign[:5])
        more = f" 等 {len(foreign)} 个" if len(foreign) > 5 else ""
        raise ShotPlanError(
            f"目标项目「{project.get('name', '')}」中有手工创建的场景"
            f"（{titles}{more}）：交接重建只清理本改编创建的场景，为避免"
            "覆盖人工内容已拒绝。请新建项目交接，或先在项目里删除这些场景。")

    char_mapping = _ensure_project_characters(
        project["id"],
        _dedupe([n for it in items for n in (it.get("character_names") or [])]),
        bible,
    )

    # 清掉本改编创建的场景（剧本交接的基础分镜 / 上次分镜交接的场景），
    # 按结构化分镜重建；用户手工场景已在上面阻断
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM scenes WHERE project_id = ? AND COALESCE(origin, '')"
            " IN ('novel_script', 'novel_shot_plan')", (project["id"],))
    scene_ids: dict = {}
    for it in items:
        sc_title = it.get("scene_title") or "新场景"
        if sc_title not in scene_ids:
            scene = sb_mod.create_scene(
                project["id"], sc_title,
                description=_scene_desc_for(sc_title, bible),
                environment=sc_title,
                origin="novel_shot_plan",
            )
            scene_ids[sc_title] = scene["id"]
        shot = sb_mod.create_shot(
            scene_ids[sc_title],
            title=it.get("title") or "",
            dialogue=it.get("dialogue") or "",
            shot_size=it.get("shot_size") or "中景",
            camera_movement=it.get("camera_movement") or "固定",
            duration_s=it.get("duration_sec") or 3,
            prompt=_shot_prompt(it, _scene_desc_for(sc_title, bible), bible),
            character_ids=[char_mapping[n] for n in (it.get("character_names") or [])
                           if n in char_mapping],
            mode=it.get("mode") or "t2v",
            # 续接模式留空：生成时默认取上一镜头末帧（工作台既有语义）
            continue_from="",
        )
        # 情绪/走位落 shot.note（create_shot 不收 note，需补写）
        note = (f"情绪：{it.get('emotion') or '-'}；走位：{it.get('position') or '-'}"
                ).strip()
        if note:
            sb_mod.update_shot(shot["id"], note=note)
        # PR-N05：回填条目 → 项目镜头映射（提交批次按此定位生成目标；
        # 重复交接时随重建刷新为最新镜头 id）
        _write("UPDATE novel_shot_items SET shot_id = ?, updated_at = ?"
               " WHERE id = ?", (shot["id"], _utcnow(), it["id"]))
    _write("UPDATE novel_shot_plans SET project_id = ?, updated_at = ? WHERE id = ?",
           (project["id"], _utcnow(), plan["id"]))
    # PR-N06：交接响应附带一致性警告（只提示不阻断——项目分镜已建成，
    # 用户可在项目里改，或回分镜页修改后重新交接）
    try:
        handoff_warnings = validate_plan(novel_id, episode_id)["warnings"]
    except Exception:  # 校验异常不阻断交接本身
        handoff_warnings = []
    result = {
        "already": rebuilt,
        "rebuilt": rebuilt,
        "project_id": project["id"],
        "project_name": project.get("name", ""),
        "scene_count": len(scene_ids),
        "shot_count": len(items),
        "character_count": len(char_mapping),
        "total_duration_sec": round(sum(i["duration_sec"] for i in items), 1),
        "warnings": handoff_warnings,
    }
    # PR-N07：记录本次交接（幂等键唯一）——同版本重复交接回放本结果
    _write(
        "INSERT OR REPLACE INTO novel_handoffs (id, plan_id, project_id,"
        " idempotency_key, result_json, created_at) VALUES (?,?,?,?,?,?)",
        (_new_id("nhof"), plan["id"], project["id"], idem_key,
         json.dumps(result, ensure_ascii=False), _utcnow()),
    )
    return result
