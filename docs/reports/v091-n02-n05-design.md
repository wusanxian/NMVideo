# NMVideo 小说漫改流水线：PR-N02~N05 设计文档

版本：v091（2026-10-21）
基线：`origin/main = 13459b9`（用户侧 FreeVideo 阶段实现）
分支：`feature/pr-n01-novel-adaptation`（PR-N01~N04 已实现并推送：394cada / 36b3d19 / 0a5fcd3 / 627ccbf）
本文档分两部分：**已实现部分（N02~N04）的 as-built 数据模型与 Schema 档案** + **PR-N05（预览转正与安全提交）的落地设计**。

---

## 0. 总览：工序次序与审核门禁

```
小说原文（只读）          章节（偏移切片，只读原文）
   │                          │
   ▼                          ▼
AI 章节分析（逐章）──→ 故事圣经（人物/场景/时间线/关系）
   │                          │
   ▼                          ▼
自动分集大纲 ──→ 动漫剧本（工作台剧本格式）──审核门禁①──→ 结构化分镜
                                                              │
                                                       审核门禁②
                                                              │
                                            ┌─────────────────┼──────────────────┐
                                            ▼                 ▼                  ▼
                                      交接项目（已实现）   预览档生成(N05)    批量安全提交(N05)
                                            │                 │                  │
                                            ▼                 ▼                  ▼
                                      分镜工作台          人审 adopt 转正     依赖图入队，失败只返工指定镜头
```

**核心不变量**（全链路强制执行）：
1. 原文只读——任何工序只写偏移/引用，不回写 `novels.content`；
2. 每道工序的产物是「草案」：可人工编辑、可重新生成、审核后才流向下游；
3. 审核即快照——approve 落不可变版本，审核后再改动自动回草稿态；
4. 不覆盖——目标项目已有镜头版本/时间线/进行中任务时，交接与提交一律 400 拒绝；
5. 不自动生成——交接只建数据；预览/正式提交必须显式触发（N05）。

---

## 1. PR-N02 故事圣经与人物关系（已实现：36b3d19）

### 1.1 数据模型（7 张表，纯增量迁移，旧库零改写）

```
novels ──1:N──> novel_chapters          （N01：原文 + 章节偏移）
   │
   └──1:1──> novel_bibles               （圣经头：status/version/summary/analysis_model）
                 │
                 ├──1:N──> novel_bible_versions   （不可变审核快照）
                 ├──1:N──> bible_characters       （人物：name/aliases/role/appearance/
                 │                                   clothing/personality/catchphrase/chapters）
                 ├──1:N──> bible_scenes           （场景：name/description/atmosphere/chapters）
                 ├──1:N──> bible_timeline         （时间线：seq/title/summary/character_names/chapters）
                 └──1:N──> bible_relations        （关系：from_character/to_character/
                                                     relation_type/description/chapters）

novel_chapter_analyses                   （只挂 novel_id！章节重识别不抹分析成果）
   （chapter_id/chapter_title/chapter_start/chapter_end/status/summary/data/error/model）
```

设计要点：
- **分析表只挂 novel_id**：章节被重新识别（拆分/合并/重命名）后，旧分析行按 `chapter_id` 失效清理，但同章内容的新分析不冲突——`UNIQUE(novel_id, chapter_id)` 保证一章一行。
- **所有实体的 `chapters` 列是章节快照**（`[{chapter_id, title, start, end}]`）：章节后来被删改，实体仍显示原文位置，满足「AI 结果保留原文来源」。
- **人物别称归一**：跨章同一人物的不同写法（「少年」→「林川」）由 LLM 合并建议 `{canonical, absorb[]}` 处理；`_apply_merges` 把 absorb 名并入 canonical 的 aliases，并重映射时间线人名与关系两端。合并建议失败自动回落本地确定性合并（同名合并、别称并集），不阻塞流程。

### 1.2 章节分析响应 JSON Schema（LLM 输出，容错后入库）

```json
{
  "summary": "string（本章梗概，必填）",
  "characters": [{
    "name": "string（标准名，必填）",
    "aliases": ["string（别称/称呼）"],
    "role": "主角|配角|...",
    "appearance": "string（外貌）",
    "clothing": "string（服装）",
    "personality": "string（性格）",
    "catchphrase": "string（口头禅）"
  }],
  "scenes": [{"name": "string", "description": "string", "atmosphere": "string"}],
  "events": [{"title": "string", "summary": "string", "characters": ["string"]}],
  "relations": [{"from": "string", "to": "string", "type": "string", "description": "string"}]
}
```

LLM 合并建议 Schema：`{"merges": [{"canonical": "string", "absorb": ["string"]}], "summary": "string"}`

### 1.3 接口（`routers/novel_bible.py`）

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | `/api/novels/{id}/analyze` | 启动后台分析；默认跳过已完成章；`force=true` 重跑；分析中 409；LLM 未配置 400 |
| GET | `/api/novels/{id}/analyze` | 进度轮询：total/done/error/running + 逐章状态 |
| GET | `/api/novels/{id}/bible` | 完整圣经（未生成 404） |
| POST | `/api/novels/{id}/bible/generate` | 汇总已完成分析 → 圣经草案（实体替换，版本历史保留） |
| POST | `/api/novels/{id}/bible/approve` | 审核：不可变快照 → `novel_bible_versions` |
| GET/PUT/DELETE | `/api/novels/{id}/bible/{kind}/{eid}` | 四类实体 CRUD（kind=characters/scenes/timeline/relations），字段白名单 |

### 1.4 测试证据

`tests/test_novel_bible.py` 12 例：分析→生成→定位、未配置 400、跳过已完成/force、乱码章 error 不影响他章、并发 409、合并失败回落本地、实体 CRUD、审核快照不可变 + 再审核 v2、重识别保留分析、删小说级联、旧接口兼容。

---

## 2. PR-N03 自动分集与动漫剧本（已实现：0a5fcd3）

### 2.1 数据模型（4 张表）

```
novels ──1:N──> novel_episodes            （seq/title/logline/target_duration_sec/
   │                                          chapter_refs/beats/status/version）
   │                  └──1:N──> novel_episode_versions   （不可变审核快照）
   │
   └──1:N──> novel_scripts                （episode_id UNIQUE：一分集一剧本）
                      └──1:N──> novel_script_versions    （不可变审核快照）
```

- `novel_scripts.content` 是**工作台剧本格式文本**（`场景X：标题` + 画面行 `（景别, 运动, 时长）` + 对白行），`scene_count/shot_count` 由 `parse_script` 实算落库——生成时即校验，不是事后。
- `project_id` 记录剧本交接去向（幂等锚点）。

### 2.2 分集大纲 JSON Schema（LLM 输出）

```json
{
  "episodes": [{
    "title": "string（集名，如「第1话 竹林对峙」）",
    "logline": "string（一句话梗概）",
    "chapter_titles": ["string（引用章节标题，按标题解析为章节快照）"],
    "duration_sec": 90,
    "beats": [{"title": "string", "summary": "string"}]
  }]
}
```

约束：引用了不存在章节的集**整集丢弃**（宁缺毋滥，保证可定位原文）；`duration_sec` 钳制 30–300。

### 2.3 动漫剧本生成契约

输入 = 章节原文（单章 4000 字 / 总量 12000 字截断，控 token）+ 圣经人物/场景切片 + 节拍结构；
输出 = 工作台剧本格式文本；**生成时即调 `parse_script` 校验**（必须有场景标记且镜头数 > 0），不合规自动重试一次，两次失败明确 400（不落脏数据）。

### 2.4 接口（`routers/novel_script.py`）

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | `/api/novels/{id}/episodes/generate` | LLM 分集大纲（依赖圣经；未配置 400） |
| GET/POST/PUT/DELETE | `/api/novels/{id}/episodes[/{eid}]` | 分集 CRUD（白名单） |
| POST | `/api/novels/{id}/episodes/{eid}/approve` | 审核快照 |
| GET | `/api/novels/{id}/episodes/{eid}/versions/{v}` | 版本回看（404 语义） |
| POST/GET/PUT | `/api/novels/{id}/episodes/{eid}/script[/generate]` | 剧本生成/读取/编辑（编辑时格式校验） |
| POST | `/api/novels/{id}/episodes/{eid}/script/approve` | 剧本审核 |
| POST | `/api/novels/{id}/episodes/{eid}/script/handoff` | 交接：create_project + import_script；幂等 |

### 2.5 测试证据

`tests/test_novel_episode_script.py` 18 例：大纲生命周期、依赖圣经 400、未配置 400、JSON 重试失败转 400、无效集丢弃、时长钳制、**剧本格式与 `parse_script` 真兼容**（2 场 3 镜）、剧本重试合规、编辑格式校验、审核快照不可变、交接创建+幂等+项目删除后可重建、prompt 取材断言、级联、旧接口兼容。

---

## 3. PR-N04 结构化分镜（已实现：627ccbf）

### 3.1 数据模型（3 张表）

```
novel_episodes ──1:1──> novel_shot_plans     （status/version/shot_count/
   │                        total_duration_sec/script_id/project_id/model）
   │                            └──1:N──> novel_shot_plan_versions（不可变审核快照）
   └──1:N──> novel_shot_items               （plan_id 级联）
```

`novel_shot_items` 字段（与工作台 shots 表对齐的枚举，不复辟新词表）：

| 字段 | 取值 | 说明 |
|---|---|---|
| `seq` | 1..N | 顺序即故事顺序（reorder 上下交换） |
| `scene_title` | string | 场景分组（交接时映射为 Scene） |
| `shot_size` | 远景/全景/中景/近景/特写 | 越界回落「中景」 |
| `camera_movement` | 固定/推/拉/摇/移/跟 | 存储含「镜」后缀（推镜），与工作台一致 |
| `action` | string | 画面动作描述 |
| `dialogue` | string | 对白 |
| `character_names` | [string] | 出场人物（圣经标准名；未知人名保留不静默丢弃） |
| `position` | string | 走位/出入画 |
| `emotion` | string | 情绪节拍 |
| `duration_sec` | 1–15 | 服务端钳制 |
| `mode` | t2v/i2v/first_last/continuation | **首镜头强制 t2v**（无可续接前序） |
| `chapter_refs` | [快照] | 定位原小说（LLM 未给则回落分集章节快照） |

### 3.2 分镜计划 JSON Schema（LLM 输出）

```json
{
  "scenes": [{
    "title": "string（场景名，尽量复用圣经场景名）",
    "shots": [{
      "title": "string",
      "shot_size": "远景|全景|中景|近景|特写",
      "camera_movement": "固定|推|拉|摇|移|跟",
      "action": "string",
      "dialogue": "string",
      "characters": ["string（圣经标准名）"],
      "position": "string（走位/出入画）",
      "emotion": "string",
      "duration_sec": 3,
      "mode": "t2v|i2v|first_last|continuation"
    }]
  }]
}
```

### 3.3 审核、版本与安全提交机制（as-built）

1. **工序次序门禁**：分镜生成要求剧本 `status=approved`（400 明示）——「审核后才进分镜」。
2. **审核快照**：approve 把「整计划 + 全部镜头」存为 `novel_shot_plan_versions` 不可变 JSON；审核后任何编辑回草稿态；重新生成替换计划（旧版本随旧计划级联删除——文档化语义）。
3. **交接安全闸**（`handoff`）：
   - 计划未审核 → 400；
   - 目标项目已有镜头版本/时间线/进行中任务 → 400 拒绝覆盖（保护旧项目与已完成视频）；
   - 幂等：`plan.project_id` → 剧本 `project_id` → 新建；项目被删可重建；重复交接 `rebuilt=true` 按审核态重建。
4. **交接落位**：Scene（圣经场景描述）/ Shot（景别/机位/时长/对白/模式/`character_ids` 关联角色卡 / prompt 自动拼「动作+场景描述+人物外貌服装」/ note 补写情绪走位）。**只建数据，零 job**。

### 3.4 测试证据

`tests/test_novel_shot_plan.py` 15 例 + `test_novel_single_chapter_e2e.py` 1 例（单章小说端到端验收：导入→分析→圣经→分集→剧本→分镜→人工修改→重新生成→审核→交接→项目内可编辑→零任务→旧项目无损）。

---

## 4. PR-N05 预览转正与安全提交（设计，待实现）

### 4.1 目标

把「审核过的结构化分镜」变成「低成本预览 → 人审 → 正式生成的完整剧情动漫」：

1. **预览档**：按分镜计划批量生成低质量预览（低帧数/低分辨率/少步数），每镜独立任务；
2. **转正**：预览候选版本人审 adopt 后标记为正式版（复用现有 `shot_versions` / `adopt_version`）；
3. **安全提交**：正式生成按依赖图入队（continuation 镜头等前序完成），失败只返工指定镜头；
4. **预算闸**：提交前按镜头数/时长/质量档位估算算力成本，超限阻断（可配置上限）。

### 4.2 数据模型（新增 2 张表，第十一批迁移）

```
novel_shot_plans ──1:N──> novel_submissions        （提交批次）
                              │    （id/plan_id/kind[preview|final]/status/
                              │     shot_count/budget_estimate/note/created_at）
                              └──1:N──> novel_submission_items
                                   （id/submission_id/shot_item_id/shot_id/job_id/
                                     status[pending|queued|running|done|failed|cancelled]/
                                     error/created_at/updated_at）
```

- `novel_submission_items.job_id` 关联现有 `jobs` 表——**不新建队列**，复用 queue_manager/worker；
- `shot_item_id` → 交接后落地的 `shots.id`（`shot_id`），失败返工时按 item 定位；
- 批次 `status`：`pending → submitting → running → done | partial | failed | cancelled`。

### 4.3 预览/提交参数 Schema

```json
{
  "kind": "preview|final",
  "quality": "draft|standard|high",
  "shot_item_ids": ["string（可选，默认全部已审核镜头）"],
  "budget_limit_sec": 3600
}
```

预览档参数映射（复用 `quality.py` 档位预设，不新造词表）：draft = 8 帧/低分辨率/少步数；standard = 默认；high = 高质量。continuation 镜头的 `continue_from` 留空——生成时默认取上一镜头末帧（工作台既有语义，N04 已按此落地）。

### 4.4 安全提交机制

1. **前置校验**（全部通过才建批次）：
   - 计划已审核；目标项目已交接（`plan.project_id` 非空且项目存在）；
   - 每个镜头：角色卡存在（bible 建档）、模式合法、continuation 镜头的前序镜头存在；
   - 项目无「进行中任务」与提交批次（避免并发双跑）；预算估算 ≤ 上限。
2. **依赖图入队**：按 `seq` 顺序提交；continuation 镜头标记 `depends_on`（前序 item），前序 `done` 后才入队——用现有 `jobs` + 轮询实现，不引新调度器。
3. **失败隔离**：单镜失败只把该 item 标 `failed`（可单独 retry），批次转 `partial`；**不级联取消**已成功镜头（返工只返工指定镜头）。
4. **人审门禁**：预览批次完成后，逐镜 adopt 才允许建 final 批次（复用现有 adopt 端点，`novel_submission_items` 记 `adopted_version_id`）。

### 4.5 接口设计（`routers/novel_submit.py`，新增）

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | `/api/novels/{id}/episodes/{eid}/shots/submit` | 建提交批次（预览或正式），返回批次 + 估算 |
| GET | `/api/novels/{id}/episodes/{eid}/shots/submissions` | 批次列表 + 逐 item 状态 |
| POST | `.../submissions/{sid}/retry` | 重试失败 item（只重试失败镜） |
| POST | `.../submissions/{sid}/cancel` | 取消未完成 item（不杀进行中生成，等其自然完成） |

### 4.6 涉及文件

- 新增：`backend/app/novel_submit.py`、`backend/app/routers/novel_submit.py`、`backend/tests/test_novel_submit.py`
- 修改：`backend/app/db.py`（第十一批 2 表 + `_FK_TABLES`）、`backend/app/main.py`（注册）、`frontend/src/api.js` + `NovelShotPanel.vue`（提交工具条 + 批次进度 + 失败重试）
- 复用不改：`engines/`、`queue_manager.py`、`generation.py`、`quality.py`、`storyboard.py` 的 generate/adopt 端点

---

## 5. 兼容性与风险登记

| 风险 | 对策 |
|---|---|
| 增量迁移破坏旧库 | 全部 `CREATE TABLE IF NOT EXISTS` + 纯新增列；不 DROP/不重建；N01~N04 已用「建新库 + 重开库」双验证 |
| LLM 输出不可靠 | JSON 提取 + schema 容错 + 重试一次 + 人工编辑兜底；脏数据不落库（400 明示） |
| 覆盖旧项目/成片 | 交接与提交的「已有成果拒绝」闸；`shot_versions`/时间线计数为闸门 |
| 重复提交/双跑 | 批次 pending 唯一性 + 项目级进行中任务检查；复用 jobs 表不建第二队列 |
| 章节重识别抹成果 | 分析表只挂 novel_id；版本快照自带章节快照（偏移定位） |
| token 爆炸 | 章节原文截断（单章 4000/总量 12000）；bible 切片注入而非全文 |

## 6. 回滚方式

每批独立 commit（分支 `feature/pr-n01-novel-adaptation`）：`git revert <sha>` 或 `git reset --hard <上一批 sha>`。数据层回滚 = 停服后删除增量表（`novel_*`/`bible_*` 与 projects/scenes/shots/jobs 完全隔离，删表不碰业务数据）；迁移只在启动时执行，回滚代码后旧库表残留但无代码引用，无害。

## 7. 验收对照（本批次）

| 验收要求 | 状态 | 证据 |
|---|---|---|
| 一章小说 → 完整可编辑动漫分镜 | ✅ | `test_novel_single_chapter_e2e.py`（单章「全文」退化 → 全链路 → 项目内 PATCH 镜头成功） |
| AI 结果保留原文来源 | ✅ | 章节快照（id+标题+起止偏移）挂在分集/镜头/实体；E2E 断言原文切片一致 |
| 支持人工修改和重新生成 | ✅ | PUT 镜头字段白名单 + 重新生成替换草案；E2E 断言修改→重生成→再审核 |
| 不覆盖旧项目/已完成视频 | ✅ | E2E 预置旧项目断言无损；N04 已有成果拒绝用例 |
| 不自动启动生成 | ✅ | E2E 断言 `jobs` 表为 0、镜头无 job_id |
| 真实测试 + 失败记录 + 回滚方式 | ✅ | 全量 pytest 实跑（见提交信息）；失败项与修复见 §8 |
| 未经授权不推送远端 | ✅ | 本批只 commit 不 push |

## 8. 本批测试失败记录（如实登记）

1. E2E 首跑 400「LLM 服务未配置」——测试自伤：定义了 `_patch_llm` 辅助函数却忘记调用（mock 未生效，走真实未配置路径）。修复：补调用。非产品缺陷。
2. 定位过程中用最小 pytest 复现验证 monkeypatch 对 `from . import llm` 模块属性调用可见（排除 patch 命名空间疑虑），确认是测试漏调用而非产品问题。
