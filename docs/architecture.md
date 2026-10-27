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
| `native` | `h3_native` / `anime` / `wan22` | 进程内推理通道，**用户可选**（不收敛、不强制迁移）；无工作流快照，不支持断点续算 |

判断一律用 `engines.is_comfy_engine(engine_id)`，不要再写 `engine == "comfyui"`——新增 ComfyUI 通道引擎时不需要改队列。

模板路由：`EngineMeta.workflow_presets`（mode → preset id）由引擎自己声明；未声明则用用户在「ComfyUI 设置」里 apply 的当前模板。`comfyui.resolve_template_for()` 不再按引擎 id 硬编码 FreeVideo 预设。

## 5. 收敛现状

`h3` 已归位 ComfyUI 通道：`engines/h3_comfy.py` 继承 `ComfyEngine`（提交 / 进度 / 归档 / 断线恢复全部复用），只额外声明 `workflow_presets` 与节点探测。FreeVideo 节点是这一张图的依赖，不是引擎身份——缺节点时 `H3ComfyEngine.describe()` 返回不可用并给安装指引，也可以在 ComfyUI 设置里换成 `minimax_h3_*`（API 计费）或自备 H3 图。

原生 diffusers H3 改名为 `h3_native`，仍是兜底通道：实现未改动，8GB 量化档保留在它这里。装机矩阵**保留** H3 自动分支（`c91ad05` 的量化成果），但文案明确标注「兜底 / 正路走 ComfyUI」——这是有意偏离 `8b939f9`（该提交原本要彻底移除自动安装），原因是不能被架构收敛抵消掉刚做好的小显存能力。

剩余待收敛项只有 `anime` / `wan22` 两个进程内引擎。

## 6. 引擎选择权归用户（2026-10-25 决定）

`anime` / `wan22` 不再作为"待收敛项"处理，也不从产品里下线：两条通道并列对外开放，
**由用户自己选**。产品只做三件事：

1. **标注差异**：引擎列表按 `execution_channel` 分组展示（ComfyUI 通道 / 本机进程内通道），
   卡片上显示可用状态与缺依赖提示，不替用户排序取舍；
2. **记住选择**：选择存在 `localStorage`（`aivideo:engine`，见 `frontend/src/enginePref.js`），
   生成页、分镜弹窗、小说批量提交、项目快捷生成共用同一个偏好；
3. **只在不可用时临时绕过**：偏好引擎当前不可用才切到别的可用引擎，并在界面上说明原因；
   偏好值本身不覆写，引擎恢复后用户的选择自动生效。

因此 `DEFAULT_ENGINE_ID` 仍是 `comfyui`，但它只表示"首次打开时的默认值"，不再是"唯一正路"；
后端 `validate_engine_mode` 只校验引擎是否支持该模式，不做通道白名单。

## 7. 资源守卫：需求预估 → 降档 → 拒绝（2026-10-27）

`backend/app/resource_guard.py` 是生成前的唯一资源闸门。此前只有"地板式"判断
（可用内存 < 2GB 拒绝），空闲 3GB 也会放行、然后加载 20GB 权重照样 OOM，等于没拦。

### 7.1 需求怎么算

需求拆成两段，**不按总像素线性缩放**（那样降到 256×256 时权重照样占满，估出来必然偏乐观）：

```
need_vram = 固定显存 + 变量显存 × (w×h×frames / 832×480×81)
need_ram  = 固定内存 + 变量内存 × 同上
```

固定部分是权重与框架开销（与分辨率无关），变量部分是激活/attention（随像素帧数走）。
各引擎在参考点（832×480 / 81 帧）下的经验值见 `ENGINE_PROFILES`；改大=更保守。
模板 `_meta.min_vram_gb` 作为**独立硬门槛**判定，不并进估算值——否则 8GB 卡跑声明
8GB 的工作流会被 1GB 安全余量误判成不够。

### 7.2 判据分两条通道

| 通道 | 显存判据 | 内存判据 | 理由 |
|---|---|---|---|
| `comfyui` | 总容量（`total - 1GB`） | 总容量（`total - 2GB`） | ComfyUI 是独立进程，会按需卸载模型、吃紧时走 pagefile；用当时的空闲量卡它，会把一台 32GB 的机器冤枉成"跑不了 H3" |
| `native` | 当前空闲（`free - 1GB`） | 当前可用（`free - 2GB`） | 进程内推理跟本进程抢内存/显存，赶不走别人 |

两条通道共用一条绝对地板：**可用内存 < 1GB 一律拒绝**（系统已经在换页，谁跑都炸）。
ComfyUI 通道若「可用内存 < 需求」则不拦但提示"生成期间可能变慢（系统换页）"。

探测顺序：ComfyUI 通道读 `/system_stats`（`devices[0].vram_*`），失败退本机
`nvidia-smi`；内存走 psutil → **ctypes `GlobalMemoryStatusEx`** → wmic。
ctypes 这一层是必需的：`requirements.txt` 里没有 psutil，装了才有，没装就静默返回 -1
（= 内存保护全失效）；wmic 在 Win11 已弃用且常被安全策略拦。

### 7.3 不够时怎么办

1. **入队前**（`jobs.create_job` / `storyboard.generate_shot` / `upgrades.prepare_upgrade`）：
   自动降档。**降档必须发生在固化工作流快照之前**，否则快照里的分辨率与任务参数不一致。
   候选排序是「先保时长、再降分辨率」——镜头时长由分镜定死，改了就跟剪辑对不上。
   降到底仍低于可用下限（宽 384px / 17 帧）则**拒绝**，不产出废片。
2. **执行前**（`queue_manager._guard_before_run`）：再查一次。
   - `native`：可就地降档（引擎按参数现算），改完落库 `resource_note`。
   - `comfyui`：快照已冻结，降档等于换工作流（破坏 PR-06 可复现），所以**只等不降**，
     超过 `AIVIDEO_RESOURCE_WAIT_S`（默认 120s）才失败；容量本身装不下的立即失败，不等。
   - `prompt_id` 已存在的恢复任务直接跳过守卫（ComfyUI 侧已在跑/已算完，拦了等于丢结果）。

降档与拒绝的原因都会写进 `jobs.resource_note`，队列页以「已自动降档」标签显示；
拒绝时接口返回 409 + 具体原因（含可用值/需求值/差值），前端直接展示。

### 7.4 开关

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `AIVIDEO_RESOURCE_GUARD` | `enforce` | `enforce` 降档+拒绝 / `warn` 只提示不拦 / `off` 完全关闭 |
| `AIVIDEO_RESOURCE_WAIT_S` | `120` | ComfyUI 通道执行前的等待余量上限（秒） |

任何探测失败一律**放行**（fail-open）：没有数据不拦人，只拦确认不够的。
