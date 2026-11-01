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
    # 插件通道门槛 = 8G 显存 / 32G 内存 / 57.5GiB 磁盘（四包 49.5 + 余量 8）
    ("8G 显存 / 32G 内存 / 144G 磁盘（H3 正路）", 8, 32, 144, "h3"),
    ("8G 显存 / 32G 内存 / 45G 磁盘（差 12.5G）", 8, 32, 45, "wan-ti2v"),
    ("8G 显存 / 33G 内存 / 60G 磁盘（刚过门槛）", 8, 33, 60, "h3"),
    ("8G 显存 / 33G 内存 / 50G 磁盘（差 7.5G）", 8, 33, 50, "wan-ti2v"),
    ("8G 显存 / 31G 内存 / 200G 磁盘（内存不足）", 8, 31, 200, "wan-ti2v"),
    # 32GB 实机报 31.96GB：不加容差会把目标配置判成不够，必须放行
    ("真机：7.996G 显存 / 31.96G 内存 / 97G 磁盘（32GB 实报值）", 7.996, 31.96, 97, "h3"),
    ("12G 显存 / 64G 内存 / 160G 磁盘", 12, 64, 160, "h3"),
    ("24G 显存 / 64G 内存 / 160G 磁盘（大卡也优先 H3）", 24, 64, 160, "h3"),
    ("24G 显存 / 64G 内存 / 50G 磁盘（大卡但盘不够）", 24, 64, 50, "wan-ti2v"),
    # 真机 RTX 4060 实测：nvidia-smi 报 8188MiB → 7.996GB，严格 >=8 会判死
    ("真机 4060：7.996G 显存 / 32G 内存 / 110G 磁盘", 7.996, 32, 110, "h3"),
    # 2026-11-01 真机：97GB 可用。旧门槛 100.3GB 会把它判成「装不下」→ 推 Wan，
    # 实际四包只有 49.5GiB。这条用例就是防那个口径复辟。
    ("真机 4060：7.996G 显存 / 32G 内存 / 97G 磁盘（旧门槛误杀）", 7.996, 32, 97, "h3"),
    ("真机 4060：7.996G 显存 / 32G 内存 / 40G 磁盘", 7.996, 32, 40, "wan-ti2v"),
    ("16G 显存 / 16G 内存 / 200G 磁盘（内存低于标定线）", 16, 16, 200, "wan-ti2v"),
    ("16G 显存 / 64G 内存 / 200G 磁盘（内存够就按 H3 走）", 16, 64, 200, "h3"),
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
