"""comfy_manager 真机 e2e：发现 → 真实启动 → 等在线 → 真实关闭。

直接调用 app.comfy_manager 的真实代码路径（与路由层同一函数，路由只是
薄封装 + HTTPException）。跑完保证 ComfyUI 处于关闭状态。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import comfy_manager

s = comfy_manager.state()
print("初始状态:", {k: s[k] for k in ("online", "is_local", "can_start", "can_stop", "starting")})
print("note:", s["note"])
print("managed:", s["managed"])
assert s["is_local"], "comfy_url 应指向本机"
assert s["can_start"], "应处于可启动状态（在线/已启动则先手动处理再验）"

r = comfy_manager.start()
print("start:", r)
assert r["started"], f"启动未成功: {r}"

deadline = time.time() + 180
st = s
while time.time() < deadline:
    time.sleep(3)
    st = comfy_manager.state()
    if st["online"]:
        break
    if not st["process_alive"]:
        print("进程早退，日志尾部：")
        log = Path(st.get("log_path") or "")
        if log.exists():
            print(log.read_text(encoding="utf-8", errors="replace")[-2000:])
        break
print("启动后状态:", {k: st[k] for k in ("online", "starting", "process_alive")})
assert st["online"], "ComfyUI 未在 180s 内就绪"

r = comfy_manager.stop()
print("stop:", r)
assert r["stopped"], f"停止未成功: {r}"
time.sleep(2)
st2 = comfy_manager.state()
print("关闭后 online:", st2["online"], "| can_start:", st2["can_start"])
assert not st2["online"], "关闭后仍探测到在线"
assert st2["can_start"], "关闭后应恢复可启动状态"
print("\nE2E OK：启动→在线→关闭 全链路通过，ComfyUI 已恢复关闭状态")
