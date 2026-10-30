"""MiniMax H3 × ComfyUI 通道

架构口径（2026-10-22 定，2026-10-25 回归主线）：**H3 的正路也走 ComfyUI**。
NMVideo 不在本进程加载 H3 权重，只按模式选图、注入参数、提交、收进度、归档——
与其它引擎同一条链路（快照固化、prompt_id 持久化、断线恢复全部复用）。

H3 的 ComfyUI 实现来自内置预设 ``nmvideo_h3_*``（节点 ``FreeVideoGenerate``）。
该插件节点在这里只是**可选适配器**：装了就能用；没装则在引擎卡如实标记不可用，
提交前由能力中心阻断并给安装指引。节点缺失不会导致换掉执行底座——也可以在
「ComfyUI 设置」里 apply 别的 H3 图（如 ``minimax_h3_*`` 官方 API 节点，按量计费），
或直接粘贴自己的 H3 JSON。

进程内 diffusers 版 H3 仍在 ``engines/h3.py``（id = ``h3_native``，含 8GB 量化档），
仅在 ComfyUI 不可用时兜底，**不与这里承诺同参数同结果**：工作流图、采样器、
蒸馏档位由 ComfyUI 侧决定。
"""
import time

from .base import EngineMeta
from .comfy import ComfyEngine

# mode -> 内置预设 id（H3 插件节点图）
H3_PRESETS = {
    "t2v": "nmvideo_h3_t2v",
    "i2v": "nmvideo_h3_i2v",
    # 续接（continuation）在 H3 插件侧就是以末帧为首帧的 i2v 输入
    "continuation": "nmvideo_h3_i2v",
    "first_last": "nmvideo_h3_first_last",
}

# 预设依赖的节点：缺任一节点都无法执行
H3_REQUIRED_NODES = ("FreeVideoGenerate",)

# 节点探测缓存：base_url -> (timestamp, missing, probe_ok)。
# 模块级而非实例级——list_engines() 每次都会新建实例，实例缓存等于没有。
_NODE_CACHE: dict = {}
_NODE_TTL = 30.0


class H3ComfyEngine(ComfyEngine):
    meta = EngineMeta(
        id="h3",
        name="MiniMax H3（ComfyUI 通道）",
        description=(
            "MiniMax H3 / VDN-H3 经 ComfyUI 工作流执行 · 按模式自动套用内置 H3 预设 · "
            "需 ComfyUI 侧装有 H3 插件节点（8GB 显存 / 16GB 内存起）"
        ),
        status="ready",
        supported_modes=("t2v", "i2v", "first_last", "continuation"),
        # 与 comfyui 引擎同属 ComfyUI 通道：队列、快照、断线恢复走同一套逻辑
        execution_channel="comfyui",
        workflow_presets=H3_PRESETS,
    )

    def missing_nodes(self) -> tuple:
        """探测 H3 预设依赖的节点 → (missing, probe_ok)。

        探测失败（/object_info 不可达或非 JSON）返回 ``([], False)``：
        不把「探测不了」谎报成「没装」，避免 ComfyUI 版本老旧时误锁引擎。
        """
        url = self.client.base_url
        now = time.time()
        hit = _NODE_CACHE.get(url)
        if hit and now - hit[0] < _NODE_TTL:
            return hit[1], hit[2]
        try:
            info = self.client.object_info(node_names=list(H3_REQUIRED_NODES))
        except Exception:
            _NODE_CACHE[url] = (now, [], False)
            return [], False
        missing = [n for n in H3_REQUIRED_NODES if n not in (info or {})]
        _NODE_CACHE[url] = (now, missing, True)
        return missing, True

    def describe(self) -> dict:
        """动态状态：ComfyUI 可达 + 预设节点齐备才算 ready。"""
        base = super().describe()
        if base.get("status") != "ready":
            return base
        missing, probe_ok = self.missing_nodes()
        if missing:
            return {
                **base,
                "status": "unavailable",
                "note": (
                    f"ComfyUI 侧未检测到 {'、'.join(missing)}：先安装 H3 插件节点再选 H3；"
                    "或在「ComfyUI 设置」里改用其它 H3 工作流（如 minimax_h3_* API 节点）"
                ),
            }
        if not probe_ok:
            return {**base, "note": "节点能力探测失败：请确认 ComfyUI 支持 /object_info"}
        return base
