# 🎬 AI 视频工坊（aivideo）

本地运行的 AI 文生视频生成器，**可插拔引擎架构**。参考 FreeVideo 的低显存设计哲学：硬件自适应（模型选择/量化/卸载全自动）、四档质量、两遍采样、视频自带参数。

| 引擎 | 模型 | 最低硬件 | 出片速度 |
|---|---|---|---|
| **AnimeDiff 动漫** | dreamlike-anime + AnimateDiff v3 | 纯 CPU，8GB 内存 | 256px 8 帧 12 步 ≈ 4 分钟（i5 CPU） |
| **Wan 2.2 文生视频** | Wan2.2-TI2V-5B | **8GB 显存 + 32GB 内存** | 480p 33 帧 20 步 ≈ 10-30 分钟（8GB 卡） |
| Wan 2.2 画质档 | Wan2.2-T2V-A14B（MoE 双专家） | 20GB+ 显存 + 60GB+ 内存 | 720p 81 帧 30 步 ≈ 30-90 分钟 |

## 架构

```
┌────────────────────────────────────────────────────────┐
│  前端 Vue3 + Pinia（生成页 / 队列页 / 画廊）              │
│  vite dev :5173  ──代理──▶                              │
└──────────────────────┬─────────────────────────────────┘
                       │ REST API
┌──────────────────────▼─────────────────────────────────┐
│  后端 FastAPI :8000                                     │
│  ├─ 任务队列（单 worker，SQLite 持久化，取消/进度/耗时）   │
│  ├─ 硬件探测器 hw_probe（GPU/显存/架构 → 模型+量化+卸载） │
│  ├─ 质量档位 quality（四档预设 + 两遍采样）               │
│  ├─ 引擎注册表（可插拔）                                  │
│  │   ├─ AnimeEngine   AnimeDiff，CPU/GPU 自适应          │
│  │   └─ WanEngine     Wan 2.2，offload+量化，8GB 可跑    │
│  └─ ffmpeg 合成（imageio-ffmpeg 内置二进制 + 参数元数据） │
└─────────────────────────────────────────────────────────┘
```

## 快速开始

### 方式一：单文件 exe（推荐，任意 Windows 10/11 电脑）

从 `packaging/dist/` 拷贝 **`AI视频工坊-v1.0.0.exe`**（约 9MB）到目标电脑，双击运行，全程自动：

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

**两遍采样**（借鉴 FreeVideo「先出预览」）：勾选「先出预览」→ 第一遍用 416×256/17 帧/12 步快速出构图（分钟级）→ 画廊里对满意的预览点「**⬆ 升级渲染**」，同 seed 同提示词跑全质量档。

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

## 调参建议

- **Wan 2.2**：CFG 用 5.0 左右（不是 SD 系的 7.5）；帧数自动吸附到 4n+1；边长自动吸附到 16 倍数。
- **速度预期**（8GB 卡 TI2V-5B sequential offload）：480p 33 帧 20 步 ≈ 10-30 分钟，720p 81 帧 40 步 ≈ 1-3 小时。赶时间用「轻量」档或预览模式。
- **AnimeDiff**：512×512 是甜点位；256×256 快约 8 倍适合试错；提示词吃标签式写法，质量词放开头。
- **种子**：固定 seed + 相同参数 = 可复现同一支视频。

## 目录结构

```
aivideo/
├── setup.bat / start.bat     # Windows 一键安装/启动
├── packaging/
│   ├── launcher.py           # exe 启动器（硬件探测/环境/模型/启动）
│   ├── build.py              # 一键构建单文件 exe
│   └── dist/                 # 构建产物（exe 约 9MB）
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 入口
│   │   ├── config.py          # 路径/镜像配置
│   │   ├── db.py              # SQLite 任务存储
│   │   ├── queue_manager.py   # 任务队列（取消/进度/耗时/元数据）
│   │   ├── hw_probe.py        # 硬件探测器（模型/量化/卸载决策）
│   │   ├── quality.py         # 质量档位与两遍采样
│   │   ├── video.py           # 帧 → MP4（含参数元数据）
│   │   ├── engines/           # 可插拔引擎
│   │   │   ├── base.py        #   引擎抽象接口
│   │   │   ├── anime.py       #   AnimeDiff（CPU/GPU 自适应）
│   │   │   └── wan.py         #   Wan 2.2（offload+量化）
│   │   └── routers/           # REST API
│   ├── scripts/
│   │   ├── download_models.py # 模型下载（AnimeDiff/Wan）
│   │   └── smoke_engine.py    # 引擎级冒烟测试
│   ├── models/                # 模型权重（git 忽略）
│   └── outputs/               # 生成产物（git 忽略）
└── frontend/
    └── src/
        ├── views/             # 生成 / 队列 / 画廊
        ├── store/jobs.js      # Pinia 状态
        └── api.js             # API 封装
```

## 已知限制

- 8GB 卡跑 Wan 2.2 是「速度换显存」，sequential offload 下每步都要搬运权重，耐心是美德。
- 单 worker 顺序执行，同时提交多个任务会排队。
- TI2V-5B 第一版仅文生视频（图生视频输入后续加）；A14B 需要 60GB+ 内存，32GB 机器别勉强。
- Wan 2.2 权重为 fp32 存储（transformer 18.6GB），bf16 加载后约 10GB；量化在加载时进行，首次启动多等 1-2 分钟。
