# -*- coding: utf-8 -*-
"""引擎选型回归：几档典型配置应该给出什么引擎（只读校验，不碰真实环境）。

为什么要有这个：选型一旦退回「按原生门槛卡插件通道」，8GB/32GB 的机器就会
被告知「跑不了 H3、给你下 Wan」——用户白下 30GB 才发现。数字错了页面看不出来，
只有真机跑一遍才暴露，所以钉成构建前自检。

跑法：backend/.venv/Scripts/python.exe packaging/check_plan.py
      （packaging/build.py 每次构建前会自动跑一遍）
"""
import importlib.util
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("launcher", PROJECT / "packaging" / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

HWInfo = launcher.HWInfo

CASES = [
    # (说明, 显存, 内存, 磁盘, 期望 model)
    ("8G 显存 / 32G 内存 / 144G 磁盘（H3 正路）", 8, 32, 144, "h3"),
    ("8G 显存 / 32G 内存 / 86G 磁盘（差 14G）", 8, 32, 86, "wan-ti2v"),
    ("8G 显存 / 16G 内存 / 101G 磁盘（门槛卡点）", 8, 16, 101, "h3"),
    ("8G 显存 / 15G 内存 / 200G 磁盘（内存不足）", 8, 15, 200, "wan-ti2v"),
    ("12G 显存 / 64G 内存 / 160G 磁盘", 12, 64, 160, "h3"),
    ("24G 显存 / 64G 内存 / 160G 磁盘（大卡也优先 H3）", 24, 64, 160, "h3"),
    ("24G 显存 / 64G 内存 / 90G 磁盘（大卡但盘不够）", 24, 64, 90, "wan-ti2v"),
    # 真机 RTX 4060 实测：nvidia-smi 报 8188MiB → 7.996GB，严格 >=8 会判死
    ("真机 4060：7.996G 显存 / 32G 内存 / 110G 磁盘", 7.996, 32, 110, "h3"),
    ("真机 4060：7.996G 显存 / 32G 内存 / 81G 磁盘", 7.996, 32, 81, "wan-ti2v"),
]

fails = 0
for desc, vram, ram, disk, expect in CASES:
    hw = HWInfo(gpus=[("fake", vram)], ram_total_gb=ram, disk_free_gb=disk)
    plan = launcher.choose_plan(hw)
    ok = plan.model == expect
    fails += 0 if ok else 1
    print(f"{'✓' if ok else '✗'} {desc}\n    -> {plan.model}（期望 {expect}）")
    if not ok:
        print(f"    reason: {plan.reason}")

print()
print("全部通过" if not fails else f"{fails} 条不符")
sys.exit(1 if fails else 0)
