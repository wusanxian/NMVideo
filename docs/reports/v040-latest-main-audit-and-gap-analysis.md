# NMVideo 最新代码审查报告（2026-10-18）

- 角色：技术负责人本轮审查
- 审查对象：`origin/main` 最新代码 + 开发线 `hotfix/p0-comfy-robustness`（PR-01~06 修复所在）
- 执行环境：本机沙箱（无 GPU、无真实 ComfyUI、无 Windows GUI）；所有「未执行」项如实标注

---

## 一、第一阶段：最新代码确认

### 1.1 远端状态

| 项 | 结果 |
|---|---|
| `git fetch origin main` | **两次失败**（`schannel: server closed abruptly`）——GitHub 瞬时不可达（与改造.md §0 记录的现象一致） |
| 本地 `origin/main` 跟踪引用 | `851e21a4877f96a66e9b275c90571d9a2e4dc422` |
| 可信度 | 该引用于 10-18 早间经成功 fetch 确认、当日两次成功 push 交叉验证；live fetch 失败不影响结论 |
| 工作区 | **干净**（无未提交修改、无 stash）——本批所有改动经 commit/push 落库，不存在被覆盖风险 |

### 1.2 origin/main 最近 15 次提交（含主要文件）

| SHA | 主题 | 主要文件 |
|---|---|---|
| 851e21a | feat(Electron): 桌面壳面板化 | desktop/renderer/*、desktop/main.js、electron-smoke |
| 21a0ff2 | feat(2C): 输入上传与产物原样归档 | comfyui.py、engines/comfy.py、queue_manager.py、video.py |
| 879b17c | feat(2B): ComfyUI 任务可靠性 | comfyui.py、db.py、engines/*、queue_manager.py、storyboard.py |
| 807344c | feat(3A): 数据保护门禁 | db.py、shotcheck.py、storyboard.py、routers/* |
| ec77a38 | feat(2A): 数据库访问收口与事务边界 | db.py、assets.py、tracks.py、db_maintenance.py |
| 0a8dc82 | docs: 第二阶段审查报告与清单 | docs/reports/phase2-review.md |
| 63c5b5a | feat: FreeVideo 增量改造任务书（第一版） | 改造.md |
| 445cbe0 | fix: 桌面工作区整合与导航加固 | desktop/* |
| 359f7be | docs: phase 0 基线 | docs/reports/* |
| cb5b655 | fix: 审核拦截/版本归属/导入安全/队列恢复 | backend/app/* |
| ea1eaa7 | feat: Electron 桌面端 + launcher 适配 | desktop/*、packaging/* |
| 29784b0 | feat: 分镜工作台 + 剧情动画增强 + 模型服务层 | backend/app/*、frontend/src/* |
| cb640bf | style: CRLF 规范化 | 全仓库 |
| 0af3f7d | chore: 扩充 .gitignore | .gitignore |
| 2e3f1ff | Initial commit | 全仓库 |

### 1.3 与 docs/reports/ 已完成工作对照

`docs/reports/` 现有 11 份：baseline / phase1 / phase2-review / phase2a / phase2b / phase2c / phase3a / phase2-electron / hotfix-p0-comfy-robustness / v021-hotfix-hf00-hf06 / v031-sprint0-pr06-snapshot-immutability。

**关键结构性事实**：`origin/main`（851e21a）**不包含** PR-01~06 的任何修复——它们全部在开发线 `hotfix/p0-comfy-robustness` 上（领先 main 6 个提交：`0213d88` → `0ca645a` → `f51f3d5` → `f2d1093` → `e954c38` → `4b9c6bf`，main 是 HEAD 祖先，可干净快进合并）。因此下表分两列呈现。

---

## 二、第二阶段：生成可靠性五项核对

审查文件：`engines/comfy.py`、`queue_manager.py`、`comfyui.py`、`routers/storyboard.py`。

### 2.1 对照表（已完成 / 未完成 / 存在回归 / 需要新增）

| # | 检查项 | origin/main (851e21a) | 开发线 (4b9c6bf) | 源码位置（开发线） | 测试证据（本轮实跑） |
|---|---|---|---|---|---|
| 1 | ComfyUI prompt_id 持久化 | **未完成**（基线缺陷） | **已完成** | `comfyui.py:90-104` submit 返 prompt_id；`engines/comfy.py:153-155` `/prompt` 返回即 `on_submitted`（先于 `_wait`）；`queue_manager.py:302-310` 闭包单条写库（prompt_id+client_id）；`db.py:95-97` jobs 三列 | `test_engine_on_submitted_fires_before_wait`、`test_engine_on_submitted_fires_even_if_wait_raises`、`test_queue_prompt_id_persisted_before_wait_completes`、`test_restart_reconcile_resume_completes` |
| 2 | 任务结果跨任务隔离 | **未完成**（原生视频串片） | **已完成** | `engines/comfy.py:107-112` `generate()` 入口清零 `last_prompt_id`/`last_native_video`（引擎实例被 `_engine_instances` 跨任务缓存） | `test_engine_native_video_not_inherited_across_jobs`（A→B→A 三轮交替）、`test_engine_no_stale_native_video_after_failure`、`test_queue_native_video_not_inherited` |
| 3 | 上传/预处理失败不留可执行任务 | **未完成**（400 留 pending 僵尸） | **已完成** | `routers/storyboard.py:401-445` 备料先行：模板加载/参考图上传/构建 graph/非空预检全部在 `create_job`（:445）之前 | `test_upload_failure_leaves_no_job`、`test_empty_template_rejected_before_job_creation`、`test_upload_success_creates_job_with_snapshot` |
| 4 | 服务断线恢复（不确定态） | **未完成**（网络异常误判 lost） | **已完成** | `comfyui.py:289-320` probe 六态（`temporarily_unavailable ≠ lost`）；`queue_manager.py:57-191` `_recover_stale_jobs`：needs_attention 待核查 + 再探循环；`queue_manager.py:39-55` `_iter_jobs` 200/页分页；`routers/jobs.py:88-100` 手动重试端点 | `test_probe_temporarily_unavailable_on_network_error`、`test_probe_lost_only_when_reachable_and_absent`、`test_recover_comfy_unavailable_marks_needs_attention`、`test_recover_needs_attention_resumes_when_back`、`test_iter_jobs_paginates_beyond_one_page`、`test_retry_needs_attention_job` |
| 5 | 受管文件路径校验 | **未完成**（`../` 可逃逸） | **已完成** | `managed_paths.py:83` `resolve_managed_asset` 四道闸（段检查/盘符绝对拦截/realpath 边界/扩展名白名单）；comfyui/export/storyboard/tracks/shotcheck/router 六处接入 | `test_managed_paths.py` 27 例（`../`、`%2e%2e%2f`、反斜杠、UNC、外链、符号链接逃逸均拒） |

**存在回归：无。** 开发线全量 135 passed / 1 skipped（10-18 早间），本轮复跑可靠性+受管路径两文件 **72 passed / 1 skipped**（21.87s，证据新鲜）；唯一 skip 为沙箱 mklink 限制的符号链接用例（诚实跳过）。

### 2.2 专项审查：submitting 不确定态（PR-02 提到的「避免双重提交」）

**场景**：进程崩溃发生于「ComfyUI 已受理 `/prompt`」与「`on_submitted` 写库」之间的窗口 → job 为 running 但无 prompt_id。

**现状**（`queue_manager.py:91-92,131-134`）：running + comfyui + 无 prompt_id → 落入明确失败（「生成在服务重启时中断，请重新生成」），**不会盲目重提**。

**技术负责人结论：维持现状，不做修改。** 理由：
1. 无 prompt_id 时无法探活（ComfyUI `/history`、`/queue` 均不支持按 client_id 反查）；
2. 若改标记 needs_attention，其唯一恢复动作（retry 端点重入队）会触发**重新提交**——万一孤立任务仍在远端执行，即构成文档明令禁止的「双重提交」；
3. 明确失败 + 用户重新触发的语义安全且可恢复，孤立远端任务（若存在）自行终结、产物不被归档，无数据损坏风险。

此项记入设计决策，不在本批改动。

### 2.3 第二阶段结论

五项在开发线**全部已修复**（提交 `0213d88`/`f51f3d5`/`f2d1093`，含上表证据）。按「已修复则不重复修改」原则，**本批无 P0 代码改动**。遗留动作只有一个：**将 `hotfix/p0-comfy-robustness` 合入 `origin/main`**（快进，无冲突风险）——按项目惯例合入时机待你确认，本报告不擅自执行。

---

## 三、第三阶段：FreeVideo 功能差距审查（8 项）

| # | 功能 | 当前实现 | 缺失部分 | 需修改文件 | 依赖 | 验收方案 |
|---|---|---|---|---|---|---|
| 1 | 独立首帧+尾帧 | UI 有 `first_last` 模式（`ShotEditorDialog.vue:271`），但只提供**单一** `reference_image` 输入（:89-92 i2v/first_last 共用）；router 只要求一张图（`routers/storyboard.py:368-370`）；shotcheck 同（`shotcheck.py:67`）；工作流只映射一个 `reference_image` 节点 | 独立的 `last_frame` 输入；两图分别注入首/尾帧节点；工作流不支持尾帧时禁用并明示 | `db.py`（shots 加列）、`routers/storyboard.py`、`engines/comfy.py`、`comfyui.py`（map 扩展）、`ShotEditorDialog.vue` | PR-07 能力声明（supports.first_last） | 首尾两张不同图片分别到达正确节点；改前一镜头后提示下游继承帧过期 |
| 2 | 角色多参考图 | 角色卡已存 `reference_images` JSON 数组（`assets.py:68`）+ lora/expressions；生成时仅拼提示词文本（`routers/storyboard.py:372-376`），**图片不进工作流** | 角色参考图批量注入；`reference_asset_ids` 数据模型；入队冻结资产+哈希 | `db.py`、`assets.py`、`routers/storyboard.py`、`engines/comfy.py`、`comfyui.py` | PR-07 能力声明（supports.multi_image） | 多张角色参考图到达各自 LoadImage 节点；同一角色跨镜头参考一致 |
| 3 | 工作流节点能力自动判断 | `_aivideo_map` 手工映射；**无** `/object_info` 探测、无 `required_nodes`、无执行前阻断（全仓库 grep 无 `object_info`） | 能力中心全部：`execution_kind`/`model_id`/`required_nodes`/`supports`/版本元数据；`/object_info` 探测；缺节点/缺模型在点击生成前阻断并给安装指引 | 新建 `app/workflow_capabilities.py`、`comfyui.py`（probe 扩展）、`routers/comfy.py`、`routers/storyboard.py`（生成前阻断） | 无（本项是其他项的地基） | 故意缺节点的工作流被阻断并列出缺项；装齐后放行 |
| 4 | 本地模型 vs 云端付费 API 区分 | presets 四个（`comfy_workflows/presets/`）：`minimax_h3_*` 的 `_meta` **注释字段**注明「API 计费/需自定义节点」，但无程序消费；UI 无「本地/API 计费」标识 | `execution_kind=local\|paid_api` 程序化元数据；ComfySetupView 展示本地/计费、缺失节点、模型大小、能力与资源建议 | `workflow_capabilities.py`（新）、`comfy_workflows/presets/*.json`（补元数据，不动现有配置值）、`ComfySetupView.vue` | 第 3 项 | 界面明确区分两类；计费类缺 API Key 时阻断并提示 |
| 5 | 预览版本升级正式版 | `quality.py` 四档 + `PREVIEW_OVERRIDE`；版本全部 candidate + `adopt_version`（`storyboard.py:547`）；docstring 提及画廊「升级渲染」 | 显式 preview→formal 升级流：保留 seed/镜头参考/工作流快照，跨采样参数明示「不保证完全一致」；双候选同步播放/逐帧对比/评分 | `storyboard.py`、`routers/storyboard.py`、`GalleryView.vue`/`ProjectView.vue`、`quality.py` | 第 8 项（快照）已具备 | 预览版一键升级正式版且 seed 一致；A/B 两候选可对比后采用 |
| 6 | 角色版本固定并关联多镜头 | 角色卡可被多镜头引用（`assets.py:135` characters_of_shot）；`update_character` **就地更新无版本历史** | 角色版本表（外貌/服装/LoRA/参考图快照）；镜头引用具体角色版本；改角色不追溯污染已用版本 | `db.py`（新表+迁移）、`assets.py`、`routers/assets.py`、`storyboard.py` | 第 2 项 | 镜头锁定角色版本 v1；角色改到 v2 后旧镜头重放仍用 v1 参数 |
| 7 | 末帧继承与续接 | `continuation` 模式取上一镜头 `current_version_id` 末帧（`routers/storyboard.py:331-367`，受管路径校验已接入）；抽帧多候选 seek | 选**已批准的具体版本**（而非当前最新）；下游继承帧过期提示；来源版本记入参数快照 | `routers/storyboard.py`、`storyboard.py`、`ShotEditorDialog.vue` | 第 6 项（版本体系） | 指定 v2 抽尾帧续接；上游重生成后下游标「继承帧已过期」 |
| 8 | 视频生成参数快照 | jobs 表参数列 + `prompt_id`/`client_id`/`workflow_snapshot`（`db.py:95-97`）；`shot_versions` 记 prompt/negative/engine/params(JSON)/seed（`storyboard.py:517-531`）；**PR-06 已补快照 schema_version+sha256**（`f2d1093`） | 续接来源 version_id、能力元数据（required_nodes 等）未入快照；preset/preview/hardware 已在 params（基本够用） | `storyboard.py`（create_version 增补字段） | 无 | 任一历史版本可凭快照+参数复现同样工作流输入 |

**差距总判**：可靠性地基（PR-01~06）已齐；FreeVideo 体验的主缺口是 **PR-07 能力中心（第 3/4 项）** 与 **PR-08 输入契约（第 1/2 项）**，二者是第 5~7 项的前置；第 8 项已基本完成，仅需小补。

---

## 四、第一批修改清单（建议下一轮实施：PR-07 后端能力中心）

范围收敛为**纯后端 + 元数据**，不碰 UI 重构、不碰 AI 导演、不做架构调整：

1. **新建 `backend/app/workflow_capabilities.py`**
   - `WorkflowCapability` 数据类：`execution_kind`(local/paid_api)、`model_id`、`required_nodes`、`supports`{t2v,i2v,first_last,multi_image,video_reference,audio_reference,lora}、推荐参数范围、显存提示、`schema_version`、映射版本
   - `load_capabilities()`：从 `comfy_workflows/presets/*.json` 的 `_meta` 读取（旧预设无 `_meta` → 默认 local + 保守 supports，**不覆盖现有配置**）
   - `check_nodes_available(client, cap)`：对 `required_nodes` 逐个查 `/object_info`，返回缺项清单
2. **`comfyui.py`**：`ComfyUIClient.object_info()`（带缓存与暂不可达语义，复用六态风格）；`list_presets()` 返回附带能力摘要
3. **`routers/comfy.py`**：`GET /api/comfy/capabilities` 返回各预设能力 + 缺节点状态（供设置页展示）
4. **`routers/storyboard.py` 生成前阻断**：preset/模板声明的 `required_nodes` 缺失 → 400 带缺项清单（**默认模板/无声明的工作流不受影响**，保证旧行为兼容）
5. **预设元数据补全**：给 4 个 preset 的 `_meta` 补 `execution_kind`/`supports`（minimax_h3 标 paid_api，其余 local）——只加字段不改现有值
6. **测试**（新文件 `tests/test_workflow_capabilities.py`）：能力解析（有/无 _meta）、缺节点判定、生成前阻断（缺节点 400 / 齐备放行 / 无声明放行）、旧预设兼容
7. **验收**：`pytest` 全量绿 + 报告记录；真实 ComfyUI 实机标注「未执行」

明确不做：UI 改造（ComfySetupView 留到 PR-07 第二批）、PR-08 数据模型、PR-10~12。

---

## 五、测试与执行记录（本轮真实执行）

| 项 | 结果 |
|---|---|
| `pytest tests/test_comfy_reliability.py tests/test_managed_paths.py -q` | **72 passed, 1 skipped**（21.87s，10-18 11:5x） |
| 开发线全量（10-18 早间，上一批） | 135 passed, 1 skipped |
| 前端 build | 本批未动前端，未执行（无变更则重跑无意义） |
| Electron Node 测试 / Windows smoke / ComfyUI 实机 | **未执行**（沙箱无 GUI/无实例，如实标注） |

## 六、风险与回滚

- **合并风险**：hotfix→main 为快进合并，无冲突；合入后 main 即含 PR-01~06。未合前 main 上的部署缺失全部可靠性修复——这是当前最大运营风险。
- **回滚**：本批为审查+文档，无代码改动；PR-07 实施批独立 commit，可 `git revert`。
- **网络**：GitHub 瞬时不可达已导致两次 fetch 失败（不影响本地结论）；后续 push 若失败需重试。
