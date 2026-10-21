"""PR-N03：自动分集与动漫剧本——AI 前期制作的第二道工序。

职责边界（第一期）：
- 分集大纲：LLM 基于「故事圣经 + 各章梗概」规划季播单元（集名/一句话梗概/
  覆盖章节/目标时长/节拍），草案可编辑、可审核、可保存版本；
- 动漫剧本：LLM 把分集覆盖的章节原文 + 圣经人物/场景设定，改写成与现有
  分镜工作台剧本解析器兼容的纯文本（场景一：xxx / 画面行（景别, 运动, 秒数）/
  人物：台词），生成时即用解析器校验格式，格式不符自动重试一次；
- 审核版本：分集与剧本 approve 均存不可变快照，历史可回看；审核后再改动
  自动回草稿态；
- 交接：审核通过的剧本一键「创建项目并导入场景分镜」，直通分镜工作台
  （幂等——重复交接返回同一项目，不重复建项目）；
- 定位原小说：分集 chapter_refs 为章节快照（chapter_id+title+start/end），
  章节被拆分/合并/重识别后仍可定位原文。

不自动启动视频生成（交接只建项目与分镜结构，生成仍由人工触发）；
不触碰已有项目/镜头/成片。LLM 未配置时明确报错并引导去设置页。
"""
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import llm
from . import novel as novel_mod
from . import novel_bible as bible_mod
from .db import db

# 送入 LLM 的正文上限（控 token 成本；剧本环节比分析环节更吃上下文）
_MAX_CHAPTER_CHARS = 4000
_MAX_TOTAL_CHARS = 12000
_MAX_TOKENS = 4000
# 分集时长护栏（秒）：动漫短剧单集常规区间，防 LLM 给出 10 秒或 3600 秒
_MIN_DURATION, _MAX_DURATION = 30, 300

_EPISODE_STATUSES = ("draft", "approved")
_SCRIPT_STATUSES = ("draft", "approved")


class ScriptError(Exception):
    """分集/剧本业务错误（message 面向用户，status 为 HTTP 语义）"""

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


# =====================================================================
# LLM：分集大纲（prompt + JSON 提取/校验 + 重试）
# =====================================================================
_OUTLINE_SYSTEM = (
    "你是动漫分集规划专家。用户给出小说标题、故事圣经摘要与各章梗概，"
    "请把小说规划为若干集（季播单元）。只输出 JSON，不要任何解释：\n"
    '{"episodes": [{"title": "第1话 小标题", "logline": "一句话概括（60字内）", '
    '"chapter_titles": ["第一章 竹林"], "duration_sec": 90, '
    '"beats": [{"title": "节拍名", "summary": "一句话说明"}]}]}\n'
    "规则：\n"
    "1. chapter_titles 必须从用户给出的章节标题里原样选用；每集 1-3 章，"
    "按故事顺序，不重不漏（用户要求集数时可按集数均摊，但不得跳章乱序）。\n"
    "2. 每集 3-6 个节拍，有起承转合。\n"
    "3. duration_sec 取 60-180 的整数。\n"
    "4. 标题格式「第N话 小标题」。"
)

_SCRIPT_SYSTEM = (
    "你是专业动画编剧。用户给出小说片段、人物设定与场景设定，"
    "请改写成动漫分镜剧本。严格按以下纯文本格式输出，"
    "不要输出任何解释、不要用代码块包裹：\n"
    "场景一：场景名\n"
    "画面描述（景别, 运动, 秒数）\n"
    "人物：台词\n"
    "要求：\n"
    "1. 场景 2-5 个，每场景 2-6 个镜头；景别从 远景/全景/中景/近景/特写 选，"
    "运动从 固定/推/拉/移/摇/跟 选，秒数 2-8 秒。\n"
    "2. 对白精炼口语化，保留原著关键台词；人物名必须使用设定中的标准名。\n"
    "3. 外貌服装严格遵循人物设定，保证跨镜头一致。\n"
    "4. 只依据提供的小说片段创作，不新增主要人物。"
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


def _chapter_ref(ch: dict) -> dict:
    return {"chapter_id": ch["id"], "title": ch["title"],
            "start": ch["start"], "end": ch["end"]}


def _coerce_outline(data: dict, chapters: list) -> list:
    """分集大纲 schema 校验：集标题/梗概/章节引用/时长/节拍，容错转换。

    章节引用按标题解析为快照（带原文偏移）；解析不出有效章节的集丢弃
    （无法定位原小说的集没有保留价值）。
    """
    by_title = {c["title"]: c for c in chapters}
    episodes = []
    for ep in data.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        title = _s(ep.get("title"), 120)
        if not title:
            continue
        raw_titles = ep.get("chapter_titles")
        titles = [_s(t, 200) for t in raw_titles] if isinstance(raw_titles, list) else []
        refs = []
        for t in titles:
            ch = by_title.get(t)
            if ch and all(r["chapter_id"] != ch["id"] for r in refs):
                refs.append(_chapter_ref(ch))
        if not refs:
            continue
        refs.sort(key=lambda r: r["start"])  # 按原文顺序（防 LLM 乱序）
        raw_beats = ep.get("beats")
        beats = []
        for b in raw_beats if isinstance(raw_beats, list) else []:
            if not isinstance(b, dict):
                continue
            bt = _s(b.get("title"), 120)
            if bt:
                beats.append({"title": bt, "summary": _s(b.get("summary"), 300)})
        duration = ep.get("duration_sec")
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            duration = 90
        duration = max(_MIN_DURATION, min(_MAX_DURATION, duration))
        episodes.append({
            "title": title, "logline": _s(ep.get("logline"), 300),
            "target_duration_sec": duration, "chapter_refs": refs,
            "beats": beats[:8],
        })
    return episodes


def _outline_user_payload(novel: dict, bible: dict, chapters: list,
                          done_rows: list, episode_count, target_duration) -> str:
    summaries = {r["chapter_id"]: (r["summary"] or "") for r in done_rows}
    compact_bible = {
        "summary": bible.get("bible", {}).get("summary", ""),
        "characters": [
            {"name": c["name"], "role": c.get("role", ""),
             "appearance": c.get("appearance", ""), "clothing": c.get("clothing", "")}
            for c in bible.get("characters", [])[:40]
        ],
        "scenes": [{"name": s["name"], "description": s.get("description", "")}
                   for s in bible.get("scenes", [])[:30]],
    }
    payload = {
        "novel_title": novel.get("title", ""),
        "bible": compact_bible,
        "chapters": [
            {"title": c["title"],
             "summary": summaries.get(c["id"], "") or _s(
                 novel_mod.get_chapter(novel["id"], c["id"])["content"], 120)}
            for c in chapters[:200]
        ],
    }
    if episode_count:
        payload["expected_episode_count"] = max(1, min(50, int(episode_count)))
    if target_duration:
        payload["expected_duration_sec"] = max(
            _MIN_DURATION, min(_MAX_DURATION, int(target_duration)))
    return json.dumps(payload, ensure_ascii=False)


def generate_outline(novel_id: str, episode_count=None, target_duration=None) -> list:
    """LLM 生成分集大纲草案（替换既有分集；版本/剧本随旧分集级联删除）。

    依赖：故事圣经已生成 + 至少一章分析完成（否则 400 引导）。
    """
    novel = novel_mod.get_novel(novel_id)
    if not novel:
        raise ScriptError("小说不存在", 404)
    bible = bible_mod.get_bible(novel_id)
    if not bible:
        raise ScriptError(
            "故事圣经尚未生成：请先运行「AI 章节分析」并「生成故事圣经」"
            "（分集规划以圣经为锚）")
    done_rows = _rows(
        "SELECT chapter_id, summary FROM novel_chapter_analyses"
        " WHERE novel_id = ? AND status = 'done'", (novel_id,))
    if not done_rows:
        raise ScriptError("还没有已完成的章节分析：请先运行「AI 章节分析」")
    chapters = novel_mod.list_chapters(novel_id)
    if not chapters:
        raise ScriptError("还没有章节，无法分集")
    if not llm.is_configured():
        raise ScriptError(
            "LLM 服务未配置：请到「模型服务」页填写 base_url 和模型名"
            "（支持 DeepSeek / 通义 / Kimi / 本地 Ollama 等 OpenAI 兼容端点）")

    user = _outline_user_payload(novel, bible, chapters, done_rows,
                                 episode_count, target_duration)
    messages = [
        {"role": "system", "content": _OUTLINE_SYSTEM},
        {"role": "user", "content": user},
    ]
    raw = llm.chat(messages, temperature=0.4, max_tokens=_MAX_TOKENS)
    try:
        episodes = _coerce_outline(_extract_json(raw), chapters)
    except ValueError:
        retry = messages + [
            {"role": "assistant", "content": raw[:500]},
            {"role": "user", "content":
                "上一次输出无法解析为 JSON。请只输出符合上述格式的 JSON 对象，"
                "不要任何解释、不要代码块。"},
        ]
        raw2 = llm.chat(retry, temperature=0.2, max_tokens=_MAX_TOKENS)
        try:
            episodes = _coerce_outline(_extract_json(raw2), chapters)
        except ValueError as e:
            raise ScriptError(
                f"LLM 输出无法解析为分集 JSON（重试后仍失败：{e}），请重试") from e
    if not episodes:
        raise ScriptError(
            "未能从 LLM 输出解析出有效分集（章节引用无法匹配当前章节），请重试")

    now = _utcnow()
    with db.transaction() as conn:
        # 替换式生成：旧分集连带其版本/剧本一起删（与故事圣经刷新同语义）
        conn.execute("DELETE FROM novel_episodes WHERE novel_id = ?", (novel_id,))
        for seq, ep in enumerate(episodes, 1):
            conn.execute(
                "INSERT INTO novel_episodes (id, novel_id, seq, title, logline,"
                " target_duration_sec, chapter_refs, beats, status, version,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,'draft',0,?,?)",
                (_new_id("nep"), novel_id, seq, ep["title"], ep["logline"],
                 ep["target_duration_sec"],
                 json.dumps(ep["chapter_refs"], ensure_ascii=False),
                 json.dumps(ep["beats"], ensure_ascii=False), now, now),
            )
    return list_episodes(novel_id)


# =====================================================================
# 动漫剧本生成（剧本格式文本 + 解析器校验 + 重试）
# =====================================================================
_SCENE_MARKER_RE = re.compile(
    r"场景[一二三四五六七八九十百\d]+|SCENE\s*\d+|第[一二三四五六七八九十百\d]+场",
    re.IGNORECASE,
)


def _stats(content: str) -> tuple:
    """用现有分镜工作台解析器统计 场景数/镜头数（格式兼容性的唯一裁判）。"""
    from . import storyboard as sb_mod
    scenes = sb_mod.parse_script(content or "")
    return len(scenes), sum(len(s.get("shots") or []) for s in scenes)


def _valid_script(content: str) -> tuple:
    """(ok, scene_count, shot_count, reason)"""
    if not (content or "").strip():
        return False, 0, 0, "剧本内容为空"
    if not _SCENE_MARKER_RE.search(content):
        return False, 0, 0, "剧本缺少场景标记（场景一：xxx）"
    scenes, shots = _stats(content)
    if scenes < 1 or shots < 1:
        return False, scenes, shots, "剧本没有解析出任何镜头"
    return True, scenes, shots, ""


def _script_user_payload(novel: dict, episode: dict, bible: dict) -> str:
    """剧本生成 payload：章节原文（截断控量）+ 圣经人物/场景切片 + 节拍结构。"""
    content = novel.get("content") or ""
    parts, used = [], 0
    for ref in episode.get("chapter_refs") or []:
        text = content[ref.get("start", 0):ref.get("end", 0)]
        if len(text) > _MAX_CHAPTER_CHARS:
            text = text[:_MAX_CHAPTER_CHARS] + "\n（本章过长已截断）"
        if used + len(text) > _MAX_TOTAL_CHARS:
            text = text[:max(0, _MAX_TOTAL_CHARS - used)] + "\n（总长度超限，后续章节从略）"
        if not text:
            continue
        used += len(text)
        parts.append(f"【{ref.get('title', '')}】\n{text}")
        if used >= _MAX_TOTAL_CHARS:
            break
    bible_head = bible.get("bible", {})
    payload = {
        "episode_title": episode.get("title", ""),
        "episode_logline": episode.get("logline", ""),
        "beats": episode.get("beats") or [],
        "characters": [
            {"name": c["name"], "appearance": c.get("appearance", ""),
             "clothing": c.get("clothing", ""), "catchphrase": c.get("catchphrase", "")}
            for c in bible.get("characters", [])[:40]
        ],
        "scenes": [
            {"name": s["name"], "description": s.get("description", ""),
             "atmosphere": s.get("atmosphere", "")}
            for s in bible.get("scenes", [])[:30]
        ],
        "story_summary": bible_head.get("summary", ""),
        "chapter_texts": parts,
    }
    return json.dumps(payload, ensure_ascii=False)


def generate_script(novel_id: str, episode_id: str) -> dict:
    """为分集生成动漫剧本草案（与工作台剧本解析器格式兼容）。

    已有剧本则覆盖为新的草案（状态回 draft 需重新审核，版本历史保留）。
    """
    novel = novel_mod.get_novel(novel_id, with_content=True)
    if not novel:
        raise ScriptError("小说不存在", 404)
    episode = _row("SELECT * FROM novel_episodes WHERE id = ? AND novel_id = ?",
                   (episode_id, novel_id))
    if not episode:
        raise ScriptError("分集不存在", 404)
    episode = _decode_json_cols(episode, ("chapter_refs", "beats"))
    if not episode.get("chapter_refs"):
        raise ScriptError("该分集还没有关联章节：请先编辑分集选择覆盖的章节")
    bible = bible_mod.get_bible(novel_id)
    if not bible:
        raise ScriptError(
            "故事圣经尚未生成：请先运行「AI 章节分析」并「生成故事圣经」"
            "（剧本以圣经保证人物/场景一致性）")
    if not llm.is_configured():
        raise ScriptError(
            "LLM 服务未配置：请到「模型服务」页填写 base_url 和模型名"
            "（支持 DeepSeek / 通义 / Kimi / 本地 Ollama 等 OpenAI 兼容端点）")

    messages = [
        {"role": "system", "content": _SCRIPT_SYSTEM},
        {"role": "user", "content": _script_user_payload(novel, episode, bible)},
    ]
    raw = llm.chat(messages, temperature=0.7, max_tokens=_MAX_TOKENS)
    content = (raw or "").strip()
    ok, scenes, shots, reason = _valid_script(content)
    if not ok:
        retry = messages + [
            {"role": "assistant", "content": content[:500]},
            {"role": "user", "content":
                f"上一次输出不符合剧本格式（{reason}）。请严格按指定纯文本格式"
                "重新输出：场景一：场景名 / 画面行（景别, 运动, 秒数）/ 人物：台词，"
                "不要任何解释。"},
        ]
        raw2 = llm.chat(retry, temperature=0.5, max_tokens=_MAX_TOKENS)
        content = (raw2 or "").strip()
        ok, scenes, shots, reason = _valid_script(content)
        if not ok:
            raise ScriptError(f"LLM 输出的剧本无法使用（{reason}），请重试或手动编写")

    now = _utcnow()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO novel_scripts (id, novel_id, episode_id, title, content,"
            " scene_count, shot_count, status, version, model, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,'draft',0,?,?,?)"
            " ON CONFLICT(episode_id) DO UPDATE SET"
            "  title=excluded.title, content=excluded.content,"
            "  scene_count=excluded.scene_count, shot_count=excluded.shot_count,"
            "  status='draft', model=excluded.model, updated_at=excluded.updated_at",
            (_new_id("nsc"), novel_id, episode_id, episode["title"], content,
             scenes, shots, _s(_cfg_model(), 80), now, now),
        )
    return get_script(novel_id, episode_id)


# =====================================================================
# 查询 / JSON 列解码
# =====================================================================
_JSON_COLS = ("chapter_refs", "beats")


def _decode_json_cols(row: dict, cols: tuple) -> dict:
    out = dict(row)
    for f in cols:
        if f in out:
            try:
                out[f] = json.loads(out[f] or "[]")
            except (ValueError, TypeError):
                out[f] = []
    return out


def list_episodes(novel_id: str) -> list:
    """分集列表（带剧本摘要：状态/版本/交接去向）。"""
    rows = _rows(
        "SELECT e.*, s.id AS script_id, s.title AS script_title,"
        " s.status AS script_status, s.version AS script_version,"
        " s.scene_count AS script_scene_count, s.shot_count AS script_shot_count,"
        " s.project_id AS script_project_id"
        " FROM novel_episodes e LEFT JOIN novel_scripts s ON s.episode_id = e.id"
        " WHERE e.novel_id = ? ORDER BY e.seq", (novel_id,))
    return [_decode_json_cols(r, _JSON_COLS) for r in rows]


def get_episode(novel_id: str, episode_id: str) -> Optional[dict]:
    """分集详情（含剧本全文与双方版本列表）。"""
    ep = _row("SELECT * FROM novel_episodes WHERE id = ? AND novel_id = ?",
              (episode_id, novel_id))
    if not ep:
        return None
    ep = _decode_json_cols(ep, _JSON_COLS)
    ep["script"] = None
    ep["episode_versions"] = _rows(
        "SELECT version, note, created_at FROM novel_episode_versions"
        " WHERE episode_id = ? ORDER BY version DESC", (episode_id,))
    script = _row("SELECT * FROM novel_scripts WHERE episode_id = ?", (episode_id,))
    if script:
        script = dict(script)
        script["versions"] = _rows(
            "SELECT version, note, created_at FROM novel_script_versions"
            " WHERE script_id = ? ORDER BY version DESC", (script["id"],))
        ep["script"] = script
    return ep


def get_script(novel_id: str, episode_id: str) -> dict:
    script = _row("SELECT * FROM novel_scripts WHERE episode_id = ? AND novel_id = ?",
                  (episode_id, novel_id))
    if not script:
        raise ScriptError("剧本尚未生成：请先点「生成动漫剧本」", 404)
    script = dict(script)
    script["versions"] = _rows(
        "SELECT version, note, created_at FROM novel_script_versions"
        " WHERE script_id = ? ORDER BY version DESC", (script["id"],))
    return script


# =====================================================================
# 分集 CRUD / 审核版本
# =====================================================================
_EPISODE_FIELDS = ("title", "logline", "target_duration_sec", "beats", "note")


def _touch_episode_draft(episode_id: str) -> None:
    """已审核分集被改动 → 回草稿态（版本历史不变）。"""
    _write(
        "UPDATE novel_episodes SET status='draft', updated_at=? WHERE id=?"
        " AND status='approved'",
        (_utcnow(), episode_id),
    )


def create_episode(novel_id: str, fields: dict) -> dict:
    """手动新增分集（AI 大纲之外的补充入口）。"""
    novel = novel_mod.get_novel(novel_id)
    if not novel:
        raise ScriptError("小说不存在", 404)
    title = _s(fields.get("title"), 120)
    if not title:
        raise ScriptError("缺少必填字段：title")
    # chapter_ids → 章节快照（按原文顺序）
    raw_ids = fields.get("chapter_ids")
    ids = [str(c) for c in raw_ids] if isinstance(raw_ids, list) else []
    chapters = {c["id"]: c for c in novel_mod.list_chapters(novel_id)}
    refs = [_chapter_ref(chapters[c]) for c in ids if c in chapters]
    refs.sort(key=lambda r: r["start"])
    raw_beats = fields.get("beats")
    beats = [b for b in raw_beats if isinstance(b, dict)] if isinstance(raw_beats, list) else []
    duration = fields.get("target_duration_sec") or 90
    try:
        duration = max(_MIN_DURATION, min(_MAX_DURATION, int(duration)))
    except (TypeError, ValueError):
        duration = 90
    mx = _row("SELECT COALESCE(MAX(seq), 0) AS m FROM novel_episodes WHERE novel_id = ?",
              (novel_id,))
    eid = _new_id("nep")
    now = _utcnow()
    _write(
        "INSERT INTO novel_episodes (id, novel_id, seq, title, logline,"
        " target_duration_sec, chapter_refs, beats, status, version, note,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,'draft',0,?,?,?)",
        (eid, novel_id, int((mx or {}).get("m", 0)) + 1, title,
         _s(fields.get("logline"), 300), duration,
         json.dumps(refs, ensure_ascii=False), json.dumps(beats[:8], ensure_ascii=False),
         _s(fields.get("note"), 300), now, now),
    )
    return get_episode(novel_id, eid)


def update_episode(novel_id: str, episode_id: str, fields: dict) -> dict:
    ep = _row("SELECT * FROM novel_episodes WHERE id = ? AND novel_id = ?",
              (episode_id, novel_id))
    if not ep:
        raise ScriptError("分集不存在", 404)
    sets, args = [], []
    for f in _EPISODE_FIELDS:
        if f not in fields or fields[f] is None:
            continue
        if f == "target_duration_sec":
            try:
                v = max(_MIN_DURATION, min(_MAX_DURATION, int(fields[f])))
            except (TypeError, ValueError):
                raise ScriptError("目标时长须为 30-300 秒的整数") from None
        elif f == "beats":
            v = json.dumps(
                [b for b in fields[f] if isinstance(b, dict)][:8], ensure_ascii=False)
        else:
            v = _s(fields[f], 2000)
            if not v:
                continue
        sets.append(f"{f} = ?")
        args.append(v)
    # chapter_ids 单独处理（转章节快照）
    raw_ids = fields.get("chapter_ids")
    if isinstance(raw_ids, list):
        chapters = {c["id"]: c for c in novel_mod.list_chapters(novel_id)}
        refs = [_chapter_ref(chapters[str(c)]) for c in raw_ids if str(c) in chapters]
        refs.sort(key=lambda r: r["start"])
        sets.append("chapter_refs = ?")
        args.append(json.dumps(refs, ensure_ascii=False))
    if not sets:
        return get_episode(novel_id, episode_id)
    sets.append("updated_at = ?")
    args.append(_utcnow())
    args.append(episode_id)
    _write(f"UPDATE novel_episodes SET {', '.join(sets)} WHERE id = ?", tuple(args))
    _touch_episode_draft(episode_id)
    return get_episode(novel_id, episode_id)


def delete_episode(novel_id: str, episode_id: str) -> None:
    ep = _row("SELECT * FROM novel_episodes WHERE id = ? AND novel_id = ?",
              (episode_id, novel_id))
    if not ep:
        raise ScriptError("分集不存在", 404)
    _write("DELETE FROM novel_episodes WHERE id = ?", (episode_id,))


def approve_episode(novel_id: str, episode_id: str, note: str = "") -> dict:
    """审核通过：当前分集整体存为不可变版本快照，状态 → approved。"""
    ep = _row("SELECT * FROM novel_episodes WHERE id = ? AND novel_id = ?",
              (episode_id, novel_id))
    if not ep:
        raise ScriptError("分集不存在", 404)
    ep = _decode_json_cols(ep, _JSON_COLS)
    snapshot = {
        "title": ep["title"], "logline": ep["logline"],
        "target_duration_sec": ep["target_duration_sec"],
        "chapter_refs": ep["chapter_refs"], "beats": ep["beats"],
    }
    version = int(ep["version"] or 0) + 1
    now = _utcnow()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO novel_episode_versions (id, episode_id, version, snapshot,"
            " note, created_at) VALUES (?,?,?,?,?,?)",
            (_new_id("nev"), episode_id, version,
             json.dumps(snapshot, ensure_ascii=False), _s(note, 300), now),
        )
        conn.execute(
            "UPDATE novel_episodes SET status='approved', version=?, updated_at=?"
            " WHERE id=?",
            (version, now, episode_id),
        )
    return {"version": version, "created_at": now, "snapshot": snapshot,
            "note": _s(note, 300)}


def get_episode_version(novel_id: str, episode_id: str, version: int) -> dict:
    ep = _row("SELECT * FROM novel_episodes WHERE id = ? AND novel_id = ?",
              (episode_id, novel_id))
    if not ep:
        raise ScriptError("分集不存在", 404)
    row = _row(
        "SELECT * FROM novel_episode_versions WHERE episode_id = ? AND version = ?",
        (episode_id, int(version)),
    )
    if not row:
        raise ScriptError(f"版本 v{version} 不存在", 404)
    try:
        snapshot = json.loads(row["snapshot"])
    except (ValueError, TypeError):
        raise ScriptError("版本快照损坏", 500) from None
    return {"version": row["version"], "note": row["note"],
            "created_at": row["created_at"], "snapshot": snapshot}


# =====================================================================
# 剧本编辑 / 审核版本 / 交接
# =====================================================================
def update_script(novel_id: str, episode_id: str, fields: dict) -> dict:
    script = _row("SELECT * FROM novel_scripts WHERE episode_id = ? AND novel_id = ?",
                  (episode_id, novel_id))
    if not script:
        raise ScriptError("剧本尚未生成：请先点「生成动漫剧本」", 404)
    sets, args = [], []
    content = fields.get("content")
    if content is not None:
        if not str(content).strip():
            raise ScriptError("剧本内容不能为空")
        ok, scenes, shots, reason = _valid_script(str(content))
        if not ok:
            raise ScriptError(f"剧本格式不符合工作台解析器要求：{reason}")
        sets.extend(["content = ?", "scene_count = ?", "shot_count = ?"])
        args.extend([str(content), scenes, shots])
    title = fields.get("title")
    if title is not None and _s(title, 200):
        sets.append("title = ?")
        args.append(_s(title, 200))
    if not sets:
        return get_script(novel_id, episode_id)
    sets.append("updated_at = ?")
    args.append(_utcnow())
    if (script["status"] or "") == "approved":
        sets.append("status = 'draft'")  # 审核后改动 → 回草稿态（版本历史保留）
    args.append(script["id"])
    _write(f"UPDATE novel_scripts SET {', '.join(sets)} WHERE id = ?", tuple(args))
    return get_script(novel_id, episode_id)


def approve_script(novel_id: str, episode_id: str, note: str = "") -> dict:
    script = _row("SELECT * FROM novel_scripts WHERE episode_id = ? AND novel_id = ?",
                  (episode_id, novel_id))
    if not script:
        raise ScriptError("剧本尚未生成：请先点「生成动漫剧本」", 404)
    ok, scenes, shots, reason = _valid_script(script["content"] or "")
    if not ok:
        raise ScriptError(f"剧本格式不符合工作台解析器要求：{reason}")
    episode = _row("SELECT * FROM novel_episodes WHERE id = ?", (episode_id,))
    snapshot = {
        "title": script["title"], "content": script["content"],
        "scene_count": scenes, "shot_count": shots,
        "episode_id": episode_id,
        "episode_title": (episode or {}).get("title", ""),
        "chapter_refs": _decode_json_cols(episode, ("chapter_refs",))["chapter_refs"]
        if episode else [],
    }
    version = int(script["version"] or 0) + 1
    now = _utcnow()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO novel_script_versions (id, script_id, version, snapshot,"
            " note, created_at) VALUES (?,?,?,?,?,?)",
            (_new_id("nsv"), script["id"], version,
             json.dumps(snapshot, ensure_ascii=False), _s(note, 300), now),
        )
        conn.execute(
            "UPDATE novel_scripts SET status='approved', version=?, scene_count=?,"
            " shot_count=?, updated_at=? WHERE id=?",
            (version, scenes, shots, now, script["id"]),
        )
    return {"version": version, "created_at": now, "snapshot": snapshot,
            "note": _s(note, 300)}


def get_script_version(novel_id: str, episode_id: str, version: int) -> dict:
    script = _row("SELECT * FROM novel_scripts WHERE episode_id = ? AND novel_id = ?",
                  (episode_id, novel_id))
    if not script:
        raise ScriptError("剧本尚未生成", 404)
    row = _row(
        "SELECT * FROM novel_script_versions WHERE script_id = ? AND version = ?",
        (script["id"], int(version)),
    )
    if not row:
        raise ScriptError(f"版本 v{version} 不存在", 404)
    try:
        snapshot = json.loads(row["snapshot"])
    except (ValueError, TypeError):
        raise ScriptError("版本快照损坏", 500) from None
    return {"version": row["version"], "note": row["note"],
            "created_at": row["created_at"], "snapshot": snapshot}


def handoff_script(novel_id: str, episode_id: str) -> dict:
    """交接：审核通过的剧本 → 创建项目 + 导入场景分镜（幂等）。

    - 剧本必须已审核（未审核 400）——「审核后才接入 Scene/Shot」的硬门槛；
    - 已交接过 → 直接返回既有项目（不重复建项目/不重复导入）；
    - 导入失败 → 删除刚建的空项目（不留半成品），错误上抛。
    """
    from . import storyboard as sb_mod

    novel = novel_mod.get_novel(novel_id)
    if not novel:
        raise ScriptError("小说不存在", 404)
    script = _row("SELECT * FROM novel_scripts WHERE episode_id = ? AND novel_id = ?",
                  (episode_id, novel_id))
    if not script:
        raise ScriptError("剧本尚未生成", 404)
    if (script["status"] or "") != "approved":
        raise ScriptError("剧本尚未审核：请先「审核通过」后再交接为项目")
    if script["project_id"]:
        project = sb_mod.get_project(script["project_id"])
        if project:  # 幂等：项目仍在（用户没手动删）→ 返回既有项目
            return {"already": True, "project_id": script["project_id"],
                    "project_name": project.get("name", "")}
        # 项目被用户删了：清回填记录，允许重新交接
        _write("UPDATE novel_scripts SET project_id = '' WHERE id = ?",
               (script["id"],))
    episode = _row("SELECT title, logline FROM novel_episodes WHERE id = ?",
                   (episode_id,))
    ep_title = (episode or {}).get("title", "")
    name = f"《{novel.get('title', '')}》{ep_title}".strip()[:80] or "小说改编项目"
    project = sb_mod.create_project(name, (episode or {}).get("logline", ""))
    try:
        result = sb_mod.import_script(project["id"], script["content"] or "",
                                      mode="rebuild")
    except Exception:
        try:
            sb_mod.delete_project(project["id"])  # 清理半成品，不留孤儿项目
        except Exception:
            pass  # 清理失败不掩盖原始错误（项目为空壳，用户可手动删）
        raise
    _write("UPDATE novel_scripts SET project_id = ?, updated_at = ? WHERE id = ?",
           (project["id"], _utcnow(), script["id"]))
    return {
        "already": False,
        "project_id": project["id"],
        "project_name": name,
        "scene_count": int(result.get("scene_count") or 0),
        "shot_count": int(result.get("shot_count") or 0),
    }
