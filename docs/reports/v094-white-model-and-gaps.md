# 白模替换流程 + 未完成清单

版本：v094（2026-11-10）
基线：`main` @ 本批提交前（上一提交 `d6d60a1`）
文档性质：**实现记录 + 缺口清单**。"现状"结论来自对 `main` 的代码阅读与 ComfyUI `/object_info` 实测；
"未验证"一律显式标注，不含推测性结论。

配套画布：`NMVideo 白模替换流程`（file id `745988949872858`，三屏：全链路 / 素材职责分离 / 九段职责）

---

## 0. 一句话

把「以白模视频为动作与镜头参考、替换人物与场景」这套写法，从一段提示词**落成了可组装的流程**；
但**白模视频本身的生成还没有接进生成模式**——链路已经查清、节点和权重都在位，缺的是一个新模式。

---

## 1. 本批做了什么

| 项 | 文件 | 状态 |
|---|---|---|
| 白模替换九段职责提示词 | `app/white_model.py`（新） | 已实现，有 11 例单测 |
| v2v 模式改用九段提示词 | `app/routers/storyboard.py` | 已接入 |
| 白模生成能力探测 | `app/routers/white_model.py`（新） | 已实现，实测 ComfyUI 停机时如实报错 |
| 探测函数 COMBO 形状 bug | `app/routers/white_model.py` | **已修**（下面 §4.2 有说明） |

### 1.1 为什么原来的拼装不够

v2v 之前用的是 `角色 ‖ 动作 ‖ 场景` 三段拼接（`compose_prompt_with_characters` /
`compose_motion_prompt` / `compose_prompt_with_scene`）。这三段是给 t2v/i2v 用的，**保留不动**。

但白模替换的失败模式和它们不一样：问题不是"描述得不够好"，而是**没人告诉模型"这份素材不负责这一项"**。
所以会出现——白模的灰色皮肤留在脸上、参考图的静态站姿被当成动作、场景参考图里的人一起画进去。

九段结构里真正起作用的是**第 02 段「参考素材职责」**：每份素材都要写明「不采用」什么。

---

## 2. 九段结构（对齐「神之八秒挑战」那套写法）

```
01 生成目标        这一版在做什么
02 参考素材职责     每份素材管什么、不采用什么   ← 真正起作用的一段
03 人物外观        全片只出现一个身份，跨景别一致；原白模外观全部替换
04 舞蹈与动作继承   起/加/停/衔接/结束跟随原片，不重编不简化
05 近镜手势与透视   近大远小、手部进出前景的路径与时机
06 表情与口型      视线/头部角度/眉眼/嘴角，不固定微笑
07 镜头继承        镜头顺序/景别/机位高度/推进速度/切镜时机
08 场景与光影融合   连续三维环境，视差透视遮挡变化，同一光源
09 声音与收束      保留原音轨与画声节拍，不新增对白音效
```

三条设计原则（写进代码注释了）：

1. **职责是数据，不是文案。** 谁承担动作/身份/环境由参数决定，段文案从职责派生。
2. **必须写「不采用」。** 只写"参考什么"，模型会连不想要的一起参考。
3. **缺素材时改写，不留空。** 没人物图时第 03 段改写为"以本段描述为准"，
   **并且不再引用「图1」**——否则提示词里出现一个不存在的素材名。

### 2.1 素材职责对照（画布第二屏）

| 素材 | 负责 | 不采用 |
|---|---|---|
| `参考视频1`（白模） | 动作 · 姿态 · 运动路径 · 表情 · 口型 · 镜头运动 · 构图 · 剪辑节奏 · 镜头顺序与景别 · 机位高度与拍摄方向 · 推进后退 · 运动速度 · 切镜时机 | 白模人物的面容、光头、吊带短裤、白色材质、空白背景 |
| `图1`（人物） | 面容 · 发型 · 服装 · 配饰 · 身体比例 · 外观风格 | 图中的静态站姿、原图背景 |
| `图2`（场景） | 空间结构 · 地面 · 环境材质 · 色调 · 光源方向与氛围 | 图中的任何人物 |

---

## 3. 白模生成：链路的实证结论

### 3.1 现有 v2v 预设在本机**不可执行**

`comfy_workflows/presets/minimax_h3_v2v.json` 的 `_meta.required_nodes` 里有 **`VHS_LoadVideo`**，
实测本机 **`/object_info` 里没有这个节点**（VideoHelperSuite 未装）。也就是说：

> 白模替换（v2v）在本机**跑不了**——不是提示词问题，是缺节点。
> 能力中心探测会拦下它（`check_missing_nodes`），这一点是**对的行为**。

### 3.2 但白模生成需要的节点本机齐了

`/object_info` 快照（2026-11-10 09:41，928 节点）实测：

| 节点 | 输出 | 必需输入 | 在位 |
|---|---|---|---|
| `LoadVideo` | `VIDEO` | `file`（COMBO，ComfyUI input 目录里的文件） | ✓ |
| `GetVideoComponents` | `IMAGE, AUDIO, FLOAT, COMBO, COMBO` | `video` | ✓ |
| `SAM3DBody_Loader` | `SAM3D_BODY_MODEL` | `model_file` | ✓ |
| `SAM3DBody_Predict` | `MHR_POSE_DATA` | `sam3d_body_model, image, run_hand_refinement, fov, batch_size` | ✓ |
| `SAM3DBody_Render` | `IMAGE` | `pose_data, width, height, render_style` | ✓ |
| `SAM3DBody_Smooth` | `MHR_POSE_DATA` | `mhr_pose_data, strength, method, window, rotation_threshold_degrees` | ✓（可选） |
| `BuildPoseFile` | `FILE_3D` | `pose_data, format, fps, camera_translation, track_index` | ✓（导 BVH/GLB 用） |
| `CreateVideo` | `VIDEO` | `images, fps` | ✓ |
| `SaveVideo` | `VIDEO` | `video, filename_prefix, format` | ✓ |

权重：`SAM3DBody_Loader.model_file` 枚举 = **`sam_3d_body_dinov3_bf16.safetensors`（在位）**。

### 3.3 目标链路（推自节点签名，未实跑）

```
LoadVideo(file=参考视频)
  → GetVideoComponents(video)              → IMAGE 帧 + AUDIO
  → SAM3DBody_Loader(model_file)           → SAM3D_BODY_MODEL
  → SAM3DBody_Predict(model, image, fov=0, batch_size=64)   → MHR_POSE_DATA
  → SAM3DBody_Render(pose_data, width=0, height=0, render_style="mesh") → IMAGE（灰白人体）
  → CreateVideo(images, fps=24)            → VIDEO
  → SaveVideo(video, format="mp4")         → 落盘
```

参数语义已核对（来自 `/object_info` 的 tooltip，不是猜的）：

- `SAM3DBody_Predict.fov` = **0 → 回退约 53°（16:9）**，所以用 0，不要瞎填 45。
- `SAM3DBody_Render.width/height` = **0 → 用 pose data 的原生尺寸**，所以用 0。
- `SAM3DBody_Render.render_style` 可选：`mesh`（真白模网格）/ `silhouette`（剪影）/
  `openpose_2d` / `openpose_3d` / `scail`。

### 3.4 为什么没直接做成可选用预设

两个硬阻塞，都是**不能糊过去**的：

1. **NMVideo 没有 whitemodel 生成模式。** `workflow_capabilities.SUPPORTED_MODES =
   ("t2v","i2v","first_last","continuation","v2v")`。白模生成是"视频→灰白视频"，
   套 `v2v` 会触发"必须有角色参考图"的校验，语义也不对。
   而如果硬塞一个 `supports: ["whitemodel"]`，`capability_from_template` 会把未知模式过滤掉、
   然后回落成"支持全部模式"——**反而变成假声明**。
2. **`render_style` 是动态 COMBO。** `mesh` 下面还挂了必需的 `shader` 子参数，
   提交形态**无法从 `/object_info` 确认**。`silhouette` 是唯一没有嵌套参数的选项，
   可以作为回退——但那是剪影，丢了肢体分离度。

所以本轮**没有**把它放进 `comfy_workflows/presets/`（放进去会被自动列出来，
看起来像能用）。下面的 JSON 是待模式就绪后可直接落地的资产：

```json
{
  "_comment": "白模生成：参考视频 → 灰白人体素模视频。节点与权重已实测在位（2026-11-10 /object_info 快照），但 **链路未实跑**；render_style 动态 COMBO 的提交形态未验证（mesh 需嵌套 shader，回退 silhouette 无嵌套参数）。启用前必须补 whitemodel 生成模式。",
  "_meta": {
    "name": "白模生成 · 参考视频 → 灰白素模视频（本地）",
    "mode": "whitemodel",
    "execution_kind": "local",
    "required_nodes": ["LoadVideo", "GetVideoComponents", "SAM3DBody_Loader",
                       "SAM3DBody_Predict", "SAM3DBody_Render", "CreateVideo", "SaveVideo"],
    "required_models": ["sam_3d_body_dinov3_bf16.safetensors"],
    "supports": ["whitemodel"],
    "min_vram_gb": 0,
    "vram_hint": "SAM3DBody 推理开销小；batch_size 默认 64，显存紧时降到 4-8",
    "verified": false,
    "unverified_params": ["render_style 动态 COMBO 提交形态", "CreateVideo 对帧序列的对齐行为"]
  },
  "prompt": {
    "1": {"class_type": "LoadVideo", "inputs": {"file": ""}},
    "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
    "3": {"class_type": "SAM3DBody_Loader",
          "inputs": {"model_file": "sam_3d_body_dinov3_bf16.safetensors"}},
    "4": {"class_type": "SAM3DBody_Predict",
          "inputs": {"sam3d_body_model": ["3", 0], "image": ["2", 0],
                     "run_hand_refinement": false, "fov": 0, "batch_size": 64}},
    "5": {"class_type": "SAM3DBody_Render",
          "inputs": {"pose_data": ["4", 0], "width": 0, "height": 0,
                     "render_style": "mesh"}},
    "6": {"class_type": "CreateVideo", "inputs": {"images": ["5", 0], "fps": 24}},
    "7": {"class_type": "SaveVideo",
          "inputs": {"video": ["6", 0], "filename_prefix": "whitemodel/shot", "format": "mp4"}}
  },
  "_aivideo_map": {
    "reference_video": {"node": "1", "input": "file"},
    "fps": {"node": "6", "input": "fps"}
  }
}
```

---

## 4. 两个必须记下来的坑

### 4.1 现有 v2v 缺 `VHS_LoadVideo`（本机）

见 §3.1。要么装 VideoHelperSuite，要么把 v2v 预设改成用本机已有的 `LoadVideo`。
后者更省事，但需要实跑确认 `LoadVideo` 的输出能被 `MiniMax H3 Content Builder` 的 `video_1` 接受。

### 4.2 `/object_info` 的 COMBO 有三种形状（已踩）

`model_file` 这类输入在 `/object_info` 里**不止一种形状**：

```
["a.safetensors", "b.safetensors"]                              # 普通 COMBO
["COMBO", {"options": [...]}]                                   # 带元信息（SAM3DBody_Loader 就是这种）
["COMFY_DYNAMICCOMBO_V3", {"options": [{key, inputs}, ...]}]    # 动态组合
```

第一版探测函数只认第一种，于是把"**有权重**"误判成"没权重"。
修好后实测：`SAM3DBody_Loader.model_file = ['sam_3d_body_dinov3_bf16.safetensors']`；
`ControlNetLoader.control_net_name = []`（**仍然是 0 个**，与 v093 的结论一致，没变）。

**教训**：凡是"枚举是否为空"的判断，都要覆盖这三种形状；否则"节点壳空转"和"节点真缺"会混为一谈。

---

## 5. 验证矩阵（明天可逐条复核）

| 项 | 结果 |
|---|---|
| 后端全套测试 | **169 通过 / 0 失败**（`./.venv/Scripts/python.exe tests/run_all.py`；含新增 11 例） |
| 新增白模提示词单测 | **11/11 通过** |
| 新增资产版本层单测（上批） | 18/18 通过 |
| 白模能力探测端点 | 已实跑；ComfyUI 停机时**如实返回 `probe_ok: false` + 错误原因**，不猜 |
| 白模生成链路 | **未实跑**（缺 whitemodel 模式 + `render_style` 提交形态未验证） |
| v2v 端到端 | **未验证**（本机缺 `VHS_LoadVideo`） |
| 前端 | 本批未改前端 |
| `app.main` 整体导入 | **未验证**（本机 venv 缺 `imageio_ffmpeg` / `python-multipart`，既有环境缺口） |

---

## 6. 未完成清单（明天照这个查）

> **更新（v095）**：第 1 条（whitemodel 模式）与第 2 条（工作流预设）已在本批落地，
> 第 10 条（tests 入库）已定案。**最新清单以 `v095-whitemodel-mode.md` §7/§8 为准**，
> 下表保留为发现时的原始记录。

### 阻塞白模生成

1. **新增 `whitemodel` 生成模式。** 需要同步改：
   `workflow_capabilities.SUPPORTED_MODES`、`generation.py` 的模式校验、
   `engines/base.py` 与 `engines/comfy.py` 的 `supported_modes`、
   前端 `ShotEditorDialog` 的 `MODES` 列表、`quality.py` 的归档判定。
   **注意**：改 `SUPPORTED_MODES` 前先看 `capability_from_template` 的回落逻辑，
   别让未声明模式的模板变成"支持全部"。
2. **白模生成工作流预设**（§3.4 的 JSON）——模式就绪后放入 `comfy_workflows/presets/`，
   并**实跑一次**确认 `render_style` 的提交形态；`mesh` 不通就回退 `silhouette`。
3. **白模视频多段支持。** H3 API 限制：参考视频 ≤3 段 / 每段 2–15s / 合计 ≤15s。
   现在 `shots.reference_video` 只有单段，且**没有时长校验**——超了只能等 API 报错。

### 白模替换的 UI

4. 九段提示词**没有界面**：现在只在 v2v 提交时后台拼装，用户看不到拼出来是什么。
   至少要做「预览本次实际提示词」。
5. `/api/white-model/sections` 与 `/api/white-model/capability` **没有前端页面**。

### 上两批遗留

6. **服装（outfits）只有接口，没有界面。** 缺「衣橱」入口（归属角色 / 适配检查 / 套用到分镜）。
7. **幕布库没有独立管理页**（列表 / 版本 / 引用清单 / 删除阻断明细）。
8. **镜头级版本锁定没有 UI**（`PUT /shots/{id}/character-versions` 等只有接口）。
9. **前端未联调**：资产版本层那 4 个界面只过了 `check:static` + `build`，**UI→接口真实往返没测过**。
10. **`backend/tests/` 被 `.gitignore` 忽略**（第 84 行），新增的 29 个用例（18 + 11）只在本地、不入库。
    需要决定：单独放行，还是维持现状。

### 更早的技术债（v093 评审里提过）

11. **ControlNet 权重仍是 0 个**（`ControlNetLoader.control_net_name = []`），
    姿态强约束实验（`FreeVideoGenerate.conditioning`）**一次都没跑**。
12. **`app.main` 整体导入验证未做**：本机 venv 缺 `imageio_ffmpeg` 与 `python-multipart`，
    现有测试靠 `tests/_stubs.py` 打桩绕过——真机起服务前要补齐这两个依赖。
