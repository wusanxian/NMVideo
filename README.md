# 🎬 AI 视频工坊（aivideo）

Windows 当前本地构建：**`release/0.1.2/aivideo-desktop-setup-0.1.2.exe`**，双击安装。目录内附启动器、校验值和使用说明；首次运行仍需联网准备依赖与模型。0.1.2 增加逐句对白排程、超时末帧延长、按配音时长生成分句字幕和竖屏导出，详见[实施记录](docs/reports/20261022-short-drama-delivery.md)。

本地运行的 AI 文生视频生成器，**可插拔引擎架构**。参考 FreeVideo 的低显存设计哲学：硬件自适应（模型选择/量化/卸载全自动）、四档质量、两遍采样、视频自带参数。

2026-10-20 增量改造：统一生成参数与引擎模式校验、可靠工作流快照、保留镜头来源的预览升级、FreeVideo H3 本地节点预设、角色参考图/LoRA 接入、导出清单冻结，以及 Windows 中文元数据修复。使用范围与验证结果见[实施记录](docs/reports/20261020-optimization-implementation.md)。FreeVideo 需要另行安装，当前完成代码和自动化验证，实机出片效果待验收。

| 引擎 | 模型 | 最低硬件 | 出片速度 |
|---|---|---|---|
| **AnimeDiff 动漫** | dreamlike-anime + AnimateDiff v3 | 纯 CPU，8GB 内存 | 256px 8 帧 12 步 ≈ 4 分钟（i5 CPU） |
| **Wan 2.2 文生视频** | Wan2.2-TI2V-5B | **8GB 显存 + 32GB 内存** | 480p 33 帧 20 步 ≈ 10-30 分钟（8GB 卡） |
| Wan 2.2 画质档 | Wan2.2-T2V-A14B（MoE 双专家） | 20GB+ 显存 + 60GB+ 内存 | 720p 81 帧 30 步 ≈ 30-90 分钟 |

## 架构

```
┌──────────────────────────────────────────────────────────────┐
│  网页创作工作台（Vue3 + Pinia，:5173 dev / :8000 单端口）      │
│  剧本（AI 编剧） / 分镜 / 素材（资产库） / 生成 / 剪辑（时间线）│
└───────────────────────────┬──────────────────────────────────┘
                            │ REST API
┌───────────────────────────▼──────────────────────────────────┐
│  项目后端与任务调度（FastAPI :8000）                          │
│  ├─ 任务状态：队列 queue_manager（单 worker，取消/进度/耗时）   │
│  ├─ 文件数据库：SQLite（jobs + 项目/场景/分镜/版本/轨道/审核）  │
│  ├─ 提示词：角色一致性拼装 / LLM 提示词优化 / 质量档位预设     │
│  └─ 工作流映射：_aivideo_map 节点注入（ComfyUI 模板/预设）      │
└───────────────────────────┬──────────────────────────────────┘
                            │
        ┌───────────────────┴───────────────────┐
        ▼                                       ▼
┌───────────────────────────────┐   ┌───────────────────────────┐
│  ComfyUI / FreeVideo          │   │  其他模型服务              │
│  · MiniMax H3 文/图生视频     │   │  · LLM（AI 编剧/提示词优化 │
│  · Wan 2.2 TI2V-5B 工作流     │   │    /台词润色，OpenAI 兼容）│
│  · AnimateDiff Evolved        │   │  · 图像生成（参考图/概念图 │
│  · 本地引擎 AnimeDiff/Wan     │   │    /首帧，OpenAI 兼容）    │
│  · WS 实时进度 + 轮询兜底     │   │  · TTS 配音（edge-tts 神经 │
│                               │   │    音色 + SAPI5 离线）    │
│                               │   │  · 字幕（SRT 生成/烧录）  │
└───────────────────────────────┘   └───────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  作品资产与剪辑输出（outputs/）                               │
│  图片（参考图/概念图）/ 音频（对白/音乐/音效）/ 视频（镜头与    │
│  版本）/ 元数据（生成参数内嵌 MP4）/ 最终 MP4（混音+字幕烧录）  │
└──────────────────────────────────────────────────────────────┘
```

## 快速开始

### 方式一：单文件 exe（推荐，任意 Windows 10/11 电脑）

从 `packaging/dist/` 拷贝 **`AI视频工坊-v0.1.2.exe`** 到目标电脑，双击运行，全程自动：

```
探测硬件（显卡/显存/内存/磁盘）
  → 按矩阵选模型 + torch 版本 + 量化/卸载策略
  → 准备 Python（优先系统 Python 建 venv；没有则自动下载便携 Python）
  → 按显卡装 torch（N 卡 CUDA 版约 2.5GB / 无卡 CPU 版约 200MB）
  → 装依赖（FastAPI/diffusers/torchao 等）
  → 下载匹配模型（hf-mirror 镜像）
  → 启动服务并自动打开浏览器
```

**硬件 → 方案自动决策矩阵**（`--model` 可手动覆盖）：

| 硬件 | 自动方案 | 磁盘需求 |
|---|---|---|
| ≥22GB 显存 + ≥60GB 内存 | Wan2.2-T2V-A14B（画质标杆） | ~135GB |
| ≥8GB 显存 + ≥32GB 内存 | Wan2.2-TI2V-5B（int8/fp8 + 逐层卸载） | ~42GB |
| 无 NVIDIA 显卡 | AnimeDiff 动漫引擎（CPU 可跑） | ~14GB |

运行时数据放在 **exe 同级 `runtime/` 目录**（venv、模型、数据库、生成产物都在里面），删掉它再运行 = 全新安装；整个目录拷走 = 迁移到别的电脑。命令行参数：`--model auto|wan-ti2v|wan-a14b|anime`、`--torch auto|cuda|cpu`、`--port N`、`--no-download`、`--reinstall`。

自己重新打包 exe：在开发机执行 `python packaging/build.py --no-fe`（需先 `cd frontend && npm run build`）。

### 方式三：Windows 桌面端安装包（Electron 壳 + 自动更新）

`desktop/` 是一个 Electron 桌面壳，双击安装后获得一个原生窗口应用：

- **开启 / 关闭服务**：标题栏常驻两个按钮。不用时关闭本软件启动的后端及其子进程，释放本地生成模型的内存/显存，同时停止工作台页面轮询；窗口保留，之后点击开启可返回原作品页面。关闭前提示保存编辑内容，进行中的生成、导出或安装会中断。独立启动的 ComfyUI 和从其他窗口启动的后端需在原窗口关闭。

- **启动流程可视化**：进度卡 + 实时终端（launcher 的全部输出），环境/依赖/模型下载一目了然
- **无系统浏览器**：应用直接内嵌在窗口里（webview 加载本地服务），关闭窗口自动清理后端进程树
- **创建桌面快捷方式**：安装信息卡里一键生成
- **自动更新**：基于 electron-updater + GitHub Releases，启动时自动检查，设置页可手动检查/下载/重启安装

本地构建安装包（开发机）：

```bat
build-desktop.bat          REM 前端 dist → launcher exe → NSIS 安装包（desktop/dist/）
```

发布新版本（推送到 GitHub Releases，用户端自动收到更新）：

```bat
set GH_TOKEN=ghp_xxx       REM GitHub Personal Access Token（repo 权限）
build-desktop-release.bat
```

发布规则：每次发布先提升 `desktop/package.json` 的 `version`（语义化版本），脚本会自动带上最新 launcher.exe——**壳更新 = 前后端全量更新**。用户端在旧版本里即可无感升级（下载完成后重启安装）。

> 想换更新源（GitHub 不稳时）：把 `desktop/electron-builder.yml` 的 `publish.provider` 改成 `generic`，指向自己的 HTTP 目录（需能访问到 `latest.yml` 与安装包）。

### 方式二：源码运行（开发者）

#### Windows 一键安装

双击 **`setup.bat`**：自动检测 Python/Node/显卡 → 创建虚拟环境 → 按显卡装 torch（N 卡 CUDA 版，否则 CPU 版）→ 装依赖（含 torchao 量化库）→ 菜单选模型 → 构建前端。

双击 **`start.bat`** 启动，浏览器打开 <http://127.0.0.1:8000>。

> 没有 Node.js 也能跑：后端 API 与生成功能不受影响，只是没有网页界面。
> 换电脑迁移：整个 `aivideo` 目录（含 `backend/models/`）拷过去即可。

### 手动安装

```bash
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -U pip -i https://mirrors.aliyun.com/pypi/simple/

# torch：无独显用 CPU 版；有 N 卡按驱动选 cu124 / cu121
.venv/Scripts/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu/
# .venv/Scripts/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu124

.venv/Scripts/python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 模型（hf-mirror 镜像）
.venv/Scripts/python.exe scripts/download_models.py                # AnimeDiff（约 3.6GB）
.venv/Scripts/python.exe scripts/download_models.py --wan-ti2v-5b  # Wan 2.2（约 32GB）
```

前端（可选，有 Node.js 时）：

```bash
cd frontend
npm install --registry=https://registry.npmmirror.com
npm run dev        # 开发模式 :5173
npm run build      # 产物由后端单端口托管
```

## Wan 2.2 低显存架构（8GB 显存线）

Wan 2.2 是开源画质标杆，原生 720p/24fps。让它跑进 8GB 显存靠四层机制，全部由 `hw_probe` 自动决策，无需手动配置：

| 机制 | 说明 |
|---|---|
| **模型选择** | 内存 ≥60GB 且显存 ≥20GB → T2V-A14B 双专家；否则 TI2V-5B（bf16 权重约 10GB，32GB 内存轻松装下） |
| **权重量化** | 40 系+（sm_89+）→ torchao fp8 动态量化；30 系 → int8 weight-only；CPU/老卡 → 不量化。量化失败自动退回 bf16 |
| **显存卸载** | 显存 ≥24GB 不卸载；12-24GB 模型级卸载（`enable_model_cpu_offload`）；<12GB **逐层流式卸载**（`enable_sequential_cpu_offload`，8GB 可跑，速度换显存） |
| **VAE tiling** | 720p 解码分块，压低峰值 |

前端生成页的「硬件自适应配置」面板实时展示探测结果与预计耗时。`GET /api/hardware` 也可直接查看。

## 质量档位与两遍采样

**四档质量**（生成页点选，自动映射分辨率/帧数/步数/CFG）：

| 档位 | 参数 | 用途 |
|---|---|---|
| 轻量 | 832×480 · 33 帧 · 20 步 | 日常片段 |
| 标准 | 832×480 · 81 帧 · 30 步 | 完整短片 |
| 精细 | 1280×720 · 81 帧 · 40 步 | 高质量 |
| 极致 | 1280×720 · 121 帧 · 50 步 | 8GB 卡上以小时计 |

**预览与高清候选**：普通 Wan 预览使用 416×256/17 帧/12 步；满意后在画廊点「生成高清候选」，沿用原镜头、提示词、seed、角色和工作流来源，用保存的目标参数重新生成。改变尺寸和帧数可能改变构图。FreeVideo 本地预设使用引擎原生预览，画廊的「继续二采」会引用原预览与 latent 缓存，沿用其目标尺寸。

**视频自带参数**：每个 MP4 内嵌 JSON 生成参数（提示词/分辨率/种子/硬件配置），`GET /api/jobs/{id}/metadata` 可读回，换机器也能复用。

## 模型说明

| 模型 | 大小 | 用途 | 存放目录 |
|---|---|---|---|
| dreamlike-anime-1.0 | ~1.9 GB | AnimeDiff 底模（SD1.5 架构） | `backend/models/anime-diffusers/` |
| animatediff-motion-adapter-v1-5-3 | ~1.5 GB | AnimateDiff v3 运动模块 | `backend/models/animatediff-motion-v3/` |
| anime-motion-unet（预转换） | ~3.4 GB | 运动模块合并进 UNet，AnimeDiff 首次加载自动生成 | `backend/models/anime-motion-unet/` |
| Wan2.2-TI2V-5B | ~32 GB | Wan 2.2 低配首选（8GB 显存线） | `backend/models/wan-ti2v-5b/` |
| Wan2.2-T2V-A14B | ~119 GB | Wan 2.2 画质标杆（MoE 双专家，按需） | `backend/models/wan-t2v-a14b/` |

下载走 `HF_ENDPOINT=https://hf-mirror.com`（huggingface.co 直连不通）。删除对应目录后重跑下载脚本可强制重下。

> **为什么 AnimeDiff 不用单文件 ckpt？** 本环境（torch 2.14 + diffusers 0.41）的 `from_single_file` 有两个坑：需要从 hub 拉 SD1.5 管线配置（镜像小文件经代理会下成 0 字节）；9GB 级 safetensors 加载触发原生段错误。故用 diffusers 格式 + `from_pretrained` 本地加载。

## 导演工作台首页（/）

打开站点先落在这里，一屏看清当前作品的创作状态：

- **标题行**：`当前作品 / 当前幕`（项目名 / 镜头所属场景），右侧「镜头 03 / 28」计数
- **主预览大卡**：当前镜头的视频预览（播放/暂停），底部「状态」徽标（草稿/排队中/生成中/待审核/已通过/需重做）
- **分镜时间线**：横向缩略图轨道，镜头卡右上角状态点，点选即切换预览
- **多轨条**：配音 / 音乐 / 音效 / 字幕，带数量徽标，点击直达剪辑台对应轨道

侧边栏按创作流组织：**我的作品 → 剧本与分镜 → 角色与场景 → 视频生成 → 剪辑工作台 → 声音制作 → 模型与设置**。
「当前作品」会记住你最后进入的项目（localStorage），项目内菜单未选中作品时会有引导提示。

## 分镜工作台（项目 → 剧本 → 分镜 → 生成 → 拼接）

侧边栏「🗂 我的作品」进入，四步做完一支片：

1. **新建项目** → 「📜 剧本」页粘贴剧本文本 → 点「✨ AI 拆分场景与分镜」。
   规则式解析：`场景一：雨夜街道` / `第2场：密室` / `===` 分隔场景；场景内每行一个镜头；
   `主角：台词` 自动识别人物对白并合并进上一条镜头；行内 `(全景, 推, 4秒)` 提取景别/运动/时长。
2. **「🎬 分镜」页**：场景下管理分镜卡片。每张卡片有完整状态机
   （草稿 → 排队中 → 生成中 → 待审核 → 已通过 / 需重做），可拖动排序、独立编辑、生成、审核、重做。
3. **镜头生成**：卡片「🎬 生成」或编辑弹窗里调参后提交。三种来源走同一条队列：
   本地引擎（AnimeDiff / Wan 2.2）或 **ComfyUI 工作流**（需本机跑着 ComfyUI，默认 8188，
   环境变量 `AIVIDEO_COMFYUI_URL` 可改）。每次生成自动落一个**候选版本**（视频 + 提示词 + 模型 +
   参数 + 种子），可对比、采用、删除；改参数重做会产生新版本，不影响已通过版本。
   ComfyUI 任务会保存提交时的服务地址；之后修改设置只影响新任务，旧任务的执行、恢复和预览升级仍使用原服务。
   排队任务取消后镜头回到草稿，可以立即重新生成；正在执行的任务需先取消并等待停止后删除。
4. **「⏱ 时间线」页**：「同步已通过镜头」把片子排进来 → 拖动排序、逐段剪入点/出点、
   淡入淡出转场 → 「🎞 导出 MP4」后台拼接（ffmpeg，统一分辨率/帧率，非等比片段自动加黑边），
   完成后直接下载。导出记录可回看。

### 剧情动画增强（第二批）

5. **「🎭 资产库」页**：给角色建**身份卡**——外貌、服装、正/侧/背/表情参考图、表情预设、
   **固定音色**、关联 LoRA。场景另存环境/光照/布局/风格。分镜绑定角色后，生成时自动把
   外貌+服装拼进提示词（`角色: 外貌, wearing 服装 || 原提示词`）。FreeVideo 本地预设还会将角色参考图与 LoRA 接入实际节点；参考图条件属于实验能力，角色一致性需要逐镜头检查，其他引擎不会自动使用这些视觉条件。
6. **TTS 配音**：分镜编辑弹窗里选音色（edge-tts 14 个中文神经音色，联网；SAPI5 离线兜底）、
   调速，一键把对白念成 MP3；角色卡绑定的音色会被自动选中。对白轨可试听、删除、重生成。
7. **多轨时间线**：视频轨 / 对白轨（TTS 按镜头位置自动落位）/ 音乐轨 / 音效轨 / 字幕轨
   各自独立管理；字幕可从对白**一键自动生成**（SRT），导出时**烧录进画面**（libass，
   失败自动退软字幕）。导出 = 拼接 + 混音（amix，各轨独立音量）+ 字幕，一条命令出成片。
   视频自带原声按剪辑入点、出点和片段位置参与混音；短音乐补静音，长音乐在成片末尾截断。
   删除对白、音乐或音效轨只移除使用记录，保留素材文件，避免破坏其他项目的共享引用；暂不自动回收这些文件。
8. **视频续接与镜头返工**：生成模式四选——文生视频 / 图生视频 / 首尾帧 / **视频续接**
   （自动抽上一镜头末帧做首帧注入工作流）。返工可「仅本镜头」或「连同后续一起」打回重做，
   历史留痕。
9. **质量审核与历史**：审核弹窗先跑自动体检（时长/提示词完整度/对白有无配音/角色参考图/
   版本记录），error 级问题禁止通过；每次审核带意见入库，「📋 历史」页可回溯每次
   通过/打回的时间、意见与当时的检查结果。

ComfyUI 工作流模板在「⚙ ComfyUI」页维护：默认模板（CheckpointLoaderSimple + KSampler + SaveImage，
PNG 序列）开箱即用；把自己从 ComfyUI 导出的 API JSON 整段粘贴进去、按节点填好参数映射
（prompt/negative/宽高/帧数/seed/steps/cfg/**reference_image**），即可用任意自定义工作流
（AnimateDiff、VHS_VideoCombine 出 MP4、图生视频/首尾帧/续接等）生成，进度通过 WebSocket 实时推送。

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/engines` | 引擎列表与状态（含硬件探测） |
| GET | `/api/hardware` | 硬件探测结果与 Wan 推荐配置 |
| GET | `/api/presets` | 质量档位列表 |
| POST | `/api/jobs` | 创建任务（+preset/preview 字段） |
| GET | `/api/jobs` | 任务列表 |
| GET | `/api/jobs/{id}` | 任务详情（含进度/耗时） |
| GET | `/api/jobs/{id}/metadata` | 读回视频内嵌生成参数 |
| POST | `/api/jobs/{id}/cancel` | 取消任务 |
| DELETE | `/api/jobs/{id}` | 删除任务及产物 |
| GET | `/outputs/{id}.mp4` | 视频文件 |
| GET/POST | `/api/projects` | 项目列表 / 创建 |
| GET/PATCH/DELETE | `/api/projects/{id}` | 项目详情 / 改 / 删 |
| PUT/POST | `/api/projects/{id}/script` `/script/import` | 保存剧本 / AI 拆分重建 |
| GET/POST | `/api/projects/{id}/scenes` | 场景列表 / 新建 |
| PATCH/DELETE | `/api/scenes/{id}` | 场景改 / 删 |
| POST | `/api/scenes/{id}/shots` | 新建分镜 |
| PATCH/DELETE | `/api/shots/{id}` | 分镜改 / 删 |
| POST | `/api/shots/{id}/generate` | 分镜发起生成（engine/mode：t2v/i2v/first_last/continuation） |
| POST | `/api/shots/{id}/review` | 审核（approve/rework + 意见，质检快照留痕） |
| POST | `/api/shots/{id}/rework` | 返工（cascade=连同后续镜头） |
| GET | `/api/shots/{id}/quality` | 镜头质量检查 |
| GET | `/api/shots/{id}/versions` | 候选版本列表 |
| POST | `/api/shots/{id}/versions/{vid}/adopt` | 采用某版本 |
| GET/POST | `/api/projects/{id}/characters` | 角色卡列表 / 新建 |
| PATCH/DELETE | `/api/characters/{id}` | 角色卡改 / 删 |
| PUT | `/api/shots/{id}/characters` | 分镜绑定/解绑角色 |
| POST | `/api/uploads` | 上传图片/音频（参考图、音乐、音效） |
| GET | `/api/tts/voices` | TTS 音色目录（14 个中文神经音色） |
| POST | `/api/shots/{id}/voice` | 生成对白配音 |
| DELETE | `/api/voices/{id}` | 删除配音 |
| GET/POST | `/api/projects/{id}/audio-tracks` | 音乐/音效轨列表 / 新增 |
| PATCH/DELETE | `/api/audio-tracks/{id}` | 音轨改 / 删 |
| GET/PUT | `/api/projects/{id}/subtitles` | 字幕列表 / 整轨替换 |
| POST | `/api/projects/{id}/subtitles/auto` | 从对白自动生成字幕 |
| GET | `/api/projects/{id}/subtitles/srt` | 导出 SRT |
| GET | `/api/projects/{id}/timeline` | 时间线片段 |
| POST | `/api/projects/{id}/timeline/sync` | 同步已通过镜头进时间线 |
| POST | `/api/projects/{id}/export` | 创建导出（拼接 + 混音 + 字幕烧录） |
| GET | `/api/exports/{id}` | 导出进度 |
| GET | `/api/projects/{id}/history` | 审核历史 |
| GET/PUT/DELETE | `/api/comfy/status` `/api/comfy/workflow` | ComfyUI 状态 / 工作流模板管理 |
| GET | `/api/comfy/presets` | 内置工作流预设（MiniMax H3 / Wan / AnimateDiff） |
| POST | `/api/comfy/presets/{id}/apply` | 应用内置预设为当前模板 |
| GET | `/api/services/status` | 四类模型服务健康状态（ComfyUI/LLM/图像生成/TTS） |
| GET/PUT | `/api/services/config` `/services/llm` `/services/imagegen` `/services/comfy` | 服务配置读写（key 打码） |
| POST | `/api/services/llm/test` `/api/services/imagegen/test` | 服务连通测试 |
| POST | `/api/llm/script` | AI 编剧（梗概 → 标准格式剧本） |
| POST | `/api/llm/polish` `/api/llm/dialogue` | 提示词优化 / 台词润色 |
| POST | `/api/imagegen/generate` | 图像生成（参考图/概念图/首帧，落盘 outputs/assets/） |

## 调参建议

- **Wan 2.2**：CFG 用 5.0 左右（不是 SD 系的 7.5）；帧数自动吸附到 4n+1；边长自动吸附到 16 倍数。
- **速度预期**（8GB 卡 TI2V-5B sequential offload）：480p 33 帧 20 步 ≈ 10-30 分钟，720p 81 帧 40 步 ≈ 1-3 小时。赶时间用「轻量」档或预览模式。
- **AnimeDiff**：512×512 是甜点位；256×256 快约 8 倍适合试错；提示词吃标签式写法，质量词放开头。
- **种子**：固定 seed + 相同参数 = 可复现同一支视频。

## 目录结构

```
aivideo/
├── setup.bat / start.bat     # Windows 一键安装/启动
├── build-desktop.bat         # 桌面端一键构建（前端→launcher→NSIS 安装包）
├── build-desktop-release.bat # 桌面端发布（打包并上传 GitHub Releases）
├── packaging/
│   ├── launcher.py           # exe 启动器（硬件探测/环境/模型/启动，支持桌面端模式）
│   ├── build.py              # 一键构建单文件 exe
│   └── dist/                 # 构建产物（exe 约 9MB）
├── desktop/                  # Electron 桌面壳（窗口/终端/自动更新）
│   ├── main.js               #   主进程：spawn launcher、IPC、autoUpdater、进程树清理
│   ├── preload.js            #   渲染进程安全桥
│   ├── electron-builder.yml  #   NSIS 打包 + GitHub Releases 发布配置
│   ├── build/icon.png        #   应用图标
│   └── renderer/             #   壳 UI（标题栏/侧栏/进度卡/终端面板，中英 i18n）
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 入口
│   │   ├── config.py          # 路径/镜像配置
│   │   ├── db.py              # SQLite 存储（jobs + 分镜工作台十三张表）
│   │   ├── storyboard.py      # 项目/场景/分镜/版本/时间线数据层 + 剧本解析
│   │   ├── assets.py          # 角色身份卡数据层 + 一致性提示词拼装
│   │   ├── tts.py             # TTS 配音（edge-tts 主 + SAPI5 离线兜底）
│   │   ├── tracks.py          # 多轨道数据层（对白/音乐/音效/字幕 + SRT）
│   │   ├── shotcheck.py       # 镜头质量检查（审核前自动体检）
│   │   ├── llm.py             # LLM 服务（AI 编剧/提示词优化，OpenAI 兼容）
│   │   ├── imagegen.py        # 图像生成服务（OpenAI 兼容 images）
│   │   ├── settings.py        # 服务配置持久化（settings.json）
│   │   ├── comfyui.py         # ComfyUI 客户端（提交/WS 进度/工作流模板/预设）
│   │   ├── export.py          # 导出 Worker（拼接 + 混音 + 字幕烧录）
│   │   ├── queue_manager.py   # 任务队列（取消/进度/耗时/元数据/分镜联动）
│   │   ├── hw_probe.py        # 硬件探测器（模型/量化/卸载决策）
│   │   ├── quality.py         # 质量档位与两遍采样
│   │   ├── video.py           # 帧 → MP4、时长/分辨率探测、拼接、末帧抽取
│   │   ├── engines/           # 可插拔引擎
│   │   │   ├── base.py        #   引擎抽象接口
│   │   │   ├── anime.py       #   AnimeDiff（CPU/GPU 自适应）
│   │   │   ├── wan.py         #   Wan 2.2（offload+量化）
│   │   │   └── comfy.py       #   ComfyUI 工作流引擎
│   │   └── routers/           # REST API（jobs / engines / storyboard / assets / services）
│   ├── comfy_workflows/       # ComfyUI 工作流模板（default.json + presets/ 内置预设）
│   ├── scripts/
│   │   ├── download_models.py # 模型下载（AnimeDiff/Wan）
│   │   ├── e2e_storyboard.py  # 分镜工作台端到端测试
│   │   ├── e2e_assets.py      # 资产/配音/多轨/审核端到端测试
│   │   ├── e2e_services.py    # LLM/图像生成/预设端到端测试（内置 mock 服务）
│   │   └── smoke_engine.py    # 引擎级冒烟测试
│   ├── models/                # 模型权重（git 忽略）
│   └── outputs/               # 生成产物（git 忽略）
└── frontend/
    └── src/
        ├── views/             # 导演工作台首页 / 生成 / 队列 / 画廊 / 项目工作台 / ComfyUI 设置 / 模型服务
        ├── components/        # 分镜卡片、编辑弹窗、审核弹窗、角色卡弹窗
        ├── store/             # Pinia 状态（jobs / project）
        └── api.js             # API 封装
```

## 已知限制

- 8GB 卡跑 Wan 2.2 是「速度换显存」，sequential offload 下每步都要搬运权重，耐心是美德。
- 单 worker 顺序执行，同时提交多个任务会排队。
- TI2V-5B 第一版仅文生视频（图生视频输入后续加）；A14B 需要 60GB+ 内存，32GB 机器别勉强。
- Wan 2.2 权重为 fp32 存储（transformer 18.6GB），bf16 加载后约 10GB；量化在加载时进行，首次启动多等 1-2 分钟。
- ComfyUI 引擎需要一台运行中的 ComfyUI 服务器（默认 127.0.0.1:8188，地址可在「模型服务」页修改）；
  「ComfyUI 设置」页提供内置预设一键应用：**MiniMax H3 文/图生视频**（需 MiniMax 自定义节点）、
  **Wan 2.2 TI2V-5B**、**AnimateDiff Evolved**（需 VideoHelperSuite），也可粘贴自己的 API JSON。
- LLM / 图像生成默认未配置（OpenAI 兼容协议，支持 DeepSeek/通义/Kimi/Ollama 及任意兼容端点），
  在「🧩 模型服务」页填写后即用；未配置时相关按钮会给出明确引导，不影响其余功能。
- 图生视频/首尾帧/视频续接的完整实现依赖 ComfyUI 自定义工作流（在 `_aivideo_map` 里加
  `"reference_image": {"node": "<LoadImage节点ID>", "input": "image"}` 映射即自动注入）；
  本地 AnimeDiff/Wan 引擎会记录参考图与续接关系，并退化为同场景衔接提示词。
- TTS 配音默认走 edge-tts（需联网访问微软服务）；离线环境自动退到 Windows SAPI5 系统语音，
  音色自然度会下降。字幕烧录依赖 libass（imageio-ffmpeg 已内置），失败时自动改为软字幕。
- 续接模式默认抽「上一镜头」末帧；跨场景续接需在编辑弹窗里手动选来源镜头。
