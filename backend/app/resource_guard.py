"""生成资源守卫：按任务参数估算峰值显存/内存，与实测余量比对后决定放行/降档/拒绝。

设计取舍（重要，改之前先读）：

1. **只做粗粒度、可解释、偏保守的估算**，不追求精确模型。视频扩散的峰值
   显存受注意力实现、分块策略、模型卸载影响，同一参数在不同 ComfyUI 版本
   上能差一倍。猜高了的代价是自动降一档（用户看得见、能改回去），猜低了的
   代价是 OOM 拖死整机——所以一律偏保守。
2. **需求拆成两段**：固定部分（权重 + 框架开销，不随分辨率变）+ 变量部分
   （激活/attention，随「像素帧数」线性增长）。只按总像素线性缩放是错的：
   降到 256×256 时权重照样要占满，那样估出来的需求会把本机说成"能跑"。
3. **ComfyUI 通道与进程内通道的判据不同**：
   - ComfyUI 自己会按需卸载模型腾显存，所以用「显卡总容量」判能不能装下，
     空闲显存只作为提示（外部程序占满了 ComfyUI 也会 OOM，但不该由我们
     抢先拒绝——它会自己卸载）。
   - 进程内通道（h3_native / anime / wan22）没法赶走别的进程，只能用
     「当前空闲显存」判。
   - 内存（RAM）两边都用「系统可用内存」判，因为那是真实争抢的资源。
4. **降档只在入队前做**。ComfyUI 的工作流快照在入队时冻结（PR-06：输出
   可复现），运行时改分辨率会破坏这个不变量，所以 ComfyUI 任务在运行前
   只做「还装得下吗」的复核，不做降档。
5. 任何探测失败都**放行**（fail-open）：没有数据不拦人，只拦确认不够的。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import hw_probe

# 参考点：标准档 832×480 / 81 帧。各引擎在此配置下的经验峰值写在下面。
REF_PIXEL_FRAMES = 832 * 480 * 81

# (固定显存, 变量显存, 固定内存, 变量内存)，单位 GB，参考点为 REF_PIXEL_FRAMES。
# 变量部分是该参考点下的激活开销，按像素帧数线性缩放。
# 数值是「能跑起来」的经验峰值（含少量冗余），不是理论最小值；改大=更保守。
ENGINE_PROFILES = {
    # ComfyUI 通用工作流（Wan TI2V-5B 一类）：ComfyUI 会按节点粒度卸载，
    # 峰值约等于「最大单个模块 + 激活」，不是全部权重的和
    "comfyui": (3.0, 3.5, 6.0, 4.0),
    # MiniMax H3 走 ComfyUI + FreeVideo 节点
    "h3": (6.0, 4.0, 22.0, 4.0),
    # H3 原生（diffusers）：文本编码器也常驻内存，固定内存最高
    "h3_native": (8.0, 4.0, 32.0, 4.0),
    "anime": (2.5, 2.0, 5.0, 2.0),
    "wan22": (5.0, 2.5, 22.0, 4.0),
}
DEFAULT_PROFILE = (4.0, 3.0, 12.0, 4.0)

# 降档阶梯：分辨率缩放比例 × 帧数。只在「比当前小」的方向上找，
# 帧数取 8n+1（Wan / H3 的时间维要求），256 是 API 校验下限。
SCALE_LADDER = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.42, 0.34, 0.28)
FRAME_LADDER = (121, 113, 97, 81, 65, 49, 33, 25, 17, 9)
MIN_SIDE = 256

# 降档的「可用下限」：低于这个规格的产物已经不是用户要的东西了（比如
# 480p 被降到 256×144），宁可明确拒绝并告诉他换引擎，也不要静默产出废片。
MIN_RESULT_WIDTH = 384
MIN_RESULT_PIXELS = 480 * 270
MIN_RESULT_FRAMES = 17

# 余量：显存留 1GB 给 CUDA context / 驱动，内存留 2GB 给系统本身。
VRAM_RESERVE_GB = 1.0
RAM_RESERVE_GB = 2.0
# 绝对地板：可用内存低于它就不许开跑（系统已经在换页，谁都救不了）
RAM_FLOOR_GB = 1.0
# ComfyUI 空闲显存低于此值时提示（不拦）：说明有外部程序占着卡。
COMFY_FREE_WARN_GB = 0.8


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def guard_mode() -> str:
    """enforce（默认，降档+拒绝）| warn（只提示不拦）| off（完全关闭）。"""
    return (os.environ.get("AIVIDEO_RESOURCE_GUARD") or "enforce").strip().lower()


@dataclass
class Decision:
    ok: bool
    params: dict
    note: str = ""            # 给用户看的一句话（空 = 无需提示）
    reason: str = ""          # 拒绝时的详细原因
    changed: bool = False     # 参数是否被降档修改过
    available: dict = field(default_factory=dict)
    need: dict = field(default_factory=dict)


# ---------------------------------------------------------------- 需求估算
def _is_comfy(engine_id: str) -> bool:
    """是否走 ComfyUI 通道（以引擎注册表为准，导入失败退回显式名单）。"""
    try:
        from .engines import is_comfy_engine

        return bool(is_comfy_engine(engine_id))
    except Exception:  # noqa: BLE001
        return str(engine_id or "") in ("comfyui", "h3")


def _profile(engine_id: str) -> tuple:
    return ENGINE_PROFILES.get(str(engine_id or ""), DEFAULT_PROFILE)


def pixel_frames(params: dict) -> int:
    try:
        return max(
            int(params.get("width") or 0)
            * int(params.get("height") or 0)
            * int(params.get("num_frames") or 0),
            1,
        )
    except (TypeError, ValueError):
        return 1


def estimate(engine_id: str, params: dict) -> dict:
    """估算峰值需求（GB）。返回 {vram, ram, scale}；min_vram 由 `_with_min_vram` 补。"""
    fixed_v, var_v, fixed_r, var_r = _profile(engine_id)
    scale = pixel_frames(params) / REF_PIXEL_FRAMES
    # 变量部分随像素帧数线性走；限制在 0.1~4 之间，避免极端参数把估算推到离谱值。
    scale = min(max(scale, 0.1), 4.0)
    return {
        "vram": round(fixed_v + var_v * scale, 1),
        "ram": round(fixed_r + var_r * scale, 1),
        "scale": round(scale, 3),
        "min_vram": 0.0,
        "fixed_vram": fixed_v,
        "fixed_ram": fixed_r,
    }


def _with_min_vram(need: dict, min_vram_gb: float) -> dict:
    """把工作流自声明的显存下限记进 need（单独判定，不参与估算值）。

    不能直接并进 need["vram"]：那是「估算需求」，判定时要再加安全余量；
    而 min_vram_gb 是模板作者写死的「低于此值必然跑不动」，本身就是门槛，
    再加余量会把「正好 8GB 的卡跑 8GB 工作流」误判成不够。
    """
    if min_vram_gb and min_vram_gb > 0:
        return {**need, "min_vram": round(float(min_vram_gb), 1)}
    return need


# ---------------------------------------------------------------- 余量探测
def _comfy_avail(comfy_url: str) -> dict:
    """从 ComfyUI 的 /system_stats 读显存容量/空闲（ComfyUI 进程视角）。"""
    from .comfyui import ComfyUIClient

    stats = ComfyUIClient(comfy_url or None).system_stats()
    devices = stats.get("devices") or []
    if not devices:
        return {}
    dev = devices[0]
    total = float(dev.get("vram_total") or 0) / 2**30
    free = float(dev.get("vram_free") or 0) / 2**30
    system = stats.get("system") or {}
    ram = float(system.get("ram_free") or 0) / 2**30
    ram_total = float(system.get("ram_total") or 0) / 2**30
    return {
        "vram_total": round(total, 1),
        "vram_free": round(free, 1),
        "ram": round(ram, 1) if ram else -1.0,
        "ram_total": round(ram_total, 1) if ram_total else -1.0,
        "source": "comfyui",
    }


def _local_avail() -> dict:
    """本机视角：nvidia-smi 总量 + 空闲，psutil 可用内存。"""
    return {
        "vram_total": round(hw_probe.vram_total_gb(), 1),
        "vram_free": round(hw_probe.free_vram_gb(), 1),
        "ram": round(hw_probe.free_ram_gb(), 1),
        "ram_total": round(hw_probe.ram_total_gb(), 1),
        "source": "local",
    }


def available(engine_id: str, comfy_url: str = "") -> dict:
    """当前可用资源。任何探测失败都返回 -1（调用方跳过对应检查）。"""
    if _is_comfy(engine_id):
        try:
            got = _comfy_avail(comfy_url)
            if got:
                # ComfyUI 只报它自己看到的显存；内存缺失时补本机数据
                if got.get("ram", -1) < 0:
                    got["ram"] = round(hw_probe.free_ram_gb(), 1)
                if got.get("ram_total", -1) < 0:
                    got["ram_total"] = round(hw_probe.ram_total_gb(), 1)
                return got
        except Exception:  # noqa: BLE001 - 探测失败不拦人
            pass
    try:
        return _local_avail()
    except Exception:  # noqa: BLE001
        return {"vram_total": -1.0, "vram_free": -1.0, "ram": -1.0,
                "ram_total": -1.0, "source": "none"}


# ---------------------------------------------------------------- 判据
def _fits(engine_id: str, need: dict, avail: dict) -> tuple:
    """返回 (是否够用, 说明)。-1 视为无数据 → 跳过该项检查。

    内存分两条规则，和显存同理：
    - 系统可用内存低于地板（1GB）→ 一律拒绝，系统已经在换页了，谁跑都炸；
    - ComfyUI 通道按**总量**判能不能装下（它是独立进程，吃紧会走 pagefile）；
    - 进程内通道按**当前可用**判（跟本进程抢内存，没得让）。
    """
    is_comfy = _is_comfy(engine_id)
    ram_avail = float(avail.get("ram", -1) or -1)
    ram_total = float(avail.get("ram_total", -1) or -1)

    if ram_avail >= 0 and ram_avail < RAM_FLOOR_GB:
        return False, (
            f"系统可用内存仅 {ram_avail:.1f}GB（低于 {RAM_FLOOR_GB:g}GB 地板），"
            "此时启动生成会直接拖垮系统"
        )

    if is_comfy:
        if ram_total > 0 and ram_total < need["ram"] + RAM_RESERVE_GB:
            return False, (
                f"内存共 {ram_total:.0f}GB，装不下本任务需求的 {need['ram']:.1f}GB"
                f"（含 {RAM_RESERVE_GB:g}GB 余量）"
            )
    elif ram_avail >= 0 and ram_avail < need["ram"] + RAM_RESERVE_GB:
        return False, (
            f"可用内存 {ram_avail:.1f}GB，低于本任务需求 {need['ram']:.1f}GB"
            f"（含 {RAM_RESERVE_GB:g}GB 系统余量）"
        )

    total = float(avail.get("vram_total", -1) or -1)

    # 工作流声明的硬下限：低于它降分辨率也没用，直接判不够
    floor = float(need.get("min_vram") or 0)
    if floor > 0 and total > 0 and total < floor:
        return False, f"当前工作流要求至少 {floor:g}GB 显存，本机显卡只有 {total:.0f}GB"

    if total <= 0:
        return True, ""  # 无独显（CPU 路径）：显存不参与判断
    if is_comfy:
        # ComfyUI 会按需卸载自己的模型，判「装不装得下」用总容量
        if total < need["vram"] + VRAM_RESERVE_GB:
            return False, (
                f"显卡共 {total:.0f}GB 显存，装不下本任务的 {need['vram']:.1f}GB"
                f"（含 {VRAM_RESERVE_GB:g}GB 余量）"
            )
        return True, ""
    free = float(avail.get("vram_free", -1) or -1)
    if free >= 0 and free < need["vram"] + VRAM_RESERVE_GB:
        return False, (
            f"显存空闲 {free:.1f}GB，低于本任务需求 {need['vram']:.1f}GB"
            f"（含 {VRAM_RESERVE_GB:g}GB 余量）"
        )
    if total < need["vram"] + VRAM_RESERVE_GB:
        return False, f"显卡共 {total:.0f}GB 显存，装不下本任务的 {need['vram']:.1f}GB"
    return True, ""


def _snap(value: float) -> int:
    """对齐到 16 的倍数，且不低于 MIN_SIDE。"""
    return max(MIN_SIDE, int(round(value / 16.0)) * 16)


def _downgrade(engine_id: str, params: dict, avail: dict, min_vram_gb: float):
    """从大到小找第一个装得下、且仍算「可用规格」的组合；找不到返回 None。"""
    cur_w = int(params.get("width") or 0) or MIN_SIDE
    cur_h = int(params.get("height") or 0) or MIN_SIDE
    cur_f = int(params.get("num_frames") or 0) or MIN_RESULT_FRAMES
    current = (cur_w, cur_h, cur_f)

    candidates = []
    for scale in SCALE_LADDER:
        w, h = _snap(cur_w * scale), _snap(cur_h * scale)
        if w < MIN_RESULT_WIDTH or w * h < MIN_RESULT_PIXELS:
            continue
        for frames in FRAME_LADDER:
            if frames > cur_f or frames < MIN_RESULT_FRAMES:
                continue
            combo = (w, h, frames)
            if combo == current:
                continue
            candidates.append(combo)
    # 排序：**先保时长，再降分辨率**。镜头时长由分镜定死（改了就跟剪辑对
    # 不上），分辨率降了还能靠后续升档补回来。所以同等条件下优先试「帧数
    # 不变、分辨率更小」的组合，帧数降到下一档后重新从最大分辨率试。
    candidates.sort(key=lambda c: (c[2], c[0] * c[1]), reverse=True)

    for w, h, frames in candidates:
        trial = {**params, "width": w, "height": h, "num_frames": frames}
        need = _with_min_vram(estimate(engine_id, trial), min_vram_gb)
        ok, _ = _fits(engine_id, need, avail)
        if ok:
            return {"width": w, "height": h, "num_frames": frames, "need": need}
    return None


def _describe_change(params: dict, new: dict) -> str:
    def _fmt(p):
        return f"{p['width']}×{p['height']} / {p['num_frames']} 帧"

    return f"{_fmt(params)} → {_fmt(new)}"


def _workflow_min_vram(engine_id: str, params: dict) -> float:
    """读当前工作流模板自声明的显存下限（模板 _meta.min_vram_gb）。

    直接读预设 JSON，不走 `comfyui.resolve_template_for`——那条路径会把
    `requests` 一起拖进来，安装不全时整个检查静默失效（这里是最该生效的
    场景，不能因为一个可选依赖就失灵）。
    """
    try:
        from .config import BASE_DIR
        from .engines import get_engine

        if not _is_comfy(engine_id):
            return 0.0
        presets = getattr(get_engine(engine_id).meta, "workflow_presets", None) or {}
        preset_id = presets.get(params.get("mode") or "t2v")
        if not preset_id:
            return 0.0
        path = Path(BASE_DIR) / "comfy_workflows" / "presets" / f"{preset_id}.json"
        if not path.is_file():
            return 0.0
        meta = json.loads(path.read_text(encoding="utf-8")).get("_meta") or {}
        return float(meta.get("min_vram_gb") or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------- 主入口
def plan(
    engine_id: str,
    params: dict,
    *,
    comfy_url: str = "",
    allow_downgrade: bool = True,
) -> Decision:
    """入队前规划：够用放行、不够降档、降到底仍不够则拒绝。

    params 必须是**已经具体化**的参数（normalize_generation 之后），
    因为降档要直接改 width/height/num_frames。
    """
    params = dict(params)
    if guard_mode() == "off":
        return Decision(ok=True, params=params)

    min_vram = _workflow_min_vram(engine_id, params)
    avail = available(engine_id, comfy_url or params.get("comfy_url") or "")
    need = _with_min_vram(estimate(engine_id, params), min_vram)

    # 工作流硬下限（模板 _meta.min_vram_gb）先判：降分辨率也满足不了，
    # 别白试一轮降档阶梯，也别把「内存不够」的原因说成「显存硬要求」。
    total = float(avail.get("vram_total", -1) or -1)
    floor = float(need.get("min_vram") or 0)
    if floor and total > 0 and total < floor:
        reason = (
            f"当前工作流要求至少 {floor:g}GB 显存，本机显卡只有 {total:.0f}GB。"
            "这是工作流声明的硬要求，降低分辨率无法满足——"
            "请改用适配本机显存的工作流（如 Wan TI2V-5B）或换一台机器。"
        )
        if guard_mode() == "warn":
            return Decision(ok=True, params=params, note=f"（仅提示，未拦截）{reason}",
                            available=avail, need=need)
        return Decision(ok=False, params=params, reason=reason,
                        available=avail, need=need)

    ok, why = _fits(engine_id, need, avail)
    if ok:
        notes = []
        if _is_comfy(engine_id):
            free_v = float(avail.get("vram_free", -1) or -1)
            if 0 <= free_v < COMFY_FREE_WARN_GB:
                notes.append(
                    f"当前显存空闲仅 {free_v:.1f}GB，可能有其他程序占用显卡；"
                    "ComfyUI 会尝试卸载自身模型腾空间，若失败请关闭占用显存的程序"
                )
            ram_avail = float(avail.get("ram", -1) or -1)
            if ram_avail >= 0 and ram_avail < need["ram"]:
                # ComfyUI 通道按总量放行，但可用量低于需求时大概率会开始换页
                notes.append(
                    f"可用内存 {ram_avail:.1f}GB 低于本任务需求的 {need['ram']:.1f}GB，"
                    "生成期间可能变慢（系统换页）；关掉占内存的程序会稳一些"
                )
        return Decision(ok=True, params=params, note="；".join(notes),
                        available=avail, need=need)

    if guard_mode() == "warn":
        return Decision(
            ok=True, params=params, note=f"（仅提示，未拦截）{why}",
            available=avail, need=need,
        )

    if allow_downgrade:
        got = _downgrade(engine_id, params, avail, min_vram)
        if got:
            changed = _describe_change(params, got)
            new_params = {
                **params,
                "width": got["width"],
                "height": got["height"],
                "num_frames": got["num_frames"],
                # 降档后原来选的画质档位已不成立，清空避免 UI 继续显示「极致」
                "preset": "",
            }
            note = (
                f"{why}。已自动降档 {changed}，避免撑爆显存/内存；"
                "需要原分辨率请关闭占用显存/内存的程序后重新生成"
            )
            return Decision(
                ok=True, params=new_params, note=note, changed=True,
                available=avail, need=got["need"],
            )

    reason = (
        f"{why}。已降到最低可用档（宽 {MIN_RESULT_WIDTH}px / {MIN_RESULT_FRAMES} 帧）仍不够——"
        "再降下去产物就没法用了，所以停在这里。"
        "请关闭其他程序（浏览器/游戏/其他 AI 工具）后重试，或换用更轻的引擎/画质档位。"
    )
    return Decision(ok=False, params=params, reason=reason, available=avail, need=need)


def wait_for_headroom(
    engine_id: str,
    params: dict,
    *,
    comfy_url: str = "",
    timeout_s: float = None,
    poll_s: float = 3.0,
    on_wait=None,
) -> Decision:
    """运行前等待余量释放（只等，不降档）。给 ComfyUI 通道用——快照已冻结。

    on_wait(elapsed) 每轮回调一次，供队列把「等待中」写进任务状态。
    """
    timeout_s = _env_float("AIVIDEO_RESOURCE_WAIT_S", 120.0 if timeout_s is None else timeout_s)
    deadline = time.monotonic() + max(timeout_s, 0.0)
    min_vram = _workflow_min_vram(engine_id, params)
    decision = plan(engine_id, params, comfy_url=comfy_url, allow_downgrade=False)
    started = time.monotonic()
    while not decision.ok and time.monotonic() < deadline:
        if on_wait:
            try:
                on_wait(round(time.monotonic() - started, 1))
            except Exception:  # noqa: BLE001
                pass
        time.sleep(poll_s)
        need = _with_min_vram(estimate(engine_id, params), min_vram)
        avail = available(engine_id, comfy_url)
        ok, why = _fits(engine_id, need, avail)
        decision = Decision(
            ok=ok, params=dict(params), reason="" if ok else why,
            available=avail, need=need,
        )
    return decision


def describe(engine_id: str, params: dict, *, comfy_url: str = "") -> dict:
    """只读查询：当前参数的需求与余量（给设置页/调试用，不做任何判定）。"""
    min_vram = _workflow_min_vram(engine_id, params)
    avail = available(engine_id, comfy_url)
    need = _with_min_vram(estimate(engine_id, params), min_vram)
    ok, why = _fits(engine_id, need, avail)
    return {"need": need, "available": avail, "fits": ok, "reason": why}
