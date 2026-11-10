# NMVideo 对外部提案《C 路线》的评审

版本：v093（2026-11-10）
基线：`main` @ `dde4433`（含 [v092 幕布库设计说明](v092-plate-library-design.md)）
被评审对象：外部提案《NMVideo C 路线：白模动作、角色一致性、换装与自动化 AI 动画生产线》v1.0（2026-11-10，作者非本仓库维护者）
文档性质：**纯评审 —— 未改代码、未实跑、未创建任何表**。所有"现状"结论来自对上述基线的代码阅读，附文件与行号；引用提案原文处标注【提案 §x】。

---

## 0. 总判断

提案方向成立，边界自述也诚实：它主动声明【提案 第0行】"尚未对最新 NMVideo 仓库完成源码审计"、【提案 §14】"接口和目录是拟议合同，不能直接当作现有项目结构"、"不能承诺 AI 成片与白模动作逐帧 100% 完全一致"、"仅有 GLB 文件并不意味着可直接复用动作"、"需要确认使用授权"。这些保留是对的，不该被当成套话。

但它有两个结构性问题：

1. **把已有等价物当成新任务重写。** 提案 15 张新表里至少 6 处与现状重复；`P3`（ComfyUI Provider）与 `P6`（FFmpeg 合成）在本仓库**已经存在**，`P0` 已部分完成。
2. **绕开了当前真正卡住的问题。** 提案 §13 的首轮验证场景（6 秒竖屏舞蹈）正是"白模动作遵循度上不去"的场景，但它给出的解法是 `RIG_RENDER`，而 `RIG_RENDER` 的前提（**正式三维角色 + 同骨架服装 Mesh**）在本仓库**不存在**。提案 §2.1 仅以"可选三维角色 `.blend/.glb`"一句带过。

**结论：这份提案可以作为中期方向参考，但不能作为实施基线；直接按它建表会与现状形成双写双源。**

---

## 1. 现状底数（用于后面对表）

| 项 | 实测 | 证据 |
|---|---|---|
| 数据库表数 | **32 张** | `backend/app/db.py` 全部 `CREATE TABLE` |
| 是否存在 `assets` / `media` 表 | **都不存在** | 同上 |
| 资产如何寻址 | URL 字符串 + 受管路径统一校验 | `managed_paths.resolve_managed_asset()`（`managed_paths.py:83`） |
| 前端路由数 | **11 条**（10 页 + 1 条 `/services` 兼容重定向） | `frontend/src/router.js` |
| `/projects/:id` 形态 | **单页 + tabs**（分镜 / 资产库 / 剧本 / 时间线 / 历史） | `ProjectView.vue:24-29` |
| ComfyUI 提交链路 | `/prompt` + `/ws` + `/history/{prompt_id}` + `/object_info` | `comfyui.py:139, 239, 338, 385` |
| 工作流槽位注入 | 模板 `_aivideo_map`，**映射了才注入** | `comfyui.py:572, 637` |
| 支持的输入模式 | `("t2v", "i2v", "first_last", "continuation", "v2v")` | `workflow_capabilities.py:33` |
| 入队冻结语义 | 已建立：参考图 / 首尾帧 / 多参考 / 角色快照 / 工作流快照 | `routers/storyboard.py:562, 586` |
| 导出合成 | 已在用 FFmpeg（拼接 / 混音 / 字幕烧录） | `export.py:20, 236, 292` |

32 张表清单：

```
jobs projects novels novel_chapters novel_bibles novel_bible_versions novel_chapter_analyses
bible_characters bible_scenes bible_timeline bible_relations novel_episodes novel_episode_versions
novel_scripts novel_script_versions novel_shot_plans novel_shot_items novel_shot_plan_versions
novel_submissions novel_submission_items novel_handoffs
scenes shots shot_versions characters motion_templates
timeline_items exports shot_voices audio_tracks subtitle_tracks review_history
```

---

## 2. 已存在：提案当成新任务的部分

| 【提案】提议 | 本仓库现状 | 证据 |
|---|---|---|
| `render_jobs` / `render_attempts` 队列与尝试 | 已有 `jobs` + `shot_versions`（`version_no / params / seed / status=candidate\|approved`） | `db.py:91`（jobs）、`db.py:464`（shot_versions） |
| `shot_manifests` 冻结执行配方 + `content_hash` | 已有等价物：`jobs` + **工作流快照**（`persist_prepared_job(graph, template)`；PR-06 已带 `schema_version` + `sha256`） | `routers/storyboard.py:586` |
| `provider_profiles` 供应商能力 | **已有且更成熟**：`workflow_capabilities.py` 的 `_meta` —— `execution_kind / model_id / required_nodes / required_models / supports / min_vram_gb / vram_hint`，含**缺节点探测**与**显存档位校验** | `workflow_capabilities.py:9-20, 125, 217` |
| `media_assets` 文件索引 | 已有：`resolve_managed_asset()` 统一校验 URL 前缀 / 解码 / 规范化 / 根归属 / 符号链接 / 大小与类型，目标与提案"不允许前端接收任意本地绝对路径"**完全一致** | `managed_paths.py:83` |
| `review_results` | 已有 `review_history` + `shots.review_stage` + `ReviewDialog.vue` | `db.py`（32 表内）、`components/ReviewDialog.vue` |
| `timeline_items` / `exports` | 已有同名表 | `db.py` |
| **P6「接入 FFmpeg 音视频合并、字幕、转场、时长对齐」** | **已完成**：`export.py` 已 `from .video import concat_videos, get_ffmpeg, has_audio_stream, probe_duration`，含混音与字幕烧录 | `export.py:20, 236, 292-329` |
| **P3「实现 POST /prompt、/ws、GET /history、产物下载/索引；模板注册与 API 格式映射」** | **已实现** | `comfyui.py:139, 239, 338, 385, 440, 572`；`engines/comfy.py` |
| P0「源码审计」 | 已部分完成（v092 与本文件即为其中一部分） | 本目录 |

**关于"角色/镜头参数拆分"**：提案 §7.1 主张人物提示词拆成 `identity_prompt / outfit_prompt / expression_prompt / environment_prompt`。本仓库已经是**同一思路但落点不同**：

- 角色 → `assets.compose_prompt_with_characters()`（`assets.py:156`）
- 动作 + 构图（站位/朝向/镜头运动）→ `motion.compose_motion_prompt()`（`motion.py:241`）
- 场景（环境 + 光照）→ `assets.compose_prompt_with_scene()`（`assets.py:176`）

三段**正交拼装**，最后随 `params["prompt"]` 入队冻结。提案要的拆分**已经存在**，只是没有叫 `*_prompt` 这几个名字。

---

## 3. 真新增：提案说得对的部分

| 【提案】提议 | 现状 | 判定 |
|---|---|---|
| **Blender Worker**（导入 / 绑骨校验 / 重定向 / 脚步修正 / 相机） | 全仓搜 `blender` `fbx` `armature` `retarget` `mixamo` —— **零出现** | ✅ 真缺口，从零开始 |
| `rigs` 骨架规范 + `Humanoid_v1` + 骨骼映射 | 不存在 | ✅ 真缺口 |
| `outfits` / `outfit_versions` 换装资产 | `characters.outfit` 是**单值 TEXT**（`db.py:504-518`），无服装资产 | ✅ 真缺口 |
| **`character_versions`** 角色身份版本 | `characters` 表**无版本字段**，`CharacterEditorDialog` 是**就地编辑** | ✅ 真缺口 |
| 白模驱动视频 `driver.mp4` + 相机模板 `CameraPlan` | 现状仅有 `v2v` 模式的 `reference_video`（模态级，非资产级） | ✅ 真缺口 |
| 云 Provider 统一适配器 `CloudMotionProvider` | 仅有 `execution_kind = paid_api` 的**标记**，无统一适配器接口 | ✅ 真缺口 |
| 状态机 `DRAFT→…→ASSEMBLED` 与错误态分离 | 现状有 `jobs` 状态 + `shots.review_stage`，但无提案这么完整的状态机 | ⚠️ 部分缺口 |

**一个值得单独指出的观察**：`character_versions` 缺失这件事，与 [v092](v092-plate-library-design.md) 发现的"幕布（`scene.reference_image`）无版本、就地覆盖"是**同一类缺陷** —— **NMVideo 的资产层普遍缺版本层**。提案和 v092 各自发现了一半，这件事应该做一次统一方案，而不是零散加表。

---

## 4. 不建议照做的部分

### 4.1 五套新表会造成双写双源

提案 §3 的 15 张表里，`render_jobs` / `render_attempts` / `shot_manifests` / `media_assets` / `provider_profiles` / `review_results` 六张与现状重叠（见 §2）。

提案正文其实写对了原则——【提案 §3】"已有表存在相同实体时通过兼容层复用，**禁止无审计直接迁移原有数据表**"——但**表结构设计那一节没有照这句话做**。若照表结构建，会立刻形成两套真相：`jobs` 与 `render_jobs` 并存、`workflow_capabilities._meta` 与 `provider_profiles` 并存，二者的 `supports` / `required_nodes` 会漂移。

这与项目已有硬约定冲突：**不破坏旧数据、不删模块、每批改动可回滚**。

**建议**：删除上述六张表；能力声明继续走 `_meta`；冻结配方继续走 `jobs` + 工作流快照；文件索引继续走 `managed_paths`。真需要新表时，只加 §3 里那六个真缺口。

### 4.2 七个新页面会分裂导航

提案 §10 提议新增 `角色库 / 衣橱 / 动作库 / 镜头导演 / 制作队列 / 质检台 / 时间线` 七个页面，并以"新路由挂载"接入。

但现状是：**11 条路由，`/projects/:id` 单页 + tabs**。其中：

| 提案新页 | 现状落点 |
|---|---|
| 角色库 | `ProjectView` 资产库 tab 的角色卡片区（`char-grid` / `char-card`） |
| 制作队列 | 已有 `/queue`（`QueueView.vue`） |
| 时间线 | `ProjectView` 时间线 tab（多轨，PR-11） |
| 质检台 | `ReviewDialog.vue` + 历史 tab（`review_history`） |

提案自己也说【提案 §10】"若项目已有对应页面，使用组件扩展与新路由挂载，**避免重构整体导航**"——那么正确做法是**扩组件 + 在既有 tab 里加子视图**，而不是把一个单页 tabs 项目改成 7 路由项目。后者会让"项目上下文"（当前项目 / 场景 / 镜头）在页面间丢失，需要重新引入全局状态，成本远大于收益。

**建议**：`角色库` → 资产库 tab 升级；`衣橱` → 角色卡里的子区；`动作库` → 扩 `motion_templates`（已有 CRUD）+ 资产库 tab；`镜头导演` → 扩 `ShotEditorDialog`；其余三项**已经有页面了**。

### 4.3 `motions` 表会撞 `motion_templates`

提案 §3 新建 `motions` + `motion_versions`。现状已有 `motion_templates`（`db.py` 32 表内），并有完整 CRUD（`routers/storyboard.py:386-418`），含 `action_prompt`，而且**已经拼进提示词并在用**（`motion.compose_motion_prompt`，`motion.py:241`），随 `params["prompt"]` 入队冻结。

**建议**：扩展 `motion_templates` 加 `rig_id` / `animation_asset_id` / `fps` / `frame_count` 等列，不要新建平行表。

---

## 5. 关键判断：该方案不解决姿态遵循问题

提案 §13 的首轮验证是「固定角色 6 秒竖屏舞蹈，输出 `driver.mp4` / `render_A.mp4` / `render_B.mp4` / `side_by_side.mp4`」——**这正是当前卡住的场景**（白模驱动下姿态遵循度上不去）。

但提案给的解法是 `RIG_RENDER`：**同一骨架直接驱动正式 3D 角色**（【提案 §0】"严格动作复现、高复用舞蹈及复杂接触动作"）。而它自己在【提案 §14】承认：「**不能承诺 AI 成片与白模动作逐帧 100% 完全一致。只有骨骼驱动的正式 3D 模型能在动作数据层面保持精确复用**」。

**所以：这是换一条路绕过去，不是把 AI_MOTION 路线的姿态遵循治好。**

绕过去要付两笔账，提案没有把话说透：

**(1) `RIG_RENDER` 的前提在本仓库不存在。**
它要求"**正式三维角色 + 同骨架服装 Mesh**"（【提案 §5.5】"加载正式 3D 角色与同骨架服装 Mesh，直接渲染"）。而现状的角色资产是 **AI 生成的 2D 参考图**——`characters.reference_images` 是 URL 数组，`appearance / outfit` 是文本描述（`db.py:504-518`）。没有任何 3D 角色模型。
提案 §2.1 把三维角色列为"**可选** `.blend/.glb`"，但那一项其实是 `RIG_RENDER` 整条路的必要前提。**从 2D 参考图到可驱动 3D 角色，是另一个量级的工程**（建模 / 绑骨 / 蒙皮 / 体型匹配），提案未展开，也未计入 P1–P6 的工作量。

**(2) 引入 Blender = 引入一个外部可执行程序。**
要安装、要在设置页配 `BLENDER_EXECUTABLE`、要启动前验 `--version`、要与 ComfyUI 抢同一块 GPU（【提案 §9】自认"本地 4060 8GB：Blender GPU 渲染、ComfyUI GPU 工作流默认互斥"）、要管进程生命周期与超时。这在一个以"8GB 显存 + 32GB 内存"为承诺门槛的桌面产品里是实质性的复杂度新增。

**(3) 提案完全没提本仓库已有的姿态链路。**
`/object_info` 实测（本机 928 节点）显示，本机已有**完整且模型在位**的 3D 人体姿态链路：

```
SAM3DBody_Loader            # model_file 枚举含 sam_3d_body_dinov3_bf16.safetensors（模型已装）
  → SAM3DBody_Predict       # image + fov（+ 可选 bboxes / track_data）
  → BuildPoseFile           # format=glb(mesh|bones_only|openpose|scail) / format=bvh(cm, mocap 标准)
                            # camera_translation: off|centered|absolute
```

即：**本地就能把一段视频变成 3D 姿态，并直接输出 OpenPose-18 骨架图（含手部与面部）或 BVH**，不需要 Blender，不需要任何新增 3D 软件。这条路与现有 ComfyUI 底座同构。

RIG_RENDER 不必然错——它解决的是"动作资产精确复用"，这个价值是真的。但**在决定引入 Blender 之前，应该先验掉更便宜的那条路**。

---

## 6. 建议顺序

**第 1 步（先做，成本 ≈ 一次生成）：验证 AI_MOTION 强约束是否成立。**

唯一决定性未知是：`FreeVideoGenerate.conditioning` 这个口到底吃什么。`/object_info` 显示它接受 `CONDITIONING`，全库有 114 个节点能产出 `CONDITIONING`，但**没有一个能由图像/骨架控制**（`ControlNetApply*` 需要 `CONTROL_NET`，而本机 `ControlNetLoader.control_net_name` 枚举为 **0 个**）。同时 `FreeVideoGenerate` **没有任何 pose / skeleton / control_image 入参**。

- 实验 A：`CLIPTextEncode → FreeVideoGenerate.conditioning`，确认该口收的是文本条件
- 实验 B（决定性）：装 ControlNet 权重 + 出于骨架图 → `ControlNetApplyAdvanced → conditioning`，看姿态遵循度是否抬升
- 注意：即便 B 通过，"每层每步注入 + 权重可调"也**不是** `FreeVideoGenerate` 的参数，是否作用于每一步去噪取决于其内部实现，**外部不可见，不得写入验收**

成本提示：装 ControlNet 权重会同时占磁盘与显存，与现有 **8GB / 32GB 门槛 + 57.5GiB 磁盘预算**冲突，需先算空间。

**第 2 步（若第 1 步失败）才考虑 Blender，且只做最小闭环。**

一个动作 + 一个白模 + 一条 `blender -b template.blend --python nm_blender_worker.py -- --job <manifest>` 出 `driver.mp4`。**不建 rig / motion / outfit 版本体系**——那是提案 P1，而 P2（Blender Worker）不依赖 P1。

**第 3 步：资产版本层一次做对。**

`character_versions` / `outfits` / 幕布版本（v092）是同一个缺陷的三个实例。应统一设计"资产 = 池 + 不可变版本 + 引用方冻结"，避免零散加表。

---

## 7. 与既有规划的衔接

| 既有规划 | 与本提案的关系 |
|---|---|
| [v092 幕布库设计说明](v092-plate-library-design.md) | 其 §7 的"三级降级"需要 `workflow_capabilities._meta.supports` **新增控制能力位**（现在只有五个输入模式）。提案若要加姿态控制，应复用同一机制，而不是另立 `provider_profiles` |
| PR-04 入队冻结输入资产 | 提案的 `shot_manifests` 语义与之重叠，应统一到 `jobs.plate_snapshot` 这类既有冻结模式上 |
| PR-06 冻结工作流 + `schema_version` + `sha256` | 提案的 `content_hash` 缓存语义与之重叠 |
| PR-08 首尾帧 / 多参考契约 | 提案的"每个分镜必须显式锁定 `hair_version`"是同一思路（输入版本冻结），措辞可复用 |
| PR-10 角色版本 / 镜头链 / 构图约束 | 提案的 `RIG_RENDER` 要接的正是这里 |
| 8GB / 32GB / 57.5GiB 门槛 | 提案 §9 的"Blender 与 ComfyUI 互斥"与之一致；但引入 Blender 会改变安装体积与依赖面，需重新核算发布口径 |

---

## 8. 附录：核对方法（可复现）

```bash
# 表数与表名
grep -oE "CREATE TABLE( IF NOT EXISTS)? [\"']?[A-Za-z_]+" backend/app/db.py | sed -E "s/CREATE TABLE( IF NOT EXISTS)? //; s/[\"']//g" | sort -u

# Blender / 骨骼体系是否存在
grep -rniE "blender|mixamo|fbx|armature|retarget" backend/app frontend/src desktop

# 前端路由
grep -cE "^\s*\{ path:" frontend/src/router.js

# ComfyUI 能力断言（需本机 ComfyUI 在 127.0.0.1:8188）
curl -s http://127.0.0.1:8188/object_info -o objinfo.json
# 再查 ControlNetLoader.control_net_name 枚举、FreeVideoGenerate 入参、SAM3DBody_*/BuildPoseFile
```

**未执行**：本评审未运行任何生成任务、未安装任何模型、未创建或修改任何数据表、未实跑 Blender。§6 的三个实验均为建议，尚未执行。
