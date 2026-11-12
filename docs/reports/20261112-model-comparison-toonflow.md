# ToonFlow 与 NMVideo 模型栈对比

> 2026-11-12 · 依据：ToonFlow 模型状态面板截图（本地推理侧）+ 本机 `G:/AI/FreeVideo` 实测落盘 +
> 本仓库代码（`h3_freevideo.py` / `h3_quant.py` / `model_store.py`）。

## 0. 一句话结论

截图里那 6 行**不是 6 个不同模型**，而是一条本地推理链路的 6 个部件，主体是
**MiniMax H3（VDN 8 步蒸馏，int8 量化）+ Qwen3-VL + Qwen-Image 2.1** 这一套。
我们这边跑的是**同一套 H3 权重**（同一个 FreeVideo 插件通道），真正的差别不在视频模型，
而在两处：

1. **本地生图**：ToonFlow 面板里有「MiniMax H3 Image」「图像生成模型」两行本地权重，
   我们本地没有生图链路，角色卡/分镜图只能走在线 API。
2. **本地提示词模型**：ToonFlow 单列一行「提示词模型」（Qwen3-VL 8B 量级），我们全靠在线 LLM。

## 1. 取数口径

| 来源 | 可信度 | 说明 |
|---|---|---|
| 本机 `G:/AI/FreeVideo/**/models`、`prepared/` | **实测** | 逐目录字节统计，模型文件名直接读出来的 |
| 本仓库 `backend/app/h3_freevideo.py` 等 | **实测/标定** | 门槛与体积写死在代码里，有 `MEASURED_AT` |
| ToonFlow 截图 6 行 | **读图推断** | 只有界面标签和「已就绪/已暂停」，没有源码或下载清单 |
| ToonFlow README / 公开教程 | **公开资料** | 仓库 README、部署教程；用于确认它的接入方式 |

**重要**：ToonFlow 那 6 行的具体文件名我**没有**核对到（没装它，仓库里也没抓到这份清单）。
表格里凡是推断的都标了「待核实」，别当成定论用。

## 2. ToonFlow 那 6 行对应什么

ToonFlow 本身是**编排层**（画布 + Agent + 供应商适配），README 明确「可配置语言/图片/视频模型 API，
也可接入本地 ComfyUI 和 LLM」。所以这份面板是它**本地推理侧**的资源清单，权重来自
VDN-H3 那一套 —— 和我们本机 FreeVideo 装的是同一族。

| 面板行 | 状态 | 对应权重（推断） | 体积 | 作用 | 证据 |
|---|---|---|---|---|---|
| 视频模型 | 已就绪 | MiniMax H3 · VDN 8 步蒸馏 · `int8-convrot` 预量化，50 个 transformer 块 | ≈21.4 GiB | 出画面的主干 | 与本机 `prepared/edge-da37681f5a1f7015/int8-convrot`（22G）同名同族 |
| 提示词模型 | 已就绪 | Qwen3-VL（`prompt-vlm`，hidden 2560×36 层 ≈ 8B，bf16） | ≈8.3 GiB | 写/扩写视频提示词，理解参考图 | 本机 `models/prompt-vlm/ebb281ec…`（8.3G），config 为 `Qwen3VLForConditionalGeneration` |
| 视频与音频解码器 | 已就绪 | H3 VAE 3 分片 + `audio_vae` + `latent_upscaler` | ≈10.3 GiB | 潜变量还原画面与声音；低显存按块流式 | 本机 `h3-base/vae/*-0000{1,2,3}-of-00003.safetensors` + `audio_vae` + `latent_upscaler`(659M) |
| 采样缓存 | 已就绪 | `sampling-cache/adaln-tables-v2-*`（13 份）+ `stage-dmd-step-250` turbo 适配器 | ≈3.3 GiB | 8 步蒸馏所需的预计算表与加速权重 | 本机 `vdn/sampling-cache`(3.0G) + `stage-dmd-step-250/adapters/turbo` |
| MiniMax H3 Image | **已暂停** | `MiniMaxAI/MiniMax-H3-Image`：int8 ConvRot DiT + 专用 VAE + 复用视频的 Qwen3-VL-32B 编码器 | 19.54 + 1.11（编码器复用 14.61） | 静图/角色卡，走 H3 自己的图像管线 | **已核实**（FreeVideo `image_gen/image_models.json` 的 `h3-image:*` 组） |
| 图像生成模型 | 已就绪 | Qwen-Image 2.1 Turbo `int8_convrot` + Qwen3-VL-8B `int8_convrot` 编码器 + Qwen-Image VAE bf16 | ≈16.1 GiB | 角色卡、场景图、分镜首帧 | 本机 `models/vdn/image_gen/**`（6.8G 扩散 + 8.8G 编码器 + 645M VAE） |

两条推断的边界说明：

- 「提示词模型」不等于视频链路的**文本编码器**。视频那条用的是
  `qwen3vl_32b_minimax_h3_nvfp4_awq`（14.6 GiB，32B 级、nvfp4+AWQ），是**条件输入**；
  「提示词模型」是**替你写字**的 VLM（8B 级）。两个都是 Qwen3-VL，但不是一个东西，不能混算体积。
- 「MiniMax H3 Image」和「图像生成模型」是**两套不同权重**（08:5x 核实）：
  前者是 `MiniMaxAI/MiniMax-H3-Image` 的 int8 DiT + 专用 VAE，后者是 Qwen-Image 2.1 Turbo。
  面板上一行暂停、一行就绪，正好对应「H3 Image 没下、Qwen-Image 下了」。

## 3. 我们这边（NMVideo）现有模型清单

| 链路 | 权重 | 体积 | 门槛 | 状态 |
|---|---|---|---|---|
| `h3`（ComfyUI 通道，节点 `FreeVideoGenerate`） | **视频**：MiniMax H3 `int8-convrot` 50 块分片<br>**文本编码器**：Qwen3-VL-32B `nvfp4+awq`<br>**解码器**：H3 VAE + audio VAE + latent upscaler<br>**采样缓存**：adaln tables + turbo 适配器 | 21.35 + 14.61 + 10.26 + 3.28<br>= **49.50 GiB**（需 57.5 GiB 磁盘） | 8GB 显存 / 32GB 内存 | 可用，本机实测跑通（512²/25 帧首次 16s） |
| `h3_native`（进程内 diffusers，原生 H3） | bf16 transformer 61.7 + 文本编码器 62.1 | **123.8 GiB**（磁盘 139GB） | 内存决定量化档：int4 需 52GB 起 | 兜底路径，未作为默认 |
| `wan22` | Wan2.2-TI2V-5B / T2V-A14B | 32GB / 119GB | 低显存档 / 20GB+ 显存档 | 可选下载，不默认拉 |
| `anime` | dreamlike-anime-1.0 + 运动模块 | ≈5GB | CPU 可跑 | 历史路径，待收敛 |
| 生图（角色卡/分镜图） | **无本地权重**，OpenAI 兼容 `images` 端点 | — | 需在线服务 | 可配但依赖外部 |
| 编剧/提示词 | **无本地权重**，OpenAI 兼容 `chat` 端点 | — | 需在线服务 | 可配但依赖外部 |
| 配音 | edge-tts（在线）/ pyttsx3 SAPI5（离线） | — | — | 双引擎兜底 |

## 4. 逐项对比

| 维度 | ToonFlow | NMVideo | 判断 |
|---|---|---|---|
| 视频主干模型 | MiniMax H3 VDN 8 步蒸馏 | 同一套（走 FreeVideo 插件节点） | **打平**，都是 H3 家族 |
| 视频画质档 | 面板只显示「已就绪」，档位在生成节点里 | 四档显式落库（轻量/标准/精细/极致 = 步数 + 两遍 + 步缓存） | 我们更透明 |
| 文本编码器 | 未单列（推测并入视频包） | 单列，14.61 GiB 计入门槛 | 我们的门槛算得实 |
| 提示词模型 | **本地 Qwen3-VL 8B**，单列一行 | 无（只用在线 LLM） | **我们缺** |
| 本地生图 | **有**（Qwen-Image 2.1 Turbo 一套 ≈16 GiB） | 无 | **我们缺（最大差距）** |
| 解码器 / 采样缓存 | 分成两行显示状态 | 算在四包内，界面不细拆 | 它更可读 |
| 量化口径 | 不对外讲 | int8-convrot 写死字节数，改体积必须重测 | 我们更严谨 |
| 接入方式 | 供应商 API 为主，本地 ComfyUI 为辅 | 本地 ComfyUI 为唯一底座 | **定位不同** |
| 硬件承诺 | 面板不谈门槛 | 8GB/32GB/57.5GB 明码标 | 我们更负责 |
| 状态表达 | 每行一个「已就绪/已暂停」 | 引擎卡 + 首启检测 | 它更像「资源管理器」 |

### 4.1 MiniMax H3 Image 我们能不能用（2026-11-12 核实）

分两问，答案不同，别混着讲。

**技术上：能用，而且门槛不高。** FreeVideo 引擎确实带这条管线：

- `freevideo_engine/image_gen/h3_runner.py` 的 docstring 写明「the image worker uses for a request
  that names `minimax-h3-image`」，采样走 `h3_pipeline.generate`（提示词 → 视频那个文本编码器
  → DiT → tiled VAE）；
- 权重清单在 `image_gen/image_models.json`，分三组：

| 组 | 内容 | 体积 |
|---|---|---|
| `h3-image-dit-int8` | `h3-image/diffusion_models/minimax_h3_image_pruned_int8_convrot.safetensors` | **19.54 GiB** |
| `h3-image-core` | 专用 VAE `minimax_h3_image_vae_fp16`（1.09G）+ `pdd_fused_heads` + config/tokenizer | 1.11 GiB |
| `h3-text-encoder` | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 14.61 GiB（**与视频链路同一个文件，本机已有，不用重下**） |

  所以增量只有 **≈20.65 GiB**（DiT + core），我们 G 盘余 64GB，装得下。
- 硬件：image_policy 是按「权重全流式」设计的（`host_weights_bytes = 14.14 GiB`、
  每层 0.18 GiB、每块 0.2035 GiB、`MIN_HOST_WORKING = 4 GiB`），8GB 卡能跑，
  内存要 ≈18GB+（本机 32GB 没问题）。1024² 单图峰值按公式约 2.7 / 1.6 / 2.3 GiB 三段。

**在 NMVideo 里：现在接不进来。** 三条硬事实：

1. FreeVideo 没把生图做成 ComfyUI 节点 —— 它注册的节点只有 `FreeVideoGenerate`(视频+音频)、
   `FreeVideoMedia`、`FreeVideoReference`、`FreeVideoLoRA`、`FreeVideoLoRAs`、
   `FreeVideoFrames`（首尾帧）。
2. 生图跑在它自己的 image worker（`image_gen/service.py` + `worker.py`）里，由 FreeVideo
   桌面端/agent 驱动；插件注册的 HTTP 路由里**没有** `/freevideo/image` 之类端点。
3. ComfyUI 本体（0.34.0）只注册了 `MiniMaxH3`（音视频 AV），
   `comfy/model_detection.py` 认得 `video_patch_proj` + `audio_patch_proj` 那套；
   **没有** H3-Image 的 supported_model，所以原生 `UNETLoader` 也加载不了那个 DiT。

按你定的边界（只走 ComfyUI 官方接口，不碰 FreeVideo 内部），这条路目前是断的。

> **2026-11-12 决定**：先不动，等 H3 Image 权重下完再复验。
> 复验时要分清两件事——**权重到位**不会自动解决**入口缺失**：
> 上面第 1、2、3 条都是代码结构问题，装完包也不会变。所以复验时先查
> ComfyUI `/object_info` 里有没有 H3-Image 相关节点，没有就直接走 A 路径。

**三条可选路径：**

| 路径 | 做法 | 代价 | 边界 |
|---|---|---|---|
| A（推荐） | 用 **Qwen-Image 2.1 Turbo** 做本地生图。它是 `Comfy-Org/Qwen-Image-2.1` 的 ComfyUI 格式，ComfyUI 有 `QwenImage` supported_model 和 `qwen3vl` 文本编码器，理论上 `UNETLoader + CLIPLoader + VAELoader + KSampler` 能直接跑 | 16.11 GiB（本机已下） | **在界内** |
| B | 接 FreeVideo 的 image worker | 要调它的内部服务/读它的目录 | **破界，不建议** |
| C | 继续用在线 API 生图 | 花钱、断网即失效 | 界内但没解决问题 |

A 路径尚需一次真机验证（加载是否被 ComfyUI 认、8GB 卡能不能跑 1024²），
没验之前别在界面上写「已支持」。

### 4.2 如果确定要走 H3 Image：唯一可用的入口是 FreeVideo 的 Agent API

2026-11-12 追加核实：H3 Image 不是完全没入口 —— FreeVideo 插件在 ComfyUI 进程里注册了一套
**REST API**（`/freevideo/agent/v1/*`，自带 `/schema`、`/guide`、`/openapi.json`），
图片是其中的 `kind: "image"` 任务，`model` 可选 `minimax-h3-image`。

这是**它自己的 API，不是 ComfyUI 官方接口**，用之前要认这条边界变更。除此之外没有别的门：
没有 ComfyUI 节点、ComfyUI 本体也不支持该架构。

**调用契约（读 `agent/schema.py` + `agent/jobs.py` 得到）**

```
POST http://127.0.0.1:8188/freevideo/agent/v1/jobs
X-Agent-Token: <token>            # 或 Authorization，见 /freevideo/agent/connect
{
  "kind": "image",
  "prompt": "...",
  "model": "minimax-h3-image",
  "aspect": "16:9",
  "tier": "standard",             # standard≈4MP / high≈16MP，仅 H3 Image 用
  "width": 2048, "height": 1152,  # 与 aspect 二选一
  "seed": 12345,
  "images": [{"file": "<上一步产物>"}]   # 可选，最多 4 张，作参考/改图
}
→ GET  /freevideo/agent/v1/jobs/{id}       任务状态
→ GET  /freevideo/agent/v1/media/{kind}/{file}/file   取图
```

**前置条件**

1. 权重：`h3-image-dit-int8`（19.54 GiB）+ `h3-image-core`（1.11 GiB），
   文本编码器复用本机已有的 14.61 GiB → **增量 ≈20.65 GiB**（G 盘余 64GB）。
2. ComfyUI 起着（插件加载后 API 才挂上 8188，和视频通道同一个端口）。
3. Token：`<FreeVideo 安装根>/agent/token`，或调 `GET /freevideo/agent/connect` 让它把
   `token_file` 路径告诉你。**这是它自己文档的配对方式**，但客观上要读它的一个凭证文件 ——
   与「不读服务端文件系统」那条边界有摩擦，需要明确同意。

**两个预期要提前说清**

- **H3 Image 的尺寸下限是 4MP**（`at_least_four_megapixels`），不像 Qwen 可以 1024²。
  16:9 standard 大约 2667×1500；8GB 卡靠全流式跑，单图可能到分钟级。
- 画质风格与视频那条 H3 一致（同一个家族、同一个文本编码器），对「出片前后风格统一」是加分项。

**落地顺序（建议）**

1. 权重下完 → 起 ComfyUI → 查 `/object_info` 确认**没有** H3-Image 节点（预计没有）。
2. 拿 token，`GET /freevideo/agent/v1/schema` 核对 `minimax-h3-image` 是否在 enum 里。
3. 先 `POST /plan` 干跑一次，确认参数校验通过，再 `POST /jobs` 真跑。
4. 8GB 卡先试 `standard`，记录耗时/显存；慢了再评估要不要降尺寸或放弃。
5. 跑通后才把 NMVideo 的 `imagegen` 加一个 `backend=freevideo_agent` 分支；
   没跑通就保持在线 API，别写死。

**风险**：这条 API 是 FreeVideo 的内部契约，版本升级可能改字段（插件版本 2026.11.11 的
`image` schema 我核对过，字段如上）。接入时要把 `/schema` 当运行时真值，别把字段写死在代码里。

## 5. 差距结论

**我们不缺视频模型，缺的是"进片之前"和"片外"的两环：**

1. **本地生图（P0）** —— ToonFlow 的角色四连图、场景图、分镜图全部本地出，
   不烧 API 钱、不怕断网、出图风格可控。我们目前 `imagegen` 只能配在线端点，
   一旦没配，角色卡/首帧这条链就断了。
2. **本地提示词模型（P1）** —— 它有 8B 级 VLM 在本地把一句话扩成合规提示词，
   我们是直接把这活交给在线 LLM。对「离线可用」这个卖点是硬伤。
3. **资源可读性（P2）** —— 它把「视频 / 提示词 / 解码器 / 采样缓存 / 生图」拆成 6 行给状态，
   我们只给 4 个包的合计体积。用户看不出 3.28 GiB 的采样缓存是干什么的。

**我们比它强的地方别丢**：门槛核算（8GB 卡真能跑，且提前说清要多久）、
画质档与存档参数一致（不静默截断）、量化体积可复测。这些是踩过坑换来的，别为了"看起来像 ToonFlow"降级。

## 6. 建议（按优先级）

- **P0 · 把本地生图接进来**：走 4.1 的 **A 路径** —— Qwen-Image 2.1 Turbo 一套
  （int8 DiT 6.8G + Qwen3-VL-8B 编码器 8.8G + VAE 645M，≈16.1 GiB，本机已下），
  以**原生 ComfyUI 图**加载，不碰 FreeVideo 的 image worker。
  先做**能力探测**：本机 8GB 卡跑 1024² 是否可行、要不要降到 768²。跑通前界面明确写「未验证」。
  注：H3 Image（20.65 GiB 增量）ComfyUI 加载不了，别当成备选。
- **P0 · 模型页照它的信息结构改**：每行 = 功能名 + 状态 + 体积 + 一句作用，
  点击展开真实文件名与路径。用户要的是"这 3.28G 是什么"，不是"已就绪"。
- **P1 · 本地提示词模型作为可选包**：`prompt-vlm` 8.3 GiB，装了就走本地扩写，
  没装回落在线 LLM —— 不要做成强制，8GB 机器的磁盘很紧。
- **P1 · 门槛表补两行**：生图 16.1 GiB、提示词 8.3 GiB 标成"可选"，不计入视频门槛，
  但要在磁盘规划里让用户看见它们。
- **不做**：不追 ToonFlow 的供应商市场（我们底座是 ComfyUI，定位不同）；不搬它的画布交互。

## 7. 待核实项 / 怎么复核

| 待核实 | 复核方法 | 状态 |
|---|---|---|
| 「MiniMax H3 Image」与「图像生成模型」是否同一包 | 查 FreeVideo `image_gen/image_models.json` 分组 | **已核实：两套不同权重**（H3-Image vs Qwen-Image 2.1） |
| H3 静图能力是否存在 | 查 ComfyUI 节点与 supported_models | **已核实：能力存在（FreeVideo 侧），ComfyUI 原生不支持** |
| ToonFlow 面板那 6 行的具体落盘路径 | 装一份 ToonFlow，进设置→本地模型看详情 | 未核实（不影响结论） |
| 本地生图在 8GB 卡上的实际可用性 | 走 A 路径：把 Qwen-Image 2.1 Turbo 放进 ComfyUI `models/`，跑 1024²/768²，记录耗时与显存峰值 | **待执行（下一步）** |

---

**附：本机实测字节数**（`du`/`find` 逐目录，2026-11-12 复量，与 11-01 口径一致）

```
prepared/edge-da37681f5a1f7015/int8-convrot   22G
models/encoder/text_encoders/qwen3vl_32b_…_nvfp4_awq.safetensors   15G
models/vdn/h3-base                            11G   (vae 3 分片 + audio_vae，transformer 仅 config)
models/vdn/latent_upscaler                   659M
models/vdn/sampling-cache                    3.0G
models/vdn/image_gen                          17G   (diffusion 6.8G + encoder 8.8G + vae 645M)
models/prompt-vlm/ebb281ec…                  8.3G
```
