# 白模生成模式（whitemodel）落地 + 连带修掉的两个坑

版本：v095（2026-11-11）
基线：`main` @ `1ac39cf`（v094）
文档性质：**实现记录 + 验证矩阵 + 剩余缺口**。
「已验证」只写真正跑过的；没跑的一律在 §6 里标「未执行」。

---

## 0. 一句话

v094 卡在「白模生成链路查清了但接不进来」。这批把缺口补完了：
**新增 `whitemodel` 生成模式 + 内置白模工作流预设 + 分镜 UI（含视频上传）**，
一个镜头现在能从「原始视频」直接生成「灰白素模视频」，再进白模替换。

仍未实跑出片——ComfyUI 当时没起（`127.0.0.1:8188` 拒绝连接），
`render_style` 动态 COMBO 的真实提交形态只能等实跑确认。见 §6。

---

## 1. 这批做了什么

| 项 | 文件 | 状态 |
|---|---|---|
| 新增 `whitemodel` 生成模式 | `workflow_capabilities.py`、`engines/comfy.py` | 已实现，有单测 |
| 旧模板不再假声明新模式 | `workflow_capabilities.py`（`LEGACY_MODES`） | 已修，有单测 |
| 白模提交链路（只要视频、不要角色图） | `generation.py` | 已实现，有单测 |
| 白模工作流预设 | `comfy_workflows/presets/nmvideo_whitemodel.json` | 已落地，**未实跑** |
| 分镜路由白模校验 + 参数如实归档 | `routers/storyboard.py`、`video.probe_fps` | 已实现 |
| 资源守卫白模档位（不按扩散估算、不降档） | `resource_guard.py` | 已实现，有单测 |
| 能力探测细化到 `render_style` 可提交性 | `routers/white_model.py` | 已实现，有单测 |
| 视频上传（此前完全不可用） | `upload_policy.py`（新）、`routers/assets.py` | 已实现，有单测 |
| 前端两个模式卡 + 参考视频输入 | `ShotEditorDialog.vue`、`ShotCard.vue` | 已实现，`build` 通过 |
| COMBO 形状解析统一 | `workflow_capabilities.combo_options` | 已修，有回归用例 |
| `backend/tests` 入库 | `.gitignore` | 已改 |

---

## 2. 模式语义：为什么不能复用 v2v

`v2v`（白模替换）和 `whitemodel`（白模生成）都是"视频进、视频出"，但输入契约相反：

| | 参考视频 | 角色图 | 提示词 | 产物 |
|---|---|---|---|---|
| `v2v` 白模替换 | 必需（动作/镜头） | **必需**（外观） | 必需（九段职责） | 换掉外观的成片 |
| `whitemodel` 白模生成 | 必需（原始素材） | **不要** | **不要** | 丢掉外观的灰白素模 |

所以硬套 `v2v` 是错的：它会要求"必须有角色参考图"，而这一步恰恰是**去掉外观**。
代码里三处体现：

- `generation.py`：白模分支只校验 `reference_video` + 模板映射，不碰 `reference_image`。
- `routers/storyboard.py`：白模模式跳过"请先填写分镜提示词"（工作流里没有文本节点）。
- 提示词不拼角色/动作/场景：拼了也不会被提交，只会让存档看起来像参与过生成。

### 2.1 参数归档：跟随源视频，不跟随 UI

白模产物是「源视频逐帧重建 → 按原生尺寸渲染 → 按源帧率合成」。
如果照样把 UI 上的 832×480 / 81 帧写进任务存档，就变成**记录参数 ≠ 实际输出**，
复现和排障都会对不上。所以新增 `_probe_whitemodel_source()`：

- `probe_resolution` / `probe_fps` / `probe_duration`（新增 `video.probe_fps`）实测源视频
- 覆盖 `width/height/fps/num_frames`；**只有 `fps` 会真正注入工作流**（`CreateVideo`），
  宽高/帧数只是存档口径
- 探测失败（外链视频、缺 ffmpeg）→ 保留原值，不猜（`try/except` + 日志）

---

## 3. 预设：`nmvideo_whitemodel.json`

```
LoadVideo(file)                             → VIDEO
  → GetVideoComponents(video)               → IMAGE 帧 + AUDIO + FPS
  → SAM3DBody_Loader(model_file)            → SAM3D_BODY_MODEL
  → SAM3DBody_Predict(model, image, fov=0, batch_size=64)   → MHR_POSE_DATA
  → SAM3DBody_Render(pose_data, width=0, height=0, render_style)
  → CreateVideo(images, fps)                → VIDEO
  → SaveVideo(video, format="mp4")          → 落盘
```

`_aivideo_map` 只映射两项：`reference_video → 1.file`、`fps → 6.fps`。
宽高**不映射**：Render 的 `width/height=0` 表示"用 pose data 原生尺寸"，
注入 UI 分辨率反而会拉伸。

### 3.1 `render_style` 为什么默认是 `silhouette`

`render_style` 在 `/object_info` 里是**动态 COMBO**：`mesh`（真白模）下面挂着必需的
`shader` 子参数，提交形态无法从接口确认。选 `mesh` 当一个还没验证过的默认值，
等于把首次使用直接丢进节点校验错误里。

所以默认 `silhouette`（唯一无嵌套参数的选项），并把取舍写进 `_meta`：

```json
"render_style_default": "silhouette",
"render_style_options": { "mesh": "... 需要嵌套 shader 子参数，提交形态未验证", ... },
"unverified_params": [ ... ],
"verified": false
```

**实跑验证通过后**：把预设里 `render_style` 改成 `mesh` 并补上 `shader` 子参数，
同时同步 `render_style_default`。能力探测端点会直接告诉你哪些选项能单独提交（见 §5）。

---

## 4. 连带修掉的两个坑（都会让白模"看起来不能用"）

### 4.1 `_iter_loader_options` 只认一种 COMBO 形状 → 权重被误判未安装

v094 只修了**探测端点**的 COMBO 解析，但生成侧的 `_iter_loader_options` 还是只认
`[[...]]`（`spec[0]` 是列表）。而 SAM3DBody 的权重是
`["COMBO", {"options": [...]}]` —— 于是：

1. 其它 loader（ckpt 之类）的枚举非空，`_iter_loader_options` 不为空字 → 判定"探测可信"
2. `sam_3d_body_dinov3_bf16.safetensors` 不在集合里 → 报"需要以下模型但未安装"
3. 结果：**权重齐备的机器上，白模生成被提交前拦住**，理由还是错的

修法：把形状解析收成一个函数 `workflow_capabilities.combo_options(spec)`，
`_iter_loader_options`、`_model_files_in`、`_combo_detail` 全部走它。
回归用例：`test_model_behind_combo_shape_is_recognized`。

### 4.2 旧模板会"自动支持"新模式

`capability_from_template` 原来在 `supports` 缺失时回落 `list(SUPPORTED_MODES)`。
把 `whitemodel` 加进 `SUPPORTED_MODES` 的那一刻，**所有没写 `supports` 的老模板
就凭空宣称支持白模生成**——提交时才失败，而且看起来像工作流的错。

修法：引入 `LEGACY_MODES`（= 加白模之前那 5 个），缺失/全非法时都回落它。
回归用例：`test_legacy_template_does_not_claim_whitemodel`。

---

## 5. 能力探测：从"节点在不在"到"这个选项能不能提交"

`GET /api/white-model/capability` 新增两块：

- `generation`：内置预设是否在位、`mode`、`verified`、`unverified_params`、
  以及白模链路额外需要的视频进出节点（`LoadVideo/GetVideoComponents/CreateVideo/SaveVideo`）
  缺哪个
- `paths[sam3d_body_mesh].render_style`：
  ```json
  {"options": ["mesh","silhouette", ...],
   "nested_inputs": {"mesh": ["shader"]},
   "safe_defaults": ["silhouette", ...],
   "preset_default": "silhouette"}
  ```
  `preset_style_is_safe` 直接回答"预设当前默认值是不是能单独提交的那种"。

`verified` 仍恒为 false：节点在位不等于链路跑通。

---

## 6. 验证矩阵（明天照这个复核）

> **2026-11-11 更新**：下表「未执行/未验证」项已在本地安装目录实跑，见
> `v096-local-runtime-verification.md`。实跑撞出 4 个缺陷（请求体正则漏改、
> `ShotUpdate` 缺 mode、产物提取把原片当产物、ComfyUI 徽标恒显示未连接），
> 均已修复。白模链路当前状态：**silhouette 路径实测出片；mesh 仍未验证**。

| 项 | 结果 |
|---|---|
| 后端全套测试 | **191 通过 / 0 失败**（`./.venv/Scripts/python.exe tests/run_all.py`） |
| 本批新增用例 | **22 例**（`tests/test_whitemodel_mode.py`） |
| 预设解析 / 能力列表 | 已实跑：`resolve_template_for("comfyui","whitemodel")` → 白模预设；`t2v` 仍回落当前模板（未被劫持） |
| `comfyui` 引擎 supported_modes | 已实跑：含 6 个模式（含 whitemodel） |
| 前端 `check:static` | 通过（24 组件，可疑 0；api/store/emits 契约无问题） |
| 前端 `build` | 通过 |
| **白模生成链路实跑** | **未执行** — ComfyUI 未启动（8188 拒绝连接） |
| **`render_style=mesh` 提交形态** | **未验证** — 需实跑；当前默认走 silhouette |
| **`SAM3DBody_Predict` 对帧批次是否逐帧** | **未验证** |
| v2v 端到端 | 未验证（本机仍缺 `VHS_LoadVideo`） |
| `app.main` 整体导入 | 未验证（venv 缺 `imageio_ffmpeg` / `python-multipart`） |
| UI → 接口真实往返 | 未执行（无服务端可起） |

### 6.1 明天最快的验证路径

1. 起 ComfyUI（8188），`GET /api/white-model/capability` → 看 `generation.missing_video_io_nodes` 与 `render_style.safe_defaults`
2. 上传一段 3~5 秒短视频（分镜编辑 → 白模生成 → 上传视频）
3. 提交生成，盯 ComfyUI 控制台：
   - 若报 `render_style` 缺 `shader` → 说明 ComfyUI 把 `silhouette` 也当成需要子参数，把预设换成 `mesh`+`shader` 再试
   - 若报 `SAM3DBody_Predict` 的 image 维度不对 → 需要前置 `ImageFromBatch`/逐帧循环（见 §7）
4. 出片后确认：产物是灰白、无原片纹理、动作与时长对得上；再把 `_meta.verified` 改 true 并写实测参数

---

## 7. 剩余缺口（按影响排序）

### 阻塞"真白模"质量

1. **`render_style=mesh` 未验证**（§3.1）：目前产物是剪影，肢体前后关系弱于灰模网格。
2. **逐帧处理未验证**：`SAM3DBody_Predict` 吃单张 IMAGE，而 `GetVideoComponents` 给的是帧批次。
   若不支持批次，需要在预设里加逐帧节点（待实跑确认，不提前猜节点名）。

### 白模替换（v2v）仍不可用

3. 本机缺 `VHS_LoadVideo`（VideoHelperSuite 未装）。两条路：装插件，或把 v2v 预设改成用
   本机已有的 `LoadVideo`——后者要实跑确认 `LoadVideo` 的输出能被
   `MiniMax H3 Content Builder.video_1` 接受。

### UI

4. **九段提示词没有预览**：v2v 提交时后台拼装，用户看不到拼出来是什么。
   建议加只读预览（`GET /shots/{id}/prompt-preview`），复用生成时的同一份拼装逻辑。
5. **`/api/white-model/sections` 与 `/capability` 没有前端页面**：现在只有接口。
6. **服装（outfits）仍只有接口**：缺「衣橱」入口。
7. **幕布库仍无独立管理页**。
8. **镜头级版本锁定仍无 UI**。

### 环境

9. `app.main` 无法在本机 venv 导入：缺 `imageio_ffmpeg`、`python-multipart`。
   前者靠 `tests/conftest.py` 打桩，后者现在只影响"起服务"，不影响规则测试
   （上传策略已抽到无依赖的 `app/upload_policy.py`）。
10. **`backend/tests` 已入库**（`.gitignore` 改为只忽略 `__pycache__`）：
    契约用例是跨批次安全网，不入库等于每批裸奔。

---

## 8. 与 v094 清单的对应

v094 §6 的 12 条 → 本批处理情况：

| v094 条目 | 现状 |
|---|---|
| 1. 新增 whitemodel 模式 | **已做**（本节 §2；含 `LEGACY_MODES` 防假声明） |
| 2. 白模工作流预设 | **已落地，未实跑**（§3） |
| 3. 白模视频多段支持 + 时长校验 | **未做**（H3 API 限制 ≤3 段 / 每段 2–15s / 合计 ≤15s） |
| 4. 九段提示词预览 | **未做**（§7-4） |
| 5. 白模能力探测前端页 | **未做**（接口已细化，§5） |
| 6. 服装界面 | **未做** |
| 7. 幕布库管理页 | **未做** |
| 8. 镜头级版本锁定 UI | **未做** |
| 9. 前端 UI→接口真实往返 | **未执行**（需起服务） |
| 10. tests 是否入库 | **已定：入库**（§7-10） |
| 11. ControlNet 权重 0 个 | **未变**（本批未碰） |
| 12. `app.main` 导入验证 | **未做**（缺两个依赖，§7-9） |
