"""PR-N02：故事圣经——AI 分章节理解 / 人物关系 / 结构化设定沉淀。

职责边界（第一期）：
- 章节分析：调用 LLM（OpenAI 兼容端点）逐章提取 梗概/人物/场景/事件/关系，
  后台线程执行，进度可查询；失败单章标记 error，不阻塞其他章
- 合并落库：本地确定性合并（同名实体合并、别名并集、章节快照收集）
  + LLM 合并建议（跨章同一人物不同写法 → canonical/absorb，失败不阻塞）
- 实体管理：角色 / 场景 / 时间线 / 人物关系四类实体全部可编辑、增删
- 审核版本：approve 生成不可变快照（novel_bible_versions），历史可回看
- 定位原小说：所有实体用 chapters 快照（chapter_id + title + start/end），
  章节被拆分/合并/重识别后仍可定位原文位置

不触发视频生成、不触碰项目/镜头/成片；LLM 未配置时明确报错并引导去设置页。
"""
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import llm
from . import novel as novel_mod
from .db import db

# 送入 LLM 的章节正文上限（网文单章可达数万字，截断控 token 成本）
_MAX_CHAPTER_CHARS = 6000
_MAX_TOKENS = 3000
# LLM 合并建议的候选人物上限（防超长 Novel 的 payload 爆炸）
_MAX_MERGE_CANDIDATES = 120

_ROLES = ("主角", "配角", "反派", "龙套")

# 正在运行分析的小说 id 集合（同一小说同时只允许一个分析线程）
_RUNNING: set = set()
_RUN_LOCK = threading.Lock()


class BibleError(Exception):
    """故事圣经业务错误（message 面向用户，status 为 HTTP 语义）"""

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
    """任意值 → 去空白字符串（截断防异常 payload）"""
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _dedupe(items: list) -> list:
    """保序去重（空值丢弃）"""
    out = []
    for it in items:
        if it and it not in out:
            out.append(it)
    return out


def _dedupe_refs(refs: list) -> list:
    """章节快照保序去重（按 chapter_id）"""
    out = []
    seen = set()
    for r in refs:
        cid = r.get("chapter_id")
        if cid and cid not in seen:
            seen.add(cid)
            out.append(r)
    return out


def _sort_refs(refs: list) -> list:
    """章节快照按原文偏移排序（合并后保持故事顺序）"""
    return sorted(_dedupe_refs(refs), key=lambda r: (r.get("start", 0), r.get("end", 0)))


# =====================================================================
# LLM：章节分析（prompt + JSON 提取/校验 + 重试）
# =====================================================================
_ANALYZE_SYSTEM = (
    "你是小说分析与动漫前期制作专家。阅读用户给出的小说章节，提取结构化信息。"
    "只输出一个 JSON 对象，不要输出任何解释、不要用代码块包裹。格式：\n"
    "{\n"
    '  "summary": "本章梗概（80字以内）",\n'
    '  "characters": [{"name": "人物标准名", "aliases": ["其他称呼"], '
    '"role": "主角|配角|反派|龙套", "appearance": "外貌描写", '
    '"clothing": "服装", "personality": "性格", "catchphrase": "口头禅"}],\n'
    '  "scenes": [{"name": "场景名", "description": "环境要点", '
    '"atmosphere": "氛围基调"}],\n'
    '  "events": [{"title": "事件名", "summary": "一句话说明", '
    '"characters": ["涉及人物标准名"]}],\n'
    '  "relations": [{"from": "人物A标准名", "to": "人物B标准名", '
    '"type": "关系类型", "description": "关系说明"}]\n'
    "}\n"
    "规则：\n"
    "1. 人物标准名取文中正式称呼，aliases 收集其他称呼（昵称/姓氏+身份等）。\n"
    "2. 只提取本章确实出现或可合理推断的信息；没有的字段留空字符串，不要编造。\n"
    "3. scenes 只记录本章故事发生的场景，每章一般 1-3 个。\n"
    "4. events 按章节内发生顺序排列，一般 2-6 个。\n"
    "5. 人物名、场景名使用文中写法，跨章一致性由合并阶段处理。"
)

_MERGE_SYSTEM = (
    "你是小说设定整理专家。用户给你一份从各章节提取的人物清单（JSON），请你：\n"
    "1. 识别同一人物的不同写法（正式名/昵称/姓氏称呼/身份称呼），给出合并建议；\n"
    "2. 写一段 120 字以内的故事总纲。\n"
    "只输出 JSON："
    '{"merges": [{"canonical": "标准名", "absorb": ["被合并称呼"]}], '
    '"summary": "故事总纲"}\n'
    "没有可合并的就返回空数组；不要输出任何解释。"
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


def _coerce_analysis(data: dict) -> dict:
    """章节分析结果 schema 校验与容错转换（缺字段补空、错类型丢弃）。"""
    out = {
        "summary": _s(data.get("summary"), 200),
        "characters": [], "scenes": [], "events": [], "relations": [],
    }
    for c in data.get("characters") or []:
        if not isinstance(c, dict):
            continue
        name = _s(c.get("name"), 60)
        if not name:
            continue
        raw_aliases = c.get("aliases")
        aliases = [_s(a, 60) for a in raw_aliases] if isinstance(raw_aliases, list) else []
        role = _s(c.get("role"), 20)
        if role not in _ROLES:
            role = role or "配角"
        out["characters"].append({
            "name": name, "aliases": _dedupe(aliases)[:8], "role": role,
            "appearance": _s(c.get("appearance")), "clothing": _s(c.get("clothing")),
            "personality": _s(c.get("personality")),
            "catchphrase": _s(c.get("catchphrase")),
        })
    for s in data.get("scenes") or []:
        if not isinstance(s, dict):
            continue
        name = _s(s.get("name"), 60)
        if not name:
            continue
        out["scenes"].append({
            "name": name, "description": _s(s.get("description")),
            "atmosphere": _s(s.get("atmosphere"), 100),
        })
    for e in data.get("events") or []:
        if not isinstance(e, dict):
            continue
        title = _s(e.get("title"), 120)
        if not title:
            continue
        raw_chars = e.get("characters")
        chars = [_s(n, 60) for n in raw_chars] if isinstance(raw_chars, list) else []
        out["events"].append({
            "title": title, "summary": _s(e.get("summary"), 300),
            "characters": _dedupe(chars)[:12],
        })
    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            continue
        a, b = _s(r.get("from"), 60), _s(r.get("to"), 60)
        if not a or not b or a == b:
            continue
        out["relations"].append({
            "from": a, "to": b, "type": _s(r.get("type"), 40) or "相识",
            "description": _s(r.get("description")),
        })
    return out


def analyze_one_chapter(row: dict) -> dict:
    """分析单个章节（LLM 调用 + 解析失败重试一次）。失败抛 ValueError/LLMError。"""
    novel = novel_mod.get_novel(row["novel_id"], with_content=True)
    text = ((novel or {}).get("content") or "")[row["chapter_start"]:row["chapter_end"]]
    truncated = len(text) > _MAX_CHAPTER_CHARS
    if truncated:
        text = text[:_MAX_CHAPTER_CHARS]
    user = f"【章节标题】{row['chapter_title']}\n【章节正文】\n{text}"
    if truncated:
        user += "\n（正文过长已截断，请基于可见部分分析）"
    messages = [
        {"role": "system", "content": _ANALYZE_SYSTEM},
        {"role": "user", "content": user},
    ]
    raw = llm.chat(messages, temperature=0.3, max_tokens=_MAX_TOKENS)
    try:
        return _coerce_analysis(_extract_json(raw))
    except ValueError:
        # 重试一次：把失败输出回灌，明确要求只输出 JSON
        retry_messages = messages + [
            {"role": "assistant", "content": raw[:500]},
            {"role": "user", "content":
                "上一次输出无法解析为 JSON。请只输出符合上述格式的 JSON 对象，"
                "不要任何解释、不要代码块。"},
        ]
        raw2 = llm.chat(retry_messages, temperature=0.2, max_tokens=_MAX_TOKENS)
        return _coerce_analysis(_extract_json(raw2))


# =====================================================================
# 分析任务：启动 / 后台线程 / 进度
# =====================================================================
def start_analysis(novel_id: str, chapter_ids: Optional[list] = None,
                   force: bool = False) -> dict:
    """启动章节分析（后台线程）。返回 {started: n}。

    语义：默认只分析未完成（pending/error）章节；force=True 重跑指定章节。
    同一小说已有分析在跑 → 409；LLM 未配置 → 400（引导去设置页）。
    """
    novel = novel_mod.get_novel(novel_id)
    if not novel:
        raise BibleError("小说不存在", 404)
    chapters = novel_mod.list_chapters(novel_id)
    if not chapters:
        raise BibleError("还没有章节，无法分析")
    target = chapters
    if chapter_ids:
        wanted = {str(c) for c in chapter_ids}
        target = [c for c in chapters if c["id"] in wanted]
        if not target:
            raise BibleError("指定章节不存在或已被删除")
    if not force:
        done = {
            r["chapter_id"] for r in _rows(
                "SELECT chapter_id FROM novel_chapter_analyses"
                " WHERE novel_id = ? AND status = 'done'", (novel_id,)
            )
        }
        target = [c for c in target if c["id"] not in done]
        if not target:
            raise BibleError("所选章节都已分析过（如需重跑请勾选「强制重新分析」）")
    if not llm.is_configured():
        raise BibleError(
            "LLM 服务未配置：请到「模型服务」页填写 base_url 和模型名"
            "（支持 DeepSeek / 通义 / Kimi / 本地 Ollama 等 OpenAI 兼容端点）"
        )
    with _RUN_LOCK:
        if novel_id in _RUNNING:
            raise BibleError("该小说的分析正在进行中，请稍候", 409)
        _RUNNING.add(novel_id)

    now = _utcnow()
    keep = {c["id"] for c in chapters}
    stale = _rows("SELECT chapter_id FROM novel_chapter_analyses WHERE novel_id = ?",
                  (novel_id,))
    with db.transaction() as conn:
        # 清理失效章节的旧分析行（章节被删除/重识别后不在当前集合）
        for r in stale:
            if r["chapter_id"] not in keep:
                conn.execute(
                    "DELETE FROM novel_chapter_analyses WHERE novel_id = ? AND chapter_id = ?",
                    (novel_id, r["chapter_id"]),
                )
        for c in target:
            conn.execute(
                "INSERT INTO novel_chapter_analyses"
                " (id, novel_id, chapter_id, chapter_title, chapter_start, chapter_end,"
                "  status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,'pending',?,?)"
                " ON CONFLICT(novel_id, chapter_id) DO UPDATE SET"
                "  status='pending', error='', summary='', data='{}',"
                "  chapter_title=excluded.chapter_title,"
                "  chapter_start=excluded.chapter_start,"
                "  chapter_end=excluded.chapter_end, updated_at=excluded.updated_at",
                (_new_id("ana"), novel_id, c["id"], c["title"], c["start"], c["end"],
                 now, now),
            )
    threading.Thread(target=_run_analysis, args=(novel_id,), daemon=True,
                     name=f"bible-analyze-{novel_id}").start()
    return {"started": len(target)}


def _run_analysis(novel_id: str) -> None:
    """后台线程体：逐章消费 pending 行；单章失败标记 error 不中断整体。"""
    try:
        while True:
            row = _row(
                "SELECT * FROM novel_chapter_analyses"
                " WHERE novel_id = ? AND status = 'pending' ORDER BY chapter_start LIMIT 1",
                (novel_id,),
            )
            if not row:
                break
            _write("UPDATE novel_chapter_analyses SET status='running', updated_at=?"
                   " WHERE id=?", (_utcnow(), row["id"]))
            try:
                data = analyze_one_chapter(row)
                _write(
                    "UPDATE novel_chapter_analyses SET status='done', summary=?, data=?,"
                    " error='', model=?, updated_at=? WHERE id=?",
                    (data.get("summary", ""), json.dumps(data, ensure_ascii=False),
                     _s(_cfg_model(), 80), _utcnow(), row["id"]),
                )
            except Exception as e:  # 单章失败：记录错误，继续后续章
                _write(
                    "UPDATE novel_chapter_analyses SET status='error', error=?,"
                    " updated_at=? WHERE id=?",
                    (str(e)[:500], _utcnow(), row["id"]),
                )
    finally:
        with _RUN_LOCK:
            _RUNNING.discard(novel_id)


def _cfg_model() -> str:
    try:
        return llm._cfg().get("model", "")
    except Exception:
        return ""


def analysis_status(novel_id: str) -> dict:
    """分析进度（前端轮询）。"""
    items = _rows(
        "SELECT chapter_id, chapter_title, chapter_start, chapter_end, status,"
        " summary, error, updated_at FROM novel_chapter_analyses"
        " WHERE novel_id = ? ORDER BY chapter_start", (novel_id,)
    )
    with _RUN_LOCK:
        running = novel_id in _RUNNING
    counts = {"pending": 0, "running": 0, "done": 0, "error": 0}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return {
        "running": running,
        "total": len(items),
        "pending": counts["pending"] + counts["running"],
        "done": counts["done"],
        "error": counts["error"],
        "items": items,
    }


# =====================================================================
# 合并：本地确定性合并 + LLM 合并建议
# =====================================================================
def _chapter_ref(row: dict) -> dict:
    return {"chapter_id": row["chapter_id"], "title": row["chapter_title"],
            "start": row["chapter_start"], "end": row["chapter_end"]}


def _local_merge(rows: list) -> dict:
    """把各章分析结果合并为 Bible 草案（纯本地、确定性、可无 LLM 复现）。"""
    characters: dict = {}
    scenes: dict = {}
    timeline: list = []
    relations: dict = {}
    for row in rows:  # rows 已按 chapter_start 排序 = 故事顺序
        try:
            data = json.loads(row["data"] or "{}")
        except (ValueError, TypeError):
            continue
        ref = _chapter_ref(row)
        for c in data.get("characters") or []:
            cur = characters.get(c["name"])
            if cur is None:
                characters[c["name"]] = {**c, "chapters": [ref]}
            else:
                for f in ("role", "appearance", "clothing", "personality", "catchphrase"):
                    if not cur.get(f) and c.get(f):
                        cur[f] = c[f]
                cur["aliases"] = _dedupe(cur["aliases"] + c.get("aliases", []))
                cur["chapters"] = _dedupe_refs(cur["chapters"] + [ref])
        for s in data.get("scenes") or []:
            cur = scenes.get(s["name"])
            if cur is None:
                scenes[s["name"]] = {**s, "chapters": [ref]}
            else:
                for f in ("description", "atmosphere"):
                    if len(s.get(f, "")) > len(cur.get(f, "")):
                        cur[f] = s[f]
                cur["chapters"] = _dedupe_refs(cur["chapters"] + [ref])
        for e in data.get("events") or []:
            timeline.append({
                "title": e.get("title", ""), "summary": e.get("summary", ""),
                "character_names": list(e.get("characters") or []),
                "chapters": [ref],
            })
        for r in data.get("relations") or []:
            key = (r.get("from", ""), r.get("to", ""), r.get("type", ""))
            cur = relations.get(key)
            if cur is None:
                relations[key] = {**r, "chapters": [ref]}
            else:
                if len(r.get("description", "")) > len(cur.get("description", "")):
                    cur["description"] = r["description"]
                cur["chapters"] = _dedupe_refs(cur["chapters"] + [ref])
    return {
        "characters": list(characters.values()),
        "scenes": list(scenes.values()),
        "timeline": timeline,
        "relations": list(relations.values()),
    }


def _suggest_merges(characters: list) -> dict:
    """LLM 合并建议：跨章同一人物的不同写法 + 故事总纲。失败抛异常由调用方兜底。"""
    compact = [
        {"name": c["name"], "aliases": c.get("aliases", []), "role": c.get("role", "")}
        for c in characters[:_MAX_MERGE_CANDIDATES]
    ]
    raw = llm.chat(
        [{"role": "system", "content": _MERGE_SYSTEM},
         {"role": "user", "content": json.dumps(compact, ensure_ascii=False)}],
        temperature=0.2, max_tokens=1500,
    )
    data = _extract_json(raw)
    merges = []
    for m in data.get("merges") or []:
        if not isinstance(m, dict):
            continue
        canonical = _s(m.get("canonical"), 60)
        raw_absorb = m.get("absorb")
        absorb = [_s(a, 60) for a in raw_absorb] if isinstance(raw_absorb, list) else []
        if canonical and absorb:
            merges.append({"canonical": canonical, "absorb": _dedupe(absorb)})
    return {"merges": merges, "summary": _s(data.get("summary"), 300)}


def _apply_merges(merged: dict, merges: list) -> dict:
    """应用 LLM 合并建议（把 absorb 里的称呼并入 canonical 人物）。

    注意：merged 的 characters/relations 为 list（_local_merge 的产出形态），
    这里转成 dict 按键合并后再转回 list，保持与落库/序列化形态一致。
    """
    characters = {c["name"]: c for c in merged["characters"]}
    relations = {(r["from"], r["to"], r["type"]): r for r in merged["relations"]}
    for m in merges:
        canonical, absorb = m["canonical"], set(m["absorb"])
        if canonical in absorb:
            continue
        targets = [
            k for k, c in characters.items()
            if k in absorb or (set(c.get("aliases", [])) & absorb)
        ]
        if not targets:
            continue
        base = characters.get(canonical)
        if base is None:
            first = characters.pop(targets[0])
            base = dict(first)
            base["name"] = canonical
            characters[canonical] = base
        for k in targets:
            if k == canonical:
                continue
            c = characters.pop(k)
            base["aliases"] = _dedupe(base.get("aliases", []) + c.get("aliases", []) + [k])
            for f in ("role", "appearance", "clothing", "personality", "catchphrase"):
                if not base.get(f) and c.get(f):
                    base[f] = c[f]
            base["chapters"] = _dedupe_refs(base.get("chapters", []) + c.get("chapters", []))
        # 时间线人物名与关系两端随合并重映射
        for ev in merged["timeline"]:
            ev["character_names"] = _dedupe(
                [canonical if n in absorb else n for n in ev["character_names"]]
            )
        new_rel: dict = {}
        for (a, b, t), r in relations.items():
            a2 = canonical if a in absorb else a
            b2 = canonical if b in absorb else b
            if a2 == b2:
                continue
            key = (a2, b2, t)
            cur = new_rel.get(key)
            if cur is None:
                new_rel[key] = {**r, "from": a2, "to": b2}
            else:
                if len(r.get("description", "")) > len(cur.get("description", "")):
                    cur["description"] = r["description"]
                cur["chapters"] = _dedupe_refs(
                    cur.get("chapters", []) + r.get("chapters", [])
                )
        relations = new_rel
    # 章节快照统一按原文偏移排序（合并先后不影响故事顺序）
    for c in characters.values():
        c["chapters"] = _sort_refs(c.get("chapters", []))
    for r in relations.values():
        r["chapters"] = _sort_refs(r.get("chapters", []))
    merged["characters"] = list(characters.values())
    merged["relations"] = list(relations.values())
    return merged


def _auto_summary(novel: dict, rows: list, merged: dict) -> str:
    return (
        f"《{novel.get('title', '')}》已分析 {len(rows)} 章：识别人物 "
        f"{len(merged['characters'])} 名、场景 {len(merged['scenes'])} 处、"
        f"事件 {len(merged['timeline'])} 起、人物关系 {len(merged['relations'])} 条。"
        "（本地合并结果，未生成 LLM 总纲）"
    )


def _ensure_bible(novel_id: str) -> dict:
    bible = _row("SELECT * FROM novel_bibles WHERE novel_id = ?", (novel_id,))
    if bible:
        return bible
    bid = _new_id("bib")
    now = _utcnow()
    _write(
        "INSERT INTO novel_bibles (id, novel_id, status, version, summary,"
        " analysis_model, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (bid, novel_id, "draft", 0, "", _s(_cfg_model(), 80), now, now),
    )
    return _row("SELECT * FROM novel_bibles WHERE id = ?", (bid,))


def generate_bible(novel_id: str) -> dict:
    """汇总已完成章节分析 → 生成/刷新故事圣经草案（替换既有实体，版本历史保留）。"""
    novel = novel_mod.get_novel(novel_id)
    if not novel:
        raise BibleError("小说不存在", 404)
    rows = _rows(
        "SELECT * FROM novel_chapter_analyses WHERE novel_id = ? AND status = 'done'"
        " ORDER BY chapter_start", (novel_id,)
    )
    if not rows:
        raise BibleError("还没有已完成的章节分析：请先运行「AI 章节分析」")
    merged = _local_merge(rows)
    summary = ""
    try:
        suggestion = _suggest_merges(merged["characters"])
        merged = _apply_merges(merged, suggestion.get("merges") or [])
        summary = _s(suggestion.get("summary"), 300)
    except Exception:
        summary = ""  # LLM 合并失败不阻塞：本地合并结果已是可用草案
    if not summary:
        summary = _auto_summary(novel, rows, merged)

    bible = _ensure_bible(novel_id)
    now = _utcnow()
    with db.transaction() as conn:
        for table in ("bible_characters", "bible_scenes", "bible_timeline",
                      "bible_relations"):
            conn.execute(f"DELETE FROM {table} WHERE bible_id = ?", (bible["id"],))
        for c in merged["characters"]:
            conn.execute(
                "INSERT INTO bible_characters (id, bible_id, name, aliases, role,"
                " appearance, clothing, personality, catchphrase, chapters, notes,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_new_id("bch"), bible["id"], c["name"],
                 json.dumps(c.get("aliases", []), ensure_ascii=False),
                 c.get("role", ""), c.get("appearance", ""), c.get("clothing", ""),
                 c.get("personality", ""), c.get("catchphrase", ""),
                 json.dumps(c.get("chapters", []), ensure_ascii=False), "", now, now),
            )
        for s in merged["scenes"]:
            conn.execute(
                "INSERT INTO bible_scenes (id, bible_id, name, description, atmosphere,"
                " chapters, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (_new_id("bsc"), bible["id"], s["name"], s.get("description", ""),
                 s.get("atmosphere", ""),
                 json.dumps(s.get("chapters", []), ensure_ascii=False), now, now),
            )
        for seq, e in enumerate(merged["timeline"], 1):
            conn.execute(
                "INSERT INTO bible_timeline (id, bible_id, seq, title, summary,"
                " character_names, chapters, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (_new_id("btl"), bible["id"], seq, e.get("title", ""),
                 e.get("summary", ""),
                 json.dumps(e.get("character_names", []), ensure_ascii=False),
                 json.dumps(e.get("chapters", []), ensure_ascii=False), now, now),
            )
        for r in merged["relations"]:
            conn.execute(
                "INSERT INTO bible_relations (id, bible_id, from_character,"
                " to_character, relation_type, description, chapters, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (_new_id("bre"), bible["id"], r["from"], r["to"], r.get("type", ""),
                 r.get("description", ""),
                 json.dumps(r.get("chapters", []), ensure_ascii=False), now, now),
            )
        conn.execute(
            "UPDATE novel_bibles SET status='draft', summary=?, analysis_model=?,"
            " updated_at=? WHERE id=?",
            (summary, _s(_cfg_model(), 80), now, bible["id"]),
        )
    return get_bible(novel_id)


# =====================================================================
# 查询 / 实体管理 / 审核版本
# =====================================================================
_ENTITY_TABLES = {
    "characters": "bible_characters",
    "scenes": "bible_scenes",
    "timeline": "bible_timeline",
    "relations": "bible_relations",
}

# 各实体可编辑字段（白名单，防注入）
_ENTITY_FIELDS = {
    "characters": ("name", "aliases", "role", "appearance", "clothing",
                   "personality", "catchphrase", "chapters", "notes"),
    "scenes": ("name", "description", "atmosphere", "chapters"),
    "timeline": ("title", "summary", "character_names", "chapters"),
    "relations": ("from_character", "to_character", "relation_type",
                  "description", "chapters"),
}
_JSON_FIELDS = {"aliases", "chapters", "character_names"}
_NAME_FIELD = {"characters": "name", "scenes": "name",
               "timeline": "title", "relations": "from_character"}


def _decode_entity(kind: str, row: dict) -> dict:
    out = dict(row)
    for f in _JSON_FIELDS:
        if f in out:
            try:
                out[f] = json.loads(out[f] or "[]")
            except (ValueError, TypeError):
                out[f] = []
    return out


def get_bible(novel_id: str) -> Optional[dict]:
    """完整圣经（头部 + 四类实体 + 版本列表）。未生成过返回 None。"""
    bible = _row("SELECT * FROM novel_bibles WHERE novel_id = ?", (novel_id,))
    if not bible:
        return None
    bid = bible["id"]
    entities = {}
    for kind, table in _ENTITY_TABLES.items():
        order = "seq" if kind == "timeline" else "created_at"
        entities[kind] = [
            _decode_entity(kind, r) for r in _rows(
                f"SELECT * FROM {table} WHERE bible_id = ? ORDER BY {order}", (bid,)
            )
        ]
    versions = _rows(
        "SELECT version, note, created_at FROM novel_bible_versions"
        " WHERE bible_id = ? ORDER BY version DESC", (bid,)
    )
    return {"bible": dict(bible), **entities, "versions": versions}


def _entity_row(kind: str, bible_id: str, entity_id: str) -> Optional[dict]:
    table = _ENTITY_TABLES[kind]
    return _row(f"SELECT * FROM {table} WHERE id = ? AND bible_id = ?",
                (entity_id, bible_id))


def _touch_draft(bible_id: str) -> None:
    """已审核圣经被改动 → 回到草稿态（版本历史不变，提示有未审核修改）。"""
    _write(
        "UPDATE novel_bibles SET status='draft', updated_at=? WHERE id=?"
        " AND status='approved'",
        (_utcnow(), bible_id),
    )


def create_entity(kind: str, novel_id: str, fields: dict) -> dict:
    bible = _ensure_bible(novel_id)
    table = _ENTITY_TABLES[kind]
    allowed = _ENTITY_FIELDS[kind]
    cols, vals = [], []
    for f in allowed:
        if f not in fields or fields[f] is None:
            continue
        v = fields[f]
        if f in _JSON_FIELDS:
            v = json.dumps(v if isinstance(v, list) else [], ensure_ascii=False)
        else:
            v = _s(v, 2000)
            if not v:
                continue
        cols.append(f)
        vals.append(v)
    if not cols:
        raise BibleError("没有可保存的字段")
    name_field = _NAME_FIELD[kind]
    if name_field in allowed and name_field not in cols:
        raise BibleError(f"缺少必填字段：{name_field}")
    # 时间线手动新增：seq 接在既有事件之后（分析产出的顺序不受影响）
    if kind == "timeline" and "seq" not in cols:
        mx = _row("SELECT COALESCE(MAX(seq), 0) AS m FROM bible_timeline WHERE bible_id = ?",
                  (bible["id"],))
        cols.append("seq")
        vals.append(int((mx or {}).get("m", 0)) + 1)
    now = _utcnow()
    eid = _new_id("bce")
    _write(
        f"INSERT INTO {table} (id, bible_id, {', '.join(cols)}, created_at, updated_at)"
        f" VALUES (?,?,{', '.join('?' * len(cols))},?,?)",
        (eid, bible["id"], *vals, now, now),
    )
    _touch_draft(bible["id"])
    row = _entity_row(kind, bible["id"], eid)
    return _decode_entity(kind, row)


def update_entity(kind: str, novel_id: str, entity_id: str, fields: dict) -> dict:
    bible = _row("SELECT * FROM novel_bibles WHERE novel_id = ?", (novel_id,))
    if not bible:
        raise BibleError("故事圣经尚未生成", 404)
    row = _entity_row(kind, bible["id"], entity_id)
    if not row:
        raise BibleError("实体不存在", 404)
    sets, args = [], []
    for f in _ENTITY_FIELDS[kind]:
        if f not in fields or fields[f] is None:
            continue
        v = fields[f]
        if f in _JSON_FIELDS:
            v = json.dumps(v if isinstance(v, list) else [], ensure_ascii=False)
        else:
            v = _s(v, 2000)
            if not v:
                continue
        sets.append(f"{f} = ?")
        args.append(v)
    if not sets:
        return _decode_entity(kind, row)
    sets.append("updated_at = ?")
    args.append(_utcnow())
    args.append(entity_id)
    _write(f"UPDATE {_ENTITY_TABLES[kind]} SET {', '.join(sets)} WHERE id = ?",
           tuple(args))
    _touch_draft(bible["id"])
    return _decode_entity(kind, _entity_row(kind, bible["id"], entity_id))


def delete_entity(kind: str, novel_id: str, entity_id: str) -> None:
    bible = _row("SELECT * FROM novel_bibles WHERE novel_id = ?", (novel_id,))
    if not bible:
        raise BibleError("故事圣经尚未生成", 404)
    if not _entity_row(kind, bible["id"], entity_id):
        raise BibleError("实体不存在", 404)
    _write(f"DELETE FROM {_ENTITY_TABLES[kind]} WHERE id = ?", (entity_id,))
    _touch_draft(bible["id"])


def approve_bible(novel_id: str, note: str = "") -> dict:
    """审核通过：当前实体整体存为不可变版本快照，状态 → approved。"""
    bible = _row("SELECT * FROM novel_bibles WHERE novel_id = ?", (novel_id,))
    if not bible:
        raise BibleError("故事圣经尚未生成", 404)
    bid = bible["id"]
    snapshot = {
        "summary": bible["summary"],
        "characters": [
            _decode_entity("characters", r) for r in _rows(
                "SELECT * FROM bible_characters WHERE bible_id = ? ORDER BY created_at",
                (bid,))
        ],
        "scenes": [
            _decode_entity("scenes", r) for r in _rows(
                "SELECT * FROM bible_scenes WHERE bible_id = ? ORDER BY created_at",
                (bid,))
        ],
        "timeline": [
            _decode_entity("timeline", r) for r in _rows(
                "SELECT * FROM bible_timeline WHERE bible_id = ? ORDER BY seq", (bid,))
        ],
        "relations": [
            _decode_entity("relations", r) for r in _rows(
                "SELECT * FROM bible_relations WHERE bible_id = ? ORDER BY created_at",
                (bid,))
        ],
    }
    version = int(bible["version"] or 0) + 1
    now = _utcnow()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO novel_bible_versions (id, bible_id, version, snapshot, note,"
            " created_at) VALUES (?,?,?,?,?,?)",
            (_new_id("bv"), bid, version,
             json.dumps(snapshot, ensure_ascii=False), _s(note, 300), now),
        )
        conn.execute(
            "UPDATE novel_bibles SET status='approved', version=?, updated_at=?"
            " WHERE id=?",
            (version, now, bid),
        )
    return {"version": version, "created_at": now,
            "snapshot": snapshot, "note": _s(note, 300)}


def get_version(novel_id: str, version: int) -> dict:
    bible = _row("SELECT * FROM novel_bibles WHERE novel_id = ?", (novel_id,))
    if not bible:
        raise BibleError("故事圣经尚未生成", 404)
    row = _row(
        "SELECT * FROM novel_bible_versions WHERE bible_id = ? AND version = ?",
        (bible["id"], int(version)),
    )
    if not row:
        raise BibleError(f"版本 v{version} 不存在", 404)
    try:
        snapshot = json.loads(row["snapshot"])
    except (ValueError, TypeError):
        raise BibleError("版本快照损坏", 500) from None
    return {"version": row["version"], "note": row["note"],
            "created_at": row["created_at"], "snapshot": snapshot}
