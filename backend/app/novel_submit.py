"""PR-N05：预览转正与安全提交——把审核过的结构化分镜变成批量视频生成。

职责边界：
- 建批次：preview（低质量预览）/ final（正式）两种，按镜头逐个建任务；
  任务创建复用工作台 POST /shots/{id}/generate 的全部备料逻辑（续接取帧 /
  角色拼装 / 参数归一 / ComfyUI 预检），**不新建第二套队列**——任务进现有
  jobs 表，由 queue_manager 单 worker 顺序执行；
- 依赖图：单 worker 天然按入队顺序执行；续接（continuation）镜头的任务
  必须等前序镜头完成才能创建（末帧抽取在任务创建时同步做）——提交器按
  seq 串行放行；前序失败时续接镜头明确失败，独立镜头照常放行（失败隔离，
  不级联取消）；
- 预算闸：提交前按质量档位估算总耗时，超上限 400 阻断（可配置）；
- 人审门禁：final 批次要求每个镜头已有采用版本（预览 → 项目里人审 adopt
  → 再提交正式批次，复用现有 adopt 端点）；
- 取消：不杀进行中的生成（等其自然完成），未入队/排队中的条目作废；
- 重试：只重试失败条目，幂等。

后台提交器：daemon 线程轮询批次进度、放行下一个镜头；GET 列表接口也会
顺带推进一次（拉驱动，双保险）。任务创建走 run_coroutine_threadsafe 到
主事件循环（generate_shot 是 async 端点；事件循环在 main.py lifespan
注册）。测试通过替换 _create_job_for_shot 打桩。
"""
import asyncio
import json
import threading
import time
import uuid
from concurrent.futures import TimeoutError as FutTimeoutError
from datetime import datetime, timezone
from typing import Optional

from . import novel as novel_mod
from . import novel_shots as shots_mod
from . import storyboard as sb_mod
from .db import db
from .engines import ENGINES, validate_engine_mode
from .quality import QUALITY_PRESETS, normalize_generation

KINDS = ("preview", "final")
QUALITIES = ("draft", "standard", "high")
# quality 请求词 → quality.py 档位（复用既有词表，不新造）
_QUALITY_PRESET = {"draft": "lite", "standard": "standard", "high": "fine"}

SUBMISSION_ACTIVE = ("pending", "running", "cancelling")
ITEM_TERMINAL = ("done", "failed", "cancelled")

# 预算估算基准：每镜头基准耗时（秒）×（镜头时长 / 4 秒基准段）。
# 经验值（覆盖低配 CPU 到 8GB 显存的混合硬件线），只做预算闸粗估，不是承诺。
_PRESET_BASE_SEC = {"lite": 120, "standard": 360, "fine": 900}

# 跨线程提交等待上限（秒）：含续接末帧抽取 / ComfyUI 上传预检，留足余量
_SUBMIT_TIMEOUT = 600


class SubmitError(Exception):
    """提交批次业务错误（message 面向用户，status 为 HTTP 语义）"""

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


# =====================================================================
# 事件循环注册与跨线程任务创建
# =====================================================================
_LOOP: Optional[asyncio.AbstractEventLoop] = None
_POLL_INTERVAL = 2.0
_watcher_thread: Optional[threading.Thread] = None
_watcher_lock = threading.Lock()


def register_loop(loop: asyncio.AbstractEventLoop) -> None:
    """main.py lifespan 调用：登记主事件循环（跨线程提交生成任务用）。"""
    global _LOOP
    _LOOP = loop


def _create_job_for_shot(shot_id: str, gen: dict) -> dict:
    """跨线程创建单个镜头的生成任务（复用工作台 generate 端点）。

    generate_shot 是 async 端点且要 await job_queue.enqueue——调度到主
    事件循环执行并阻塞等待结果。HTTPException（400/409/500）转 SubmitError。
    """
    from fastapi import HTTPException

    from .routers.storyboard import ShotGenerate, generate_shot

    if _LOOP is None or _LOOP.is_closed():
        raise SubmitError("后台服务尚未就绪，无法提交生成任务，请稍后重试", 503)
    fut = asyncio.run_coroutine_threadsafe(
        generate_shot(shot_id, ShotGenerate(**gen)), _LOOP)
    try:
        return fut.result(timeout=_SUBMIT_TIMEOUT)
    except HTTPException as e:
        detail = e.detail
        msg = detail.get("message") if isinstance(detail, dict) else detail
        raise SubmitError(str(msg or "生成任务创建失败"), e.status_code) from e
    except FutTimeoutError:
        raise SubmitError("提交生成任务超时，请稍后在批次里重试", 504) from None
    except Exception as e:  # 未预期异常也隔离为单镜失败，不拖垮批次
        raise SubmitError(f"生成任务创建失败: {e}", 500) from e


# =====================================================================
# 参数与预算
# =====================================================================
def _gen_params(shot: dict, kind: str, quality: str, engine: str, mode: str) -> dict:
    """单镜头生成参数（ShotGenerate 字段；归一由 generate_shot 端点做）。

    preview 走既有 preview=True 通道（quality.PREVIEW_OVERRIDE：416x256 /
    17 帧 / 12 步——与画廊「预览渲染」同语义）；final 按档位预设。
    帧数按镜头时长 × 档位帧率估算（8-121 钳制）。
    """
    preset = "lite" if kind == "preview" else _QUALITY_PRESET[quality]
    p = QUALITY_PRESETS[preset]
    fps = p["fps"]
    num_frames = int(round(float(shot.get("duration_s") or 3) * fps))
    num_frames = max(8, min(121, num_frames))
    if engine == "anime":
        # AnimeDiff 护栏（与 quality.normalize_generation 同约束）：提前钳制
        # 帧数让预览/标准档可用；越界组合（如 high 档 720p）仍由归一器 400 明示
        num_frames = min(num_frames, 32)
    return {
        "engine": engine, "preset": preset, "preview": kind == "preview",
        "width": p["width"], "height": p["height"],
        "num_frames": num_frames, "fps": fps,
        "steps": p["steps"], "guidance": p["guidance"],
        "seed": -1, "mode": mode,
    }


def estimate_budget_sec(durations: list, kind: str, quality: str) -> int:
    """预算估算（秒）：Σ 档位基准 × (镜头时长 / 4s)。"""
    preset = "lite" if kind == "preview" else _QUALITY_PRESET[quality]
    base = _PRESET_BASE_SEC[preset]
    return int(round(sum(base * float(d or 3) / 4.0 for d in durations)))


# =====================================================================
# 建批次
# =====================================================================
def create_submission(novel_id: str, episode_id: str, *, kind: str,
                      quality: str = "draft", engine: str = "anime",
                      shot_item_ids=None, budget_limit_sec: int = 3600,
                      note: str = "") -> dict:
    """建提交批次（预览或正式）并放行第一个镜头。

    前置校验全部通过才落库（不留半截批次）：
    计划已审核 → 已交接项目 → 条目有镜头映射 → 无并发批次/任务 →
    逐镜头生成前校验（模式支持/参考图/采用版本/参数归一预演）→ 预算闸。
    """
    if not novel_mod.get_novel(novel_id):
        raise SubmitError("小说不存在", 404)
    if kind not in KINDS:
        raise SubmitError(f"未知批次类型: {kind}")
    if quality not in QUALITIES:
        raise SubmitError(f"未知质量档位: {quality}")
    if engine not in ENGINES:
        raise SubmitError(f"未知引擎: {engine}")

    plan = _row("SELECT * FROM novel_shot_plans WHERE episode_id = ?"
                " AND novel_id = ?", (episode_id, novel_id))
    if not plan:
        raise SubmitError("分镜计划尚未生成：请先「生成结构化分镜」", 404)
    if (plan["status"] or "") != "approved":
        raise SubmitError("分镜计划尚未审核通过：请先「审核通过」再提交生成")
    all_items = shots_mod._decode_items(
        _rows("SELECT * FROM novel_shot_items WHERE plan_id = ? ORDER BY seq",
              (plan["id"],)))
    if not all_items:
        raise SubmitError("分镜计划为空，无法提交", 400)
    if shot_item_ids:
        idset = {str(x) for x in shot_item_ids}
        selected = [it for it in all_items if it["id"] in idset]
        missing = idset - {it["id"] for it in all_items}
        if missing:
            raise SubmitError(
                f"镜头不存在: {'、'.join(sorted(missing))}", 404)
        if not selected:
            raise SubmitError("未选择任何镜头", 400)
    else:
        selected = all_items

    # 交接校验：批次只对已交接的项目镜头生效
    project_id = plan["project_id"] or ""
    if not project_id:
        raise SubmitError("分镜尚未交接为项目分镜：请先「交接为项目分镜」", 400)
    project = sb_mod.get_project(project_id)
    if not project:
        raise SubmitError("交接目标项目已被删除，请重新交接后再提交", 400)

    # 镜头映射校验（N05 轻量迁移前的旧条目 / 重新交接前会为空）
    shots_by_item: dict = {}
    problems: list = []
    for it in selected:
        shot = sb_mod.get_shot(it.get("shot_id") or "") if it.get("shot_id") else None
        if not shot:
            problems.append(
                f"#{it['seq']} {it['title']}: 分镜未交接或已重新交接，"
                "请重新「交接为项目分镜」")
            continue
        shots_by_item[it["id"]] = shot
    if problems:
        raise SubmitError("；".join(problems[:5]), 400)

    # 并发闸：同项目不双跑（批次级 + 任务级）
    dup = _row("SELECT id FROM novel_submissions WHERE project_id = ?"
               " AND status IN ('pending', 'running', 'cancelling') LIMIT 1",
               (project_id,))
    if dup:
        raise SubmitError("该项目已有进行中的生成批次，请等待完成或先取消", 409)
    busy = _row(
        "SELECT COUNT(*) AS c FROM jobs j"
        " JOIN shots s ON j.shot_id = s.id"
        " JOIN scenes sc ON s.scene_id = sc.id"
        " WHERE sc.project_id = ? AND j.status IN"
        " ('pending', 'queued', 'submitting', 'running', 'needs_attention')",
        (project_id,))["c"]
    if int(busy or 0):
        raise SubmitError(
            f"该项目还有 {int(busy)} 个进行中的生成任务，请等待完成后再提交", 409)

    # 逐镜头生成前校验（把生成端点的 400 提前到建批次时明示）
    for it in selected:
        shot = shots_by_item[it["id"]]
        label = f"#{it['seq']} {shot.get('title') or it['title']}"
        mode = it["mode"] or shot.get("mode") or "t2v"
        if not (shot.get("prompt") or "").strip():
            problems.append(f"{label}: 提示词为空")
            continue
        try:
            validate_engine_mode(engine, mode)
        except ValueError as e:
            problems.append(f"{label}: {e}")
            continue
        if mode in ("i2v", "first_last") and not (
                (shot.get("first_frame_image") or "").strip()
                or (shot.get("reference_image") or "").strip()):
            problems.append(
                f"{label}: 模式「{mode}」需要先在项目分镜里设置参考图/首帧图片")
            continue
        if kind == "final" and not (shot.get("current_version_id") or "").strip():
            problems.append(
                f"{label}: 还没有采用的版本（请先跑预览批次，在项目里"
                "人工审核并采用后再提交正式批次）")
            continue
        try:
            normalize_generation(
                _gen_params(shot, kind, quality, engine, mode),
                dict(_gen_params(shot, kind, quality, engine, mode)))
        except ValueError as e:
            problems.append(f"{label}: {e}")
            continue
    if problems:
        raise SubmitError("；".join(problems[:5]), 400)

    # 预算闸
    budget = estimate_budget_sec(
        [it.get("duration_sec") for it in selected], kind, quality)
    if budget > int(budget_limit_sec):
        raise SubmitError(
            f"预算超限：预计总耗时约 {budget} 秒，超过上限 {int(budget_limit_sec)}"
            " 秒。请减少镜头、降低质量档位或调高 budget_limit_sec", 400)

    # 落库（批次 + 条目；adopted_version_id 记提交时已采用的版本，供追溯）
    now = _utcnow()
    sub_id = _new_id("nsub")
    # PR-N06：建批次时冻结「分镜 ↔ 圣经」一致性警告（JSON 数组落库，
    # 之后圣经再改动不影响已建批次的警示内容）。校验异常不阻断提交。
    try:
        _v = shots_mod.validate_plan(novel_id, episode_id)
        warnings_json = json.dumps(_v.get("warnings", []),
                                   ensure_ascii=False)
    except Exception as e:
        print(f"[novel-submit] 批次一致性校验跳过: {e}")
        warnings_json = "[]"
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO novel_submissions (id, novel_id, episode_id, plan_id,"
            " project_id, kind, quality, engine, status, shot_count,"
            " budget_estimate_sec, budget_limit_sec, warnings, note,"
            " created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?)",
            (sub_id, novel_id, episode_id, plan["id"], project_id, kind,
             quality, engine, len(selected), budget, int(budget_limit_sec),
             warnings_json, (note or "").strip()[:300], now, now),
        )
        for it in selected:
            shot = shots_by_item[it["id"]]
            conn.execute(
                "INSERT INTO novel_submission_items (id, submission_id,"
                " shot_item_id, seq, shot_id, adopted_version_id, status,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,'pending',?,?)",
                (_new_id("nsi2"), sub_id, it["id"], it["seq"], shot["id"],
                 shot.get("current_version_id") or "", now, now),
            )
    try:
        _advance(sub_id)
    except Exception as e:  # 首镜放行失败不回滚批次（提交器会重试）
        print(f"[novel-submit] 批次 {sub_id} 首镜放行异常: {e}")
    _ensure_watcher()
    return get_submission(novel_id, episode_id, sub_id)


# =====================================================================
# 查询
# =====================================================================
def _item_view(it: dict) -> dict:
    it = dict(it)
    job = db.get_job(it["job_id"]) if it.get("job_id") else None
    it["progress"] = round(float(job["progress"]) if job and job.get("progress") is not None else 0.0, 4)
    it["stage"] = (job or {}).get("stage") or ""
    it["video_url"] = (job or {}).get("video_url") or ""
    return it


def get_submission(novel_id: str, episode_id: str, sid: str) -> Optional[dict]:
    sub = _row("SELECT * FROM novel_submissions WHERE id = ? AND novel_id = ?"
               " AND episode_id = ?", (sid, novel_id, episode_id))
    if not sub:
        return None
    sub = dict(sub)
    sub["items"] = [
        _item_view(r) for r in _rows(
            "SELECT i.*, s.title AS shot_title FROM novel_submission_items i"
            " LEFT JOIN shots s ON i.shot_id = s.id"
            " WHERE i.submission_id = ? ORDER BY i.seq", (sid,))
    ]
    done = sum(1 for it in sub["items"] if it["status"] == "done")
    sub["done_count"] = done
    sub["failed_count"] = sum(1 for it in sub["items"] if it["status"] == "failed")
    try:
        sub["warnings"] = json.loads(sub.get("warnings") or "[]")
    except (ValueError, TypeError):
        sub["warnings"] = []
    return sub


def list_submissions(novel_id: str, episode_id: str) -> list:
    subs = _rows("SELECT * FROM novel_submissions WHERE novel_id = ?"
                 " AND episode_id = ? ORDER BY created_at DESC",
                 (novel_id, episode_id))
    return [get_submission(novel_id, episode_id, s["id"]) for s in subs]


def active_submission_ids(novel_id: str = "", episode_id: str = "") -> list:
    sql = "SELECT id FROM novel_submissions WHERE status IN ('pending', 'running', 'cancelling')"
    args: list = []
    if novel_id:
        sql += " AND novel_id = ?"
        args.append(novel_id)
    if episode_id:
        sql += " AND episode_id = ?"
        args.append(episode_id)
    return [r["id"] for r in _rows(sql, tuple(args))]


# =====================================================================
# 提交器：状态同步 / 依赖放行 / 失败隔离 / 取消 / 重试
# =====================================================================
_JOB_STATUS_MAP = {
    "pending": "queued", "queued": "queued", "submitting": "queued",
    "running": "running", "needs_attention": "running",
    "done": "done", "failed": "failed", "cancelled": "cancelled",
}


def _sync_items(items: list) -> None:
    """把批次条目状态与底层 job 对齐（done/failed 由 worker 写，这里回读）。

    只同步已提交（queued/running）的条目——pending 是「本轮未提交」，
    若残留上一轮的旧 job_id（重试场景）不得据此回写状态。
    """
    for it in items:
        if it["status"] not in ("queued", "running"):
            continue
        if not it.get("job_id"):
            continue
        job = db.get_job(it["job_id"])
        if not job:
            _write("UPDATE novel_submission_items SET status='failed',"
                   " error='任务记录丢失', updated_at=? WHERE id=?",
                   (_utcnow(), it["id"]))
            continue
        new = _JOB_STATUS_MAP.get(job["status"])
        if new and new != it["status"]:
            err = (job.get("error") or "") if new in ("failed", "cancelled") else ""
            _write("UPDATE novel_submission_items SET status=?, error=?,"
                   " updated_at=? WHERE id=?",
                   (new, err, _utcnow(), it["id"]))
            it["status"], it["error"] = new, err


def _dependency_item(plan_items: list, item: dict, items_by_nsi: dict) -> tuple:
    """续接镜头的依赖：同场景内 seq 更小的最大镜头（与工作台「续接默认取
    上一镜头」语义一致——工作台按同场景 order_index 找前序）。返回
    (批次内依赖条目或 None, 依赖分镜条目或 None)。非续接镜头返回 (None, None)。"""
    if (item.get("mode") or "") != "continuation":
        return None, None
    prev_plan = None
    for cand in plan_items:
        if cand["seq"] >= item["seq"]:
            continue
        if (cand.get("scene_title") or "") != (item.get("scene_title") or ""):
            continue
        if prev_plan is None or cand["seq"] > prev_plan["seq"]:
            prev_plan = cand
    if prev_plan is None:
        return None, None
    return items_by_nsi.get(prev_plan["id"]), prev_plan


def _fail_item(item_id: str, message: str) -> None:
    _write("UPDATE novel_submission_items SET status='failed', error=?,"
           " updated_at=? WHERE id=?", (message[:300], _utcnow(), item_id))


def _submit_item(item: dict, shot: dict, sub: dict, plan_item: dict) -> None:
    """创建单镜生成任务；失败只标记该条目（失败隔离）。"""
    mode = plan_item["mode"] or shot.get("mode") or "t2v"
    gen = _gen_params(shot, sub["kind"], sub["quality"], sub["engine"], mode)
    try:
        job = _create_job_for_shot(shot["id"], gen)
    except SubmitError as e:
        _fail_item(item["id"], str(e))
        return
    _write("UPDATE novel_submission_items SET job_id=?, status='queued',"
           " error='', updated_at=? WHERE id=?",
           (job["id"], _utcnow(), item["id"]))


def _advance(sid: str) -> None:
    """推进一个批次一步：同步任务状态 → 放行下一个可提交镜头 → 结算状态。

    一次至多放行一个镜头（与单 worker 执行顺序一致，保持故事顺序）。
    幂等：可被后台线程与 GET 列表接口并发安全地重复调用。
    """
    sub = _row("SELECT * FROM novel_submissions WHERE id = ?", (sid,))
    if not sub or sub["status"] not in SUBMISSION_ACTIVE:
        return
    plan_items = shots_mod._decode_items(
        _rows("SELECT * FROM novel_shot_items WHERE plan_id = ? ORDER BY seq",
              (sub["plan_id"],)))
    items = [dict(r) for r in _rows(
        "SELECT * FROM novel_submission_items WHERE submission_id = ?"
        " ORDER BY seq", (sid,))]
    _sync_items(items)

    plan_by_id = {p["id"]: p for p in plan_items}

    if sub["status"] == "cancelling":
        # 取消语义：不杀进行中的生成（running 等其自然完成）；
        # 未入队的作废，排队中的撤下队列（worker 还没开始跑）
        for it in items:
            if it["status"] == "pending":
                _write("UPDATE novel_submission_items SET status='cancelled',"
                       " error='批次已取消', updated_at=? WHERE id=?",
                       (_utcnow(), it["id"]))
                it["status"] = "cancelled"
            elif it["status"] == "queued" and it.get("job_id"):
                from .queue_manager import job_queue
                job_queue.cancel(it["job_id"])
    else:
        items_by_nsi = {it["shot_item_id"]: it for it in items}
        for it in items:
            if it["status"] != "pending":
                continue
            plan_item = plan_by_id.get(it["shot_item_id"])
            shot = sb_mod.get_shot(it.get("shot_id") or "")
            if not shot:
                _fail_item(it["id"], "镜头已被删除或重新交接，请重新提交批次")
                continue
            dep_item, dep_plan = _dependency_item(
                plan_items, plan_item or {}, items_by_nsi)
            if dep_plan is not None and dep_item is None:
                # 依赖镜头不在本批次（部分提交）：看它是否已有采用版本
                dep_shot = sb_mod.get_shot(dep_plan.get("shot_id") or "")
                if dep_shot and (dep_shot.get("current_version_id") or "").strip():
                    _submit_item(it, shot, sub, plan_item)
                    break
                _fail_item(it["id"],
                           f"续接依赖的前序镜头「{dep_plan['title']}」没有已采用的版本，无法续接")
                continue
            if dep_item is not None:
                if dep_item["status"] == "done":
                    _submit_item(it, shot, sub, plan_item)
                    break
                if dep_item["status"] in ("failed", "cancelled"):
                    _fail_item(it["id"],
                               f"前序镜头「{dep_plan['title'] if dep_plan else ''}」未成功完成，无法续接")
                    continue
                break  # 依赖还在排队/生成中，等待下一轮
            # 独立镜头（t2v/i2v/first_last）：直接放行
            _submit_item(it, shot, sub, plan_item)
            break

    # 结算批次状态（先再同步一次：本轮放行的任务可能已即时终态）
    items = [dict(r) for r in _rows(
        "SELECT * FROM novel_submission_items WHERE submission_id = ?"
        " ORDER BY seq", (sid,))]
    _sync_items(items)
    active = [it for it in items if it["status"] not in ITEM_TERMINAL]
    now = _utcnow()
    if not active:
        done = sum(1 for it in items if it["status"] == "done")
        if sub["status"] == "cancelling":
            final = "cancelled"
        elif done == len(items) and items:
            final = "done"
        elif done > 0:
            final = "partial"
        else:
            final = "failed"
        _write("UPDATE novel_submissions SET status = ?, updated_at = ?"
               " WHERE id = ?", (final, now, sid))
    elif sub["status"] == "pending":
        _write("UPDATE novel_submissions SET status = 'running',"
               " updated_at = ? WHERE id = ?", (now, sid))


def advance_submission(sid: str) -> None:
    """公开推进入口（GET 轮询与后台线程共用；异常只打日志不外抛）。"""
    try:
        _advance(sid)
    except Exception as e:
        print(f"[novel-submit] 批次 {sid} 推进失败: {e}")


# =====================================================================
# 取消 / 重试
# =====================================================================
def _guard_submission(novel_id: str, episode_id: str, sid: str) -> dict:
    if not novel_mod.get_novel(novel_id):
        raise SubmitError("小说不存在", 404)
    sub = _row("SELECT * FROM novel_submissions WHERE id = ? AND novel_id = ?"
               " AND episode_id = ?", (sid, novel_id, episode_id))
    if not sub:
        raise SubmitError("提交批次不存在", 404)
    return sub


def cancel_submission(novel_id: str, episode_id: str, sid: str) -> dict:
    sub = _guard_submission(novel_id, episode_id, sid)
    if sub["status"] not in ("pending", "running"):
        raise SubmitError("批次已结束或已取消，无需取消", 400)
    _write("UPDATE novel_submissions SET status='cancelling', updated_at=?"
           " WHERE id=?", (_utcnow(), sid))
    advance_submission(sid)
    return get_submission(novel_id, episode_id, sid)


def retry_submission(novel_id: str, episode_id: str, sid: str) -> dict:
    sub = _guard_submission(novel_id, episode_id, sid)
    if sub["status"] not in ("partial", "failed"):
        raise SubmitError("只有「部分完成」或「失败」的批次可以重试", 400)
    failed = _row(
        "SELECT COUNT(*) AS c FROM novel_submission_items"
        " WHERE submission_id = ? AND status IN ('failed', 'cancelled')",
        (sid,))["c"]
    if not int(failed or 0):
        raise SubmitError("没有失败的镜头可重试", 400)
    now = _utcnow()
    _write("UPDATE novel_submission_items SET status='pending', error='',"
           " job_id='', updated_at=? WHERE submission_id=? AND status IN"
           " ('failed', 'cancelled')", (now, sid))
    _write("UPDATE novel_submissions SET status='running', updated_at=?"
           " WHERE id=?", (now, sid))
    advance_submission(sid)
    return get_submission(novel_id, episode_id, sid)


# =====================================================================
# 后台提交器线程
# =====================================================================
def _watcher_loop() -> None:
    while True:
        time.sleep(_POLL_INTERVAL)
        try:
            for sid in active_submission_ids():
                advance_submission(sid)
        except Exception as e:  # 线程不能死
            print(f"[novel-submit] 提交器轮询失败: {e}")


def _ensure_watcher() -> None:
    global _watcher_thread
    with _watcher_lock:
        if _watcher_thread and _watcher_thread.is_alive():
            return
        _watcher_thread = threading.Thread(
            target=_watcher_loop, daemon=True, name="novel-submit")
        _watcher_thread.start()
