"""运行中看门狗：任务跑到一半，系统可用内存跌破硬底就中止本次生成。

为什么需要（改之前先读）：

`resource_guard` 只管「开跑那一刻」——余量够就放行，然后不再看第二眼。
但视频扩散的峰值出现在**中途**：开跑时可用内存 8GB，采样到第 40 帧时
权重 + 激活 + 别的程序一起把内存吃干净，Windows 开始狂换页，整机假死
（鼠标卡、任务管理器点不开、连"取消"都点不动）。这时候唯一还能自救的
动作，是正在跑的那个任务自己退出来把内存还回去。

判据为什么只盯内存，不盯显存：

- 显存吃紧的后果是 ComfyUI / torch 自己 OOM 报错，任务本来就失败退出，
  机器本身不受影响——不值得为它主动杀任务。
- 进程内推理时「显存空闲」必然接近 0（模型就压在卡上），拿它当判据会
  把每一个正常任务都判死。
- 内存吃紧才是整机级灾难：前台程序全部卡死，用户连重开程序都费劲。

防误杀（三条，缺一条都会把好任务杀掉）：

1. **连续 N 次采样**都低于地板才算（默认 3 次 × 5s = 15s）。任务刚启动
   加载权重时可用内存会短暂塌下去，单次采样判死会把每个任务的第一分钟
   全砍掉。
2. **开跑后 grace 期内不判**（默认 15s），同上，覆盖加载最猛的阶段。
3. **探测失败一律不判**（-1 = 没有数据），和守卫一样 fail-open：没有数据
   不杀任务，只杀确认撑爆的。

开关与守卫一致：`AIVIDEO_RESOURCE_GUARD=off` 时看门狗也关；`warn` 时只
记录不中止（用户只想看提示不想被打断）。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from . import resource_guard

# 硬底：和入队守卫共用同一个数（1GB），避免「开跑时算不够、跑起来又不算」
# 两套标准互相打架。
DEFAULT_FLOOR_GB = resource_guard.RAM_FLOOR_GB
# 预警线：低于「地板 + 这个余量」时先给用户一句“内存紧张”，但**不停任务**。
TIGHT_MARGIN_GB = 1.5


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


def watchdog_enabled() -> bool:
    """看门狗是否生效。`AIVIDEO_RESOURCE_GUARD` 的 off / warn 都会关掉中止。"""
    switch = (os.environ.get("AIVIDEO_RESOURCE_WATCHDOG") or "on").strip().lower()
    if switch in ("off", "0", "false", "no"):
        return False
    # warn 模式的含义是「只提示不打断」，中止任务明显违背它
    return resource_guard.guard_mode() == "enforce"


@dataclass
class Verdict:
    """一次采样的结论。level: ok | tight（紧张但继续）| abort（确认撑爆）。"""

    level: str = "ok"
    ram_gb: float = -1.0
    reason: str = ""


class Watcher:
    """纯逻辑的采样判定器（不碰 asyncio / 数据库，单测直接喂数值）。

    调用方按自己的节奏 `observe(ram_gb)`，拿 abort 就去中止任务。
    """

    def __init__(
        self,
        *,
        floor_gb: float = None,
        tight_margin_gb: float = TIGHT_MARGIN_GB,
        interval_s: float = None,
        samples: int = None,
        grace_s: float = None,
        need_ram_gb: float = 0.0,
        clock=None,
    ):
        self.floor_gb = DEFAULT_FLOOR_GB if floor_gb is None else float(floor_gb)
        self.tight_gb = self.floor_gb + max(0.0, float(tight_margin_gb))
        self.interval_s = max(
            0.2, _env_float("AIVIDEO_WATCHDOG_S", 5.0) if interval_s is None else interval_s
        )
        self.samples = max(
            1, _env_int("AIVIDEO_WATCHDOG_SAMPLES", 3) if samples is None else int(samples)
        )
        self.grace_s = max(
            0.0, _env_float("AIVIDEO_WATCHDOG_GRACE_S", 15.0) if grace_s is None else grace_s
        )
        self.need_ram_gb = float(need_ram_gb or 0.0)
        self._clock = clock or time.monotonic
        self.started = self._clock()
        self.streak = 0
        self.warned = False

    # ---------- 判定 ----------
    @property
    def in_grace(self) -> bool:
        return (self._clock() - self.started) < self.grace_s

    def observe(self, ram_gb: float) -> Verdict:
        """喂一次「系统可用内存」（GB，-1 表示探测失败）。"""
        ram = float(ram_gb)
        if ram < 0:
            # 探测失败：保留既有 streak 不清零也不累加，绝不因没有数据杀任务
            return Verdict("ok", ram)
        if ram >= self.tight_gb:
            self.streak = 0
            return Verdict("ok", ram)
        if ram >= self.floor_gb:
            # 紧张但还没到地板：清掉「连击」，只提示
            self.streak = 0
            if self.warned:
                return Verdict("ok", ram)
            self.warned = True
            return Verdict(
                "tight", ram,
                f"可用内存降至 {ram:.1f}GB（接近 {self.floor_gb:g}GB 硬底），继续生成；"
                "建议关掉占内存的程序，否则会被自动中止",
            )

        self.streak += 1
        if self.in_grace:
            return Verdict("ok", ram)
        if self.streak < self.samples:
            return Verdict("ok", ram)
        return Verdict("abort", ram, self._abort_reason(ram))

    def _abort_reason(self, ram: float) -> str:
        held = self.streak * self.interval_s
        need = (
            f"（本任务估算需要 {self.need_ram_gb:.1f}GB）" if self.need_ram_gb > 0 else ""
        )
        return (
            f"生成过程中系统可用内存降至 {ram:.1f}GB，低于 {self.floor_gb:g}GB 硬底"
            f"并持续约 {held:.0f}s{need}——继续跑下去会让整机进入换页假死，"
            "已自动中止本次生成。请关闭占内存的程序（浏览器/游戏/其他 AI 工具）后重新生成。"
        )
