# 第三批 3A：数据保护门禁 — 实施报告

> 对应《下一阶段改造与优化方案.md》工作包 3A + 审查项 S1/S3/I1/O1/D1。
> 前置依赖 2A（统一数据库访问 + 事务边界）已于 ec77a38 提交，本包全部建立在其上。

## 一、变更内容

### 1. 数据库 schema（第三批迁移，`app/db.py`）

- `projects.revision INTEGER DEFAULT 0`：项目内容版本号，导入预览绑定用
- `shots.review_stage TEXT DEFAULT ''`：审核三态标记（design / generation / video / ''）
- `review_history.stage TEXT DEFAULT ''`：审核记录所属门
- 迁移与 2A 迁移同一套 `CREATE TABLE IF NOT EXISTS + ALTER TABLE` 机制，旧库平滑升级
- 新增 `Database.auto_backup(prefix)`：按约定（库同级 `backups/` + UTC 时间戳）做一致性备份

### 2. 审核三态分离（`app/storyboard.py` / `app/shotcheck.py`）

- 旧 status 状态机（draft/queued/generating/review/approved/rework）**不动**，
  `review_stage` 只表达"当前处于哪个审核门"，旧客户端忽略即可，向后兼容
- `check_design(shot)`：设计审核（生成前）——时长/提示词长度/对白配音/角色参考图/模式参考图，
  **不要求有生成版本**；`check_shot` 保持面向视频审核不变
- `review_shot(shot_id, action, version_id, comment, stage, expected_version_id)`：
  - `stage=design`：approve → `review_stage=design_ok`；rework → `design`；不推进生成状态机；
    阻断性设计问题 → `ReviewBlockedError`（422 + issues + score）并留痕 `design_blocked`
  - `stage=video`（默认，兼容旧行为）：质检前置（S1）——QA 在**内存候选版本**上跑，
    阻断时指针与状态均保持原值；通过后同一事务原子写「采用指针 + 状态 + 审核记录」
  - `expected_version_id` 与当前采用版本不一致 → `StaleSubmissionError`（409 过期提交）

### 3. 引用保护（I1/O1）

- `shot_references / scene_references / project_references / job_references` + `_guard_no_references`：
  时间线片段、进行中任务、任务产物（版本）都算引用
- 以下操作有引用即 409 + 明细，不做静默清理：
  - `delete_shot / delete_scene / delete_project`
  - `adopt_version` 换指针、`delete_version` 删当前版本、`review_shot` 换版本
  - `DELETE /api/jobs/{id}`（画廊误删正在剪辑使用的视频）
- 已通过（approved）镜头换采用版本 → 回到 `review + review_stage=video`（新版本不继承 approved）

### 4. 导入保护（S3）

- `script_preview` 返回 `content_hash`（sha256）+ `project_revision`，不写库
- `import_script` 新参数 `expected_revision / content_hash / confirm`：
  - hash 不匹配 → 400；revision 过期 → 409（预览之后项目被改过）
  - rebuild 将损失已生成版本/时间线时必须 `confirm=true`，否则 409 + 损失明细；
    确认后**先一致性备份**（`backups/aivideo-pre-rebuild-*.db`）再进事务重建
  - 每次成功导入 revision 同事务自增

### 5. 队列联动（`app/queue_manager.py`）

- 生成完成：落候选版本 → `review_stage=video`；**被时间线引用的镜头只追加候选、
  不静默更换 `current_version_id`**（是否采用由用户在审核中显式决定）
- 失败/取消/重启中断/引擎加载失败：`review_stage=""`（无待审成片）
- 入队时 `review_stage=generation`（router `generate_shot`）

### 6. 前端适配（最小）

- `api.js`：`reviewShot` 增 `stage / expected_version_id`；`importScript` 增
  `expected_revision / content_hash / confirm`；`shotQuality` 增 `stage`；
  错误对象挂 `status / data`（409 的 loss/references、422 的 issues/score 才能展示）
- `ReviewDialog.vue`：审核门切换（设计/视频）+ 三态徽标 + 422 结构化明细直显 +
  视频审核提交带 `expected_version_id` 过期防护
- `ShotCard.vue`：`review_stage` 徽标（design/design_ok/generation/video）+「📐 设计审核」入口
- `ProjectView.vue`：导入流程改为「预览绑定 → 提交 → 409 损失明细二次确认 → confirm 重试」；
  设计审核门默认打开设计页
- 后端 `GET /shots/{id}/quality?stage=design|video`（默认 video，行为不变）

## 二、测试结果（全部真实执行）

```
backend/.venv/Scripts/python.exe -m pytest tests/     # env -u PYTHONPATH
======================= 43 passed, 1 warning in 55.71s ======================
```

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| tests/test_db_concurrency.py 等（2A） | 13 | 并发/事务/旧库审计（ec77a38 起） |
| tests/test_protection_gates.py（3A 数据层） | 24 | 三态流转、QA 前置不改状态、过期提交、跨镜头归属、文件缺失拒绝、时间线引用拒删/拒换、任务引用、预览绑定、破坏性重建确认+备份、队列完成/失败路径 |
| tests/test_http_protection_api.py（3A 接口） | 6 | 422 带 issues/score、400/409 契约、references/loss 明细、adopt/delete 指针回退、导入保护全流程 |

`vite build` 通过（4.03s）。

## 三、实施中修复的实现 bug（测试抓出）

1. **`db.backup()` 无参调用**（storyboard import_script）：`backup(dest)` 缺参会
   TypeError，破坏性重建确认路径必崩 → 新增 `auto_backup()` 按约定命名
2. **`adopt_version` 只校验不写指针**：docstring 写"current_version_id 指向它"，
   代码从未执行指针更新（采用后 current_version_id 不变）→ 原子写指针 +
   approved 镜头回 video 审核
3. **`_destructive_loss` 版本数恒 0**：`list_scenes` 的分镜不带 versions 字段，
   `shot.get("versions")` 永远空 → 破坏性确认门禁漏统计已生成版本 → 直查 shot_versions 表
4. **`routers/jobs.py` 缺 `sb` 导入**：删除任务守卫 `NameError`（接口测试抓出）

## 四、剩余风险 / 后续

- 引用保护目前是「过渡策略：有引用即拒绝」。更细的做法是「移除引用后才允许」的
  引导流程（前端已能从 409 明细里看到引用位置），下批可做一键定位
- `review_stage` 三态目前只到「标记 + 门禁」层面；审核工作流（待审列表、批量审核）
  属于下一批「审核流」范围，本批未做（遵循本批不加大型功能约束）
- 前端 3A 适配只做了冒烟级构建验证；Windows Electron 全链路验证（含这些弹窗交互）
  排在 Electron 整合子任务之后统一做
- ComfyUI 任务可靠性（2B）、输入上传与产物原样归档（2C）尚未开始

## 五、回滚点

- 本包开始前 tag：`phase2-start`（63c5b5a）
- 本包提交：见 git log（`phase3a` 系列）
