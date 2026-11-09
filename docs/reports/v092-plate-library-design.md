# NMVideo 空间幕布（幕布库）设计说明

版本：v092（暂定，待并入正式批次编号）
基线：`main` @ `0d029a9`（2026-11-09「场景幕布（拖图/选图/AI生成）+ 生成时自动注入环境光照」）
文档性质：**设计说明，未实现、未实跑**。所有"现状"结论均来自该基线的代码阅读，附文件与行号；所有"方案"均为待评审设计，不代表已生效。

---

## 0. 一句话结论

**幕布现在"能存、能看"，但还没有参与生成。**

`scene.reference_image` 既没进 `params`，也没进工作流——`generate_shot()` 只把 `environment / lighting` 两段**文本**拼进提示词。所以这套设计的第一优先级不是版本管理，而是**把幕布从"给人看的图"变成"给模型用的输入"**。

第二个结论更省事：**「站位约束」的数据结构和提示词通道都已经存在并且在用**，缺的只是"摆"的界面。

---

## 1. 现状（代码证据）

### 1.1 已经有的

| 能力 | 位置 | 状态 |
|---|---|---|
| 场景资产字段 | `scenes.environment / lighting / layout / style / reference_image`，`SceneUpdate` 已扩 | 可用（`0d029a9`） |
| AI 生成幕布 | `POST /scenes/{id}/backdrop` → `assets.generate_scene_backdrop()`，imagegen 1280×720 回写 `reference_image` | 可用（同上） |
| 幕布前端 | `ProjectView.vue` 资产库「场景幕布」卡片：拖图 / 选图（复用 `uploadFile`）/ 预览 / 移除 / AI 生成 | 可用 |
| **站位约束数据结构** | `shots.composition` JSON（`character_positions` / `character_facing`）+ `shots.camera_movement` | 可用 |
| **站位约束提示词通道** | `motion.compose_motion_prompt()` 输出 `{角色名}位于{位置}`、`{角色名}{朝向}`、`镜头{运动}`，随 `params["prompt"]` 入队冻结 | **已接入，在用** |
| 槽位注入机制 | 模板 `_aivideo_map` + `comfyui.build_shot_workflow()` | 可用 |

站位/朝向是**封闭枚举**（`motion.py:170`）：

```
_POSITION_ZH = {left: 画面左侧, center: 画面中央, right: 画面右侧}
_FACING_ZH   = {toward_camera: 面向镜头, back_to_camera: 背对镜头,
                toward_left: 面向画面左侧, toward_right: 面向画面右侧,
                profile: 侧身对镜头}
```

### 1.2 缺口（本方案要补的）

1. **幕布图从未进入生成链路（P0）。** `generate_shot()`（`routers/storyboard.py:528-540`）只注入三类文本：角色（`compose_prompt_with_characters`）、动作+构图（`compose_motion_prompt`）、场景（`compose_prompt_with_scene`——**只读 `environment` / `lighting`，不读 `reference_image`**）。
   `params["reference_image"]` 是 **shot 级**参考图，与场景幕布无关。幕布 URL 没有出现在 `params` 任何字段里。
2. **没有可视化的"摆"（P1）。** 站位只有下拉枚举，撑不起「谁在前、谁在后、面朝哪、机位怎么走」。
3. **没有版本，历史不可复现（P1）。** 改幕布 = 直接覆盖 `scene.reference_image`；已入队 / 已通过的镜头再重跑会拿到新幕布，违反 PR-08「入队冻结输入资产与来源版本」的口径。
4. **控制图（depth / pose）完全不存在**，且 NMVideo 侧不应新增依赖（见 §2 非目标）。

---

## 2. 目标与非目标

### 目标（按落地顺序）

- **G1 幕布参与生成** —— 让基准图作为一个真实输入进工作流。
- **G2 站位可视化** —— 图形化"摆"的界面，产物落回**既有** `composition` / `camera_movement` 字段，复用既有提示词通道。
- **G3 版本冻结** —— 改幕布另存新版本，已入队/已通过的任务继续引用旧版本。
- **G4 控制图（depth / pose）** —— 二级增强，由工作流内预处理节点生成。

### 非目标（明确不做）

- **不做 3D 编辑器。** 8GB 显存门槛下没有这个余量；2D 站位 + 控制图已经能锁住空间关系。
- **不读服务端文件系统，不认自定义节点目录**，不依赖任何插件私有路径。
- **不在 NMVideo 侧新增 depth / pose 依赖**（不引入 depth-anything / onnxruntime 等）。控制图一律交给 ComfyUI 工作流内的预处理节点。
- **不静默忽略**：工作流不支持的能力，一律禁用 + 明确提示，不允许"以为生效了其实没注入"。

---

## 3. 概念分层（需要确认的关键决定）

幕布**同时存在两种归属**，本方案主张分两层，而不是二选一：

| 层 | 载体 | 语义 |
|---|---|---|
| **池** | 幕布库（项目级资产，带版本） | 一张幕布可被多个场景 / 多个镜头复用 |
| **默认绑定** | `scenes.reference_image`（升级为指向 `plate_version_id`） | 该场景的默认幕布，不破现状 |
| **实际引用** | `shots.plate_id` + `shots.plate_version` | 入队冻结的那一版 |

**生效优先级：镜头级 `shots.plate_version` > 场景默认 `scenes.plate_version_id` > 无**

旧数据兼容：`scenes.reference_image` 若仍是字符串 URL，按"未收编"处理，读取时降级为场景默认幕布的 v1 语义，**不做数据迁移**（沿用项目"不破坏旧数据"的约定）。

### 换场景 / 复制镜头时的默认行为

| 场景 | 行为 |
|---|---|
| 新建场景 | 不自动绑定；引导「从场景幕布导入」或从库选 |
| 新建镜头 | 默认继承所属场景的默认幕布（镜头不单独绑定 = 跟随场景） |
| 复制镜头到另一场景 | 幕布**随镜头一起复制**（引用同一 `plate_version`，不因场景变化而改写） |
| 把镜头改绑到另一个场景 | 若该镜头**未单独绑定**幕布 → 自动跟随新场景默认；若**已单独绑定** → 保持不变，UI 提示"该镜头使用独立幕布，未跟随场景" |
| 场景默认幕布换了版本 | 未单独绑定的镜头视为"继承已过期"，在列表上标记，但不自动改已入队任务 |

---

## 4. 数据模型

沿用 `db.py` 的 `ALTER TABLE ... ADD COLUMN` 迁移风格（见 `db.py:760-777` 的 `shot_cols15` 写法）。

### 新表

```
plates                        幕布（池）
  id                TEXT PK
  project_id        TEXT       -- 归属项目
  name              TEXT
  form              TEXT       -- 'flat' | 'equirect'  平铺 / 720°全景
  width, height     INTEGER
  current_version   INTEGER    -- 当前版本号，从 1 起
  status            TEXT       -- 'draft' | 'active'
  created_at, updated_at

plate_versions                幕布版本（不可变）
  id                TEXT PK
  plate_id          TEXT FK
  version           INTEGER    -- 唯一 (plate_id, version)
  image_asset_id    TEXT       -- 指向 assets，勿存裸路径
  image_url         TEXT       -- 本机 /outputs/ URL（PR-02 口径：记本机 URL）
  sha256            TEXT       -- 冻结校验
  note              TEXT
  created_at

plate_control_maps            派生控制图
  id                TEXT PK
  plate_version_id  TEXT FK
  kind              TEXT       -- 'depth' | 'pose'
  asset_id          TEXT
  generator         TEXT       -- 'workflow_node'
  generator_ref     TEXT       -- 节点类名，如 'DepthAnythingV2Preprocessor'
  created_at
```

### 新增列

```
scenes:  plate_version_id TEXT DEFAULT ''      -- 场景默认绑定（新）；reference_image 保留兼容
shots:   plate_id         TEXT DEFAULT ''
         plate_version    INTEGER DEFAULT 0     -- 0 = 未绑定，跟随场景默认
jobs:    plate_snapshot   TEXT DEFAULT '{}'     -- 入队冻结，照抄 characters_snapshot 模式
```

`jobs.plate_snapshot` 内容（照 `routers/storyboard.py:562` 的 `characters_snapshot` 写法）：

```json
{
  "plate_id": "...", "plate_version": 3,
  "image_url": "/outputs/plates/xxx.png", "sha256": "...",
  "control_maps": {"depth": {"asset_id": "...", "generator": "DepthAnythingV2Preprocessor"}},
  "staging": {"character_positions": {"<char_id>": "left"},
              "character_facing": {"<char_id>": "toward_camera"},
              "camera_movement": "缓慢右移"}
}
```

---

## 5. 接口（沿用 `routers/storyboard.py` 现有风格）

| 方法 | 路径 | 说明 | 阻断条件 |
|---|---|---|---|
| GET | `/projects/{pid}/plates` | 幕布列表（含 `version` / `usage_count`） | — |
| POST | `/projects/{pid}/plates` | 新建幕布（v1） | — |
| GET | `/plates/{id}` | 详情（版本列表 + 引用清单 + 控制图状态） | 404 |
| POST | `/plates/{id}/versions` | **另存新版本**（改幕布的**唯一**入口） | 素材缺失 400 |
| POST | `/plates/{id}/versions/{v}/promote` | 回滚某版本为当前 | 404 |
| GET | `/plates/{id}/usages` | 被哪些镜头/场景引用 | — |
| DELETE | `/plates/{id}` | 删除 | **有引用 → 409 + 引用清单**（对齐 3A 数据保护门禁） |
| POST | `/scenes/{sid}/promote-backdrop` | 把现有 `scene.reference_image` 收编为幕布 + v1 | 无幕布 400 |
| POST | `/plates/{id}/probe-control-maps` | 用 `object_info()` 探测工作流是否支持 depth / pose 预处理 | 无工作流 400 |
| PUT | `/shots/{sid}/plate` | 绑定幕布到镜头（写 `plate_id` / `plate_version`） | 版本不存在 404 |

**关键约束：幕布内容一律"另存新版本"，不提供就地覆盖接口。** 这是 §9 验收第 1 条的前提——只要允许就地改，历史可复现性就守不住。

---

## 6. 生成链路接入（最小改动路径）

### 6.1 G1：幕布进工作流

在 `generate_shot()` 中 `compose_prompt_with_scene(...)`（`routers/storyboard.py:538`）之后追加：

```python
# 空间幕布：基准图作为真实输入入队（PR-02 口径：记本机 /outputs/ URL，非 ComfyUI input 引用名）
plate = resolve_plate_for_shot(shot, scene)      # 依 §3 优先级解析
if plate:
    params["scene_plate"] = plate["image_url"]
    params["plate_snapshot"] = json.dumps(..., ensure_ascii=False)
```

模板侧 `_aivideo_map` 新增槽位（示例，实际节点号按各预设定）：

```json
"scene_plate": { "node": "4", "input": "image" }
```

- 上传复用 `comfyui.upload_reference_image(client, url)`（`comfyui.py:600`）。
- **模板未映射 `scene_plate` 时：不注入**，并在 UI 明示"当前工作流未使用幕布"。这是 `_aivideo_map` 既有语义（"映射了才注入"，见 `comfyui.py:637`），不要新增隐式行为。

### 6.2 G2：站位可视化（**零新增提示词通道**）

图形化"摆"的产物就是已有的两个字段：

| UI | 写回字段 | 取值 |
|---|---|---|
| 色块左/中/右 | `composition.character_positions[char_id]` | `left` / `center` / `right` |
| 色块朝向 | `composition.character_facing[char_id]` | §1.1 的五档枚举 |
| 机位轨迹 | `shots.camera_movement` | 文本（"固定" = 不输出） |

提示词拼装**完全复用 `motion.compose_motion_prompt()`，零改动**。这也意味着：

- 站位约束**恒可用**——不依赖任何工作流能力，不存在"降级"，它本来就是文本通道。
- 从图形界面应能切到「查看提示词」，展示实际拼出的那一段（避免黑盒）。

### 6.3 G4：控制图

- 控制图**由工作流内的预处理节点生成**，NMVideo 只提供 `scene_plate` 原图。理由：零新增 Python 依赖、零新增 NMVideo 侧显存，且与 8GB 门槛不冲突。
- 能力探测走已封装的 `ComfyUIClient.object_info()`（`comfyui.py:385`）。
- 探测名单待定（见 §10）。命中 → 允许映射 `control_image` 槽位；未命中 → 该行禁用 + 列出缺项节点名。

---

## 7. 三级降级规则（显式表）

| 能力 | 工作流支持 | 工作流不支持 |
|---|---|---|
| 幕布基准图 | 映射 `scene_plate` → 注入 + 上传 | **不注入**，UI 标注"未使用幕布" |
| depth 控制图 | 映射 `control_image`(depth) | 该行禁用 + 列出缺节点名 |
| pose 控制图 | 映射 `control_image`(pose) | 该行禁用 + 列出缺节点名 |
| 站位约束 | 恒可用（提示词通道，已实现） | — |

三态在 UI 上必须可区分（已映射 ✅ / 能力缺失禁用 ✕ / 文本降级 ⚠），并且**每一态都要能点开看实际发生了什么**。

---

## 8. 迁移路径（三步，每步可独立交付）

| 步 | 内容 | 可独立验收 |
|---|---|---|
| **Step 1** | 幕布库页（只读）+ `POST /scenes/{id}/promote-backdrop` 收编现有幕布 | 旧 `scene.reference_image` 收编后不丢图、不改动生成结果（此步不动生成链路） |
| **Step 2** | 镜头绑定 + G1 接入 + `jobs.plate_snapshot` | 同一 job 重跑拿到同一版幕布；改幕布不影响已通过镜头 |
| **Step 3** | 版本另存 UI + 控制图探测 + 站位可视化 | 删除被引用幕布被阻断；未映射槽位有明确标注 |

**建议先做 Step 2 的 G1 那一小块**（约十几行 + 一次模板 `_aivideo_map` 修改）。它单独就能把"幕布白存了"这个问题消掉，而且不需要动库、不需要动前端。

---

## 9. 验收

1. 改幕布**不改变**已通过镜头的可复现性：同一 job 重跑使用其 `plate_snapshot` 记录的那一版；`sha256` 与快照一致。
2. 删除被引用的幕布返回 **409 且附引用清单**；未被引用的可删。
3. 模板未映射 `scene_plate` 时：`params` 里**没有**幕布内容，且 UI 有明确标注（不得静默）。
4. 站位图形化产物与手工写 `composition` 结果**逐字一致**（回归 `compose_motion_prompt` 既有输出）。
5. 控制图：工作流缺节点时该项禁用且列出缺失节点名，任务仍可正常提交（不阻断主流程）。
6. 老数据：`scenes.reference_image` 为非空字符串的老项目，打开项目不报错、生成结果与改造前一致。

---

## 10. 未决问题

1. **归属**：幕布属场景还是项目？本方案给"池 + 默认绑定"两层（§3），**需确认**。
2. **幕布在生成里是 i2v 首帧语义，还是参考图语义？** 两者对结果影响不同，需要实验定；这决定 `scene_plate` 映射到哪个节点输入。
3. **depth 预处理节点选型**（DepthAnything / MiDaS / Zoe …），直接决定 §6.3 的探测名单。
4. **`equirect`（720°全景）在当前链路里如何使用？** 本方案先按"它就是一张更宽的平铺图"处理；若要做多机位裁切，需另立设计。
5. 是否需要"幕布被引用"的**批量重绑**入口（换主图时把引用方一次性升级到新版本）？本方案暂不提供，只允许逐个镜头改。

---

## 附：不做成 3D 编辑器的理由

参照对象 LibTV 的「导演台」是云端算力支撑的轻量 3D 构图节点：720° 全景贴到全景球上当背景 → 摆人体素模 → 多机位截图 → 截图喂视频模型。它成立的前提是（1）云端有算力余量，（2）全景球是"假 3D"，只保证背景透视一致、**不保证几何正确**——人站进墙里、脚陷进地面，模型会忠实照抄。

NMVideo 的等价物是：**一张摆好站位的图 → depth / pose 控制图 → 送进 ComfyUI 控制节点**。控制力比"让模型看懂全景"更硬，交互成本低一个数量级，且不引入 3D 编辑器与额外显存。
