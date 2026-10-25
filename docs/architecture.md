# 架构定位：ComfyUI 底座 + FreeVideo 参考（2026-10-22）

本文只定义边界，不描述未来功能。口径由项目负责人确认：**项目要基于 ComfyUI，FreeVideo 只作为参考借鉴对象。**

## 1. 一句话定位

- **执行底座 = ComfyUI**：所有视频生成经 `/prompt` 提交、WS 进度、`/history` 对账、产物归档，模型与节点由 ComfyUI 侧管理。
- **产品层 = NMVideo**：编排、资产、版本、审核、剪辑、导出。不自己加载视频模型。
- **FreeVideo = 参考样本**：只借鉴产品能力设计，不复制运行时，不把它的插件节点当作引擎身份。

## 2. 借鉴 FreeVideo 的部分（能力，不是代码）

| 能力 | 落在 NMVideo 的实现位置 |
|---|---|
| 工作流能力中心（按图声明 mode / 输入槽 / 缺节点） | `workflow_capabilities.py`（读模板的能力描述）+ `comfy_workflows/presets/*.json` 的 `_aivideo_map` |
| 首帧 / 尾帧独立槽位 | `first_frame_image`、`last_frame_image` 两个独立入参 + 各自节点映射；`first_last` 模式缺任一槽位直接阻断 |
| 多参考图 | 目前是单槽 `reference_image`；多参考仍属 PR-08 计划项，未实现 |
| 预览转正式版 | `quality.py` 档位 + `upgrades.py` 保留快照/seed 的升级路径 |
| 队列与状态可解释 | `queue_manager.py`（prompt_id 持久化、断线恢复、待核查） |
| 作品级工作流记录 | 入队固化 `workflow_snapshot` + MP4 内嵌参数元数据 |

## 3. 明确不做的事

- 不要求在 `custom_nodes` 安装 FreeVideo 才能出片；`freevideo_h3_*` 只是内置预设之一。
- 不在 NMVideo 进程内为「默认路径」加载视频模型权重。
- 不把 FreeVideo 的节点名、参数名当作内部契约——契约是 `_aivideo_map` 槽位与能力描述符。

## 4. 执行通道（代码契约）

`EngineMeta.execution_channel`（`backend/app/engines/base.py`）：

| 通道 | 引擎 | 语义 |
|---|---|---|
| `comfyui` | `comfyui`（默认，`DEFAULT_ENGINE_ID`） | 入队即固化工作流快照；队列按 `(引擎, 服务地址)` 缓存；支持 prompt_id 恢复 |
| `comfyui` | `h3`（专用预设） | 同一通道，按模式套 `freevideo_h3_*` 预设；`FreeVideoGenerate` 缺失时引擎卡如实不可用 |
| `native` | `h3_native` / `anime` / `wan22` | 历史兜底通道，ComfyUI 不可用时才用；无工作流快照，不支持断点续算 |

判断一律用 `engines.is_comfy_engine(engine_id)`，不要再写 `engine == "comfyui"`——新增 ComfyUI 通道引擎时不需要改队列。

模板路由：`EngineMeta.workflow_presets`（mode → preset id）由引擎自己声明；未声明则用用户在「ComfyUI 设置」里 apply 的当前模板。`comfyui.resolve_template_for()` 不再按引擎 id 硬编码 FreeVideo 预设。

## 5. 收敛现状

`h3` 已归位 ComfyUI 通道：`engines/h3_comfy.py` 继承 `ComfyEngine`（提交 / 进度 / 归档 / 断线恢复全部复用），只额外声明 `workflow_presets` 与节点探测。FreeVideo 节点是这一张图的依赖，不是引擎身份——缺节点时 `H3ComfyEngine.describe()` 返回不可用并给安装指引，也可以在 ComfyUI 设置里换成 `minimax_h3_*`（API 计费）或自备 H3 图。

原生 diffusers H3 改名为 `h3_native`，仍是兜底通道：实现未改动，8GB 量化档保留在它这里。装机矩阵**保留** H3 自动分支（`c91ad05` 的量化成果），但文案明确标注「兜底 / 正路走 ComfyUI」——这是有意偏离 `8b939f9`（该提交原本要彻底移除自动安装），原因是不能被架构收敛抵消掉刚做好的小显存能力。

剩余待收敛项只有 `anime` / `wan22` 两个进程内引擎。
