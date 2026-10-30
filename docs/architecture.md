# 架构定位：ComfyUI 底座（2026-10-22）

本文只定义边界，不描述未来功能。口径由项目负责人确认：**项目要基于 ComfyUI，外部参考项目只用于借鉴产品设计，不进入运行时。**

## 1. 一句话定位

- **执行底座 = ComfyUI**：所有视频生成经 `/prompt` 提交、WS 进度、`/history` 对账、产物归档，模型与节点由 ComfyUI 侧管理。
- **产品层 = NMVideo**：编排、资产、版本、审核、剪辑、导出。不自己加载视频模型。
- **外部参考 = 只借鉴产品能力设计**：不复制运行时，不把任何第三方插件节点当作引擎身份。

## 2. 借鉴的能力（能力，不是代码）

| 能力 | 落在 NMVideo 的实现位置 |
|---|---|
| 工作流能力中心（按图声明 mode / 输入槽 / 缺节点） | `workflow_capabilities.py`（读模板的能力描述）+ `comfy_workflows/presets/*.json` 的 `_aivideo_map` |
| 首帧 / 尾帧独立槽位 | `first_frame_image`、`last_frame_image` 两个独立入参 + 各自节点映射；`first_last` 模式缺任一槽位直接阻断 |
| 多参考图 | 目前是单槽 `reference_image`；多参考仍属 PR-08 计划项，未实现 |
| 预览转正式版 | `quality.py` 档位 + `upgrades.py` 保留快照/seed 的升级路径 |
| 队列与状态可解释 | `queue_manager.py`（prompt_id 持久化、断线恢复、待核查） |
| 作品级工作流记录 | 入队固化 `workflow_snapshot` + MP4 内嵌参数元数据 |

## 3. 明确不做的事

- 不要求在 `custom_nodes` 安装特定第三方插件才能出片；`freevideo_h3_*` 只是内置预设之一。
- 不在 NMVideo 进程内为「默认路径」加载视频模型权重。
- 不把第三方插件的节点名、参数名当作内部契约——契约是 `_aivideo_map` 槽位与能力描述符。

## 4. 执行通道（代码契约）

`EngineMeta.execution_channel`（`backend/app/engines/base.py`）：

| 通道 | 引擎 | 语义 |
|---|---|---|
| `comfyui` | `comfyui`（默认，`DEFAULT_ENGINE_ID`） | 入队即固化工作流快照；队列按 `(引擎, 服务地址)` 缓存；支持 prompt_id 恢复 |
| `comfyui` | `h3`（专用预设） | 同一通道，按模式套 `freevideo_h3_*` 预设；`FreeVideoGenerate` 节点缺失时引擎卡如实不可用 |
| `native` | `h3_native` / `anime` / `wan22` | 进程内推理通道，**用户可选**（不收敛、不强制迁移）；无工作流快照，不支持断点续算 |

判断一律用 `engines.is_comfy_engine(engine_id)`，不要再写 `engine == "comfyui"`——新增 ComfyUI 通道引擎时不需要改队列。

模板路由：`EngineMeta.workflow_presets`（mode → preset id）由引擎自己声明；未声明则用用户在「ComfyUI 设置」里 apply 的当前模板。`comfyui.resolve_template_for()` 不再按引擎 id 硬编码专用预设。

## 5. 收敛现状

`h3` 已归位 ComfyUI 通道：`engines/h3_comfy.py` 继承 `ComfyEngine`（提交 / 进度 / 归档 / 断线恢复全部复用），只额外声明 `workflow_presets` 与节点探测。H3 插件节点是这一张图的依赖，不是引擎身份——缺节点时 `H3ComfyEngine.describe()` 返回不可用并给安装指引，也可以在 ComfyUI 设置里换成 `minimax_h3_*`（API 计费）或自备 H3 图。

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

降档与拒绝的原因都会写进 `jobs.resource_note`，队列页按内容显示
「已自动降档 / 内存紧张 / 资源中止」标签；拒绝时接口返回 409 + 具体原因
（含可用值/需求值/差值），前端直接展示。

### 7.4 运行中看门狗（`resource_watch.py`）

守卫只管「开跑那一刻」。视频扩散的峰值在中途：开跑时余量 8GB，采样到一半
权重 + 激活 + 别的程序把内存吃干净，Windows 开始狂换页，整机假死——鼠标卡、
任务管理器点不开、连「取消」都点不动。这时候唯一还能自救的是正在跑的任务
自己退出。

- **只盯 RAM**：显存吃紧会由 ComfyUI/torch 自己 OOM 报错，任务本来就失败，
  机器不受影响；而进程内推理时「显存空闲」必然接近 0，拿它当判据会把每个
  正常任务都判死。内存吃紧才是整机级灾难。
- **防误杀三条**：连续 3 次采样（×5s）都在地板以下才算；开跑后 15s grace
  内不判；探测失败（-1）一律不判。grace 期内跌破的连击会累计，所以「一开跑
  就在换页」的任务会在 grace 结束的第一个采样点中止，不必再等 15s。
- **中止怎么落地**：看门狗翻 `is_cancelled()` → 引擎在下一个检查点抛
  `GenerationCancelled` → `_run_job` 区分「用户取消」与「资源中止」：前者标
  `cancelled`，后者标 `failed` 并把原因写进 `error` + `resource_note`、镜头
  打回需重做。
- **远端尽力收尾**：ComfyUI 任务中止时先查 `/queue`，排队中的精确删除
  （`delete: [prompt_id]`），确认 `queue_running` 就是本任务才 `/interrupt`。
  HF-06 的「不乱调全局中断」依然成立——只有整机已经在换页时才会走到这条路径。
- **预警不打断**：可用内存跌破「地板 + 1.5GB」时只写一条 `resource_note`
  提醒（每任务一次），任务继续跑。

### 7.5 开关

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `AIVIDEO_RESOURCE_GUARD` | `enforce` | `enforce` 降档+拒绝 / `warn` 只提示不拦 / `off` 完全关闭 |
| `AIVIDEO_RESOURCE_WAIT_S` | `120` | ComfyUI 通道执行前的等待余量上限（秒） |
| `AIVIDEO_RESOURCE_WATCHDOG` | `on` | `off` 关掉运行中看门狗（守卫的 `warn`/`off` 也会关掉它） |
| `AIVIDEO_WATCHDOG_S` | `5` | 看门狗采样间隔（秒） |
| `AIVIDEO_WATCHDOG_SAMPLES` | `3` | 连续多少次跌破才算确认 |
| `AIVIDEO_WATCHDOG_GRACE_S` | `15` | 开跑后多久内不判（覆盖加载权重阶段） |

任何探测失败一律**放行**（fail-open）：没有数据不拦人，只拦确认不够的。
看门狗同理——「没有数据」绝不等于「中止任务」。

## 8. 启动 / 工作空间页（2026-10-27）

侧栏第一项「启动」（`/launch`），对应 `LaunchView.vue` + `GET /api/launch/status`。

**只报实况，不编进度。** 桌面端有启动器进程，能报真实的
`preparing / installing / models / ready` 阶段；浏览器端看不到那个进程，所以
这里不假装「正在安装依赖 43%」。三件事各自独立探测，进度 = 几件事已经通了：

| 步骤 | 判据 | 拿不到时的说法 |
|---|---|---|
| 本地服务 | 这个接口能被调到（本身就是证据） | —— |
| ComfyUI | `ComfyUIClient.is_available()` + `/system_stats` + `/queue` | 「未检测到 {url}，请先启动 ComfyUI」 |
| 生成引擎 | ComfyUI 在线则通道可用；否则默认引擎无法出片 | 说明依赖关系，不谎报可用 |

ComfyUI 未连接是**正常首启状态**，接口必须 200 + 原因，不能 500（`test_launch_status`
钉住了这条）。轮询成本靠缓存压住：目录体积 60s、硬件探测 30s——模型目录几百 GB
时每 2 秒 walk 一遍会把磁盘拖死。

路径与体积都来自后端实况（`paths.models/outputs`、磁盘可用、显卡/内存），
点击路径可复制。就绪后轮询从 2s 放宽到 10s。

配色（2026-10-27 用户要求）：全局换蓝黑，强调色 `--accent #3b82f6` /
`--accent-2 #38bdf8`，半透明一律用 `--accent-rgb` / `--accent-2-rgb`，不再写死
十六进制；`desktop/renderer/styles.css` 与前端共用同一套值，壳层与页面不打架。

## 9. 设置页（2026-10-29）

路由 `/settings`，三段等宽 tab：**服务 / 环境 / 偏好**（版式参考常见桌面应用的设置弹窗：
顶部 tab → 分组小标签 → 圆角卡片 → 「左标题+副说明 / 右控件」的行）。
`/services` 重定向到 `/settings?tab=services`，`/comfy`（工作流模板编辑器）保留——
模板 JSON 与参数映射那块太重，塞进设置页会变成一屏半的输入框。

| tab | 内容 | 数据来源 |
|---|---|---|
| 服务 | ComfyUI 地址与队列、LLM、图像生成、TTS、MCP 接入 | `/services/status`、`/services/config`、`/services/mcp` |
| 环境 | ComfyUI 地址、目录（程序/模型/输出/DB）、模板编辑器入口、兼容性 | `/launch/status` + `/services/config` |
| 偏好 | 语言、配色、更新（桌面端）、存储占用、关于 | `/launch/status` |

原则与启动页一致：**只显示实况，不做假开关**。

- 目录只读：目录由后端环境变量决定，界面只展示/复制/打开，避免界面和进程各记一份。
- 资源守卫与看门狗只显示实际生效值（`launch/status` 新增 `resource` 字段：
  `guard / watchdog / ram_floor_gb / ram_tight_gb`）。改档位要动环境变量并重启后端，
  所以不放切换开关——放了也改不动正在跑的进程。
- 更新按钮仅在桌面端可用（`window.desk.checkUpdate`），浏览器打开时按钮是灰的并写明原因。
- 语言只做「简体中文」，English 显式 disabled：没做多语言就不给一个点了没反应的按钮。

样式类在 `style.css`（`.set-tabs / .set-group / .set-card / .set-row / .set-seg /
.set-switch / .set-badge`），组件在 `components/settings/`；分段控件是
`components/Segmented.vue`（`variant="tabs"` 走顶部大分段，默认是表单里的小分段）。

## 10. 本地构建安装包（2026-10-30）

```bash
backend/.venv/Scripts/python.exe -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
cd desktop && npm install          # 首次；镜像见下
cd .. && build-desktop.bat         # 前端 → launcher exe → NSIS
```

产物：`desktop/dist/aivideo-desktop-setup-<版本>.exe`（约 96MB）+ `latest.yml`。
安装包内是 Electron 壳 + `resources/launcher/NMVideo.exe`（PyInstaller 单文件，
内含前后端全部源码）；torch/模型由 exe 首启按硬件自动装。

国内网络要点：`ELECTRON_BUILDER_BINARIES_MIRROR` 指 npmmirror，
`desktop/electron-dist/` 放 `electron-v<版本>-win32-x64.zip`（构建脚本会自己下）；
`signAndEditExecutable: false` 避开 winCodeSign（其 7z 内含 macOS symlink，
非提权进程解压失败）。

### 首启耗时（8GB 4060 / 32GB 内存实测）

| 阶段 | 耗时 | 说明 |
|---|---|---|
| 解压载荷 + 硬件探测 | ~3s | 决策模型/torch/量化 |
| 建 venv | ~1s | 优先系统 Python ≥3.10 |
| 装 PyTorch CUDA | 8~15min | 2.5GB，`download.pytorch.org` |
| 装 requirements | 3~5min | transformers/diffusers/fastapi 等 |
| 起 uvicorn | ~5s | 之后才会看到界面 |

第二次启动（venv 已就绪）约 40 秒到 `STATE=ready`。

### 这轮打包暴露的三个真 bug

1. **版本号空串**：`_version()` 只认 `AIVIDEO_VERSION` 和 `desktop/package.json`，
   而安装包里没有 `desktop/`。改读运行根的 `VERSION`（`build.py` 写的
   `payload/VERSION`）。「关于」那行原来显示「版本 —」。
2. **ComfyUI 探测缓存挂在实例上**：`list_engines()` 每次 `c()` 新建实例，缓存
   永远打不中 → ComfyUI 离线时 `/api/engines` 实测 4.2s（生成页一打开就转圈）。
   缓存改到模块级、按 `base_url` 分键，`/api/services/status`、`/api/launch/status`
   共用同一份。
3. **首屏 4.4s 全在请求路径上**：首次 `hw_probe.probe()` 要 `import torch`（约 2s）
   + spawn `nvidia-smi`，ComfyUI 探测再等满 1.5s 超时。改成 lifespan 起两个后台
   线程预热，首屏降到 **0.012s**。

配套：探测缓存 TTL 20s（10s 会被「用户打开页面的时间」吃掉，预热等于白做）；
`GET /api/launch/status?refresh=1` 跳过缓存，「重新检测」按钮走这条路——用户刚把
ComfyUI 拉起来时不该让他等缓存过期。

`packaging/launcher.spec` 由 `build.py` 每次重生成，内容是本机绝对路径，已从仓库
移除并加入 `.gitignore`。
