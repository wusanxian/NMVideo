# 改造.md 第一批：Sprint 0 审查 + PR-06 快照不可变收尾

- 日期：2026-10-18
- 分支：`hotfix/p0-comfy-robustness`（接续 `f51f3d5`）
- 依据：`改造.md` §5 交付批次 / §6 执行指令
- 提交：见文末「提交信息」

## 1. Sprint 0：远端 HEAD 与 PR-01~06 映射

### 1.1 远端状态

| 项 | 值 |
|---|---|
| `git fetch origin main` | 首次**两次失败**（`schannel: server closed abruptly` / `Empty reply from server`）——GitHub 当时不可达，与改造.md §0 记录的现象一致；当批 push 成功后重试成功 |
| 本地 `origin/main` 跟踪引用 | `851e21a`（feat(Electron): 桌面壳面板化…） |
| 引用可信度 | 本地跟踪引用与重试成功的 live fetch 结果一致（均 `851e21a`），且昨日（10-17）push 后亦核对过远程即 `851e21a`；Sprint 0 映射结论以此为准 |

`origin/main` 近 5 条：`851e21a`（Electron 桌面壳）→ `21a0ff2`（2C 上传与归档）→ `879b17c`（2B 可靠性）→ `807344c`（3A 数据门禁）→ `ec77a38`（2A 数据库收口）。

### 1.2 PR-01~PR-06 逐项映射（对照当前 HEAD `f51f3d5`）

| PR | 文档要求 | 现状 | 证据（提交/文件/测试） | 结论 |
|---|---|---|---|---|
| PR-01 跨任务隔离 [P0] | `GenerationResult` 含 job_id/prompt_id/native_video_path/frames/metadata；迁移期可先在入口重置旧字段 | **已修（迁移期方案）** | `f51f3d5`：`engines/comfy.py` `generate()` 入口清零 `last_prompt_id`/`last_native_video`；测试 `test_engine_native_video_not_inherited_across_jobs`（A→B→A 三轮交替）、`test_engine_no_stale_native_video_after_failure`、`test_queue_native_video_not_inherited` | 跳过。文档明言「迁移期间可先在每次生成入口重置旧字段」，验收（A→B→A、失败→B、取消→B 不串片）已由测试覆盖；完整 `GenerationResult` 重构不做（文档禁止一次大规模重构，见 §4 差异说明） |
| PR-02 prompt_id 立即持久化 [P0] | `submit()` 返回即 `on_submitted` 落库，再等待；submitting 不确定态避免双重提交 | **已修** | `0213d88`：引擎 `on_submitted(prompt_id, client_id)` 回调 + `jobs.client_id` 列 + 重启 probe 对账；测试 `test_engine_on_submitted_fires_before_wait`、`test_engine_on_submitted_fires_even_if_wait_raises`、`test_queue_prompt_id_persisted_before_wait_completes`、`test_restart_reconcile_resume_completes`（崩溃后不重复提交） | 跳过，不重复修改 |
| PR-03 预处理失败不留任务 [P0] | 增加 `preparing`，输入/快照完成才转 `pending` 入队 | **已修（备料先行方案）** | `0213d88`：`routers/storyboard.py` `generate_shot` 重排——模板加载/参考图上传/构建 graph/非空预检全在 `create_job` 之前，失败 400 不落 job；测试 `test_upload_failure_leaves_no_job`、`test_empty_template_rejected_before_job_creation`、`test_upload_success_creates_job_with_snapshot` | 跳过。验收（400/连接失败/磁盘失败后重启无可执行残留）满足；未引入新状态值，旧库/API 全兼容，优于文档的 `preparing`/`failed_preflight` 新状态方案 |
| PR-04 统一受管素材路径 [P0] | 统一 `resolve_managed_asset`，验前缀/解码/根归属/符号链接/类型 | **已修** | `0213d88`：`app/managed_paths.py` 四道闸（段检查/盘符绝对拦截/realpath 边界/扩展名白名单），comfyui/export/storyboard/tracks/shotcheck/router 六处接入；测试 `test_managed_paths.py` 27 例（`../`、`%2e%2e%2f`、反斜杠、UNC、外链、符号链接逃逸均拒） | 跳过，不重复修改 |
| PR-05 重启恢复不确定状态 [P1] | 区分 confirmed_failed/not_found/temporarily_unavailable；退避重试；分页恢复 | **已修（六态方案）** | `f51f3d5`：probe 六态（`temporarily_unavailable ≠ lost`）、`needs_attention` 待核查态（镜头保持 generating、不重提不标败）、`_iter_jobs` 200/页分页恢复（替原 `limit=1000` 截断）、`POST /api/jobs/{id}/retry` 手动重试；测试 `test_probe_temporarily_unavailable_on_network_error`、`test_recover_comfy_unavailable_marks_needs_attention`、`test_recover_needs_attention_resumes_when_back`、`test_iter_jobs_paginates_beyond_one_page`（203 条）等 | 跳过。退避重试的落点是「下次启动恢复时再探 + 手动重试端点」——恢复器本就只在启动时运行，进程内退避无实际场景；验收（离线后上线仍正确关联、1000+ FIFO 恢复）满足 |
| PR-06 冻结工作流、取消隔离 [P1] | 快照存 schema_version + sha256，丢失/损坏标记需处理；取消不误伤其他任务 | **取消隔离已修；快照指纹缺失 → 本批补齐** | `f51f3d5`：HF-06 取消不调全局 `/interrupt`（`test_poll_cancel_does_not_global_interrupt`）、HF-05 损坏快照显式失败（`test_engine_corrupt_snapshot_fails_explicitly`）；**本批**：schema_version + sha256 写入与校验 | 本批实施，见 §2 |

## 2. 本批变更（PR-06 收尾：不可变快照）

### 2.1 设计

快照格式统一为：

```json
{"schema_version": "1.0", "job_id": "<job_id>", "graph": {...}, "sha256": "<graph 规范化 JSON 的 sha256>"}
```

- 写入方两处（router 入队、引擎兜底补写）统一走 `comfyui.build_snapshot_payload(graph, job_id)`；
- 读取方（引擎执行）统一走 `comfyui.verify_snapshot_payload(data, path)`；
- sha256 计算用 `json.dumps(graph, sort_keys=True, separators=(",",":"))` 规范化后哈希，跨进程/跨写入方一致；
- 校验失败（schema 版本不受支持 / 指纹不符 / 非 JSON 对象 / 无节点图）→ `ComfyUIError` 明确失败并提示人工处理，**绝不静默改用当前模板**；
- 旧快照（无 schema_version/sha256，2B 时期产物）→ 放行，校验降级为结构检查——用的仍是存档本身，不存在「替换」，满足「不破坏已有生成结果」约束。

### 2.2 变更清单（4 个生产文件 + 1 个测试文件）

| 文件 | 变更 |
|---|---|
| `backend/app/comfyui.py` | 新增 `import hashlib`；新增 `SNAPSHOT_SCHEMA_VERSION="1.0"`、`_graph_sha256`、`build_snapshot_payload`、`verify_snapshot_payload`（约 +90 行，含文档字符串） |
| `backend/app/engines/comfy.py` | `_resolve_workflow`：快照解析改走 `verify_snapshot_payload`（原内联的 graph 结构检查上移为统一校验，新增 schema/指纹检查与「非 JSON 对象」防护）；兜底补写改用 `build_snapshot_payload`；导入区同步；docstring 补 PR-06 说明 |
| `backend/app/routers/storyboard.py` | 入队快照写入改用 `build_snapshot_payload(graph, job_id)`；导入区加 `build_snapshot_payload`；注释补 PR-06 |
| `backend/tests/test_comfy_reliability.py` | 新增 PR-06 段 5 例（载荷往返/指纹不符/版本不受支持/旧快照放行/JSON 数组拒绝）；扩展 3 例既有断言（兜底补写、入队快照×2 现断言 schema_version+sha256） |

前端无改动（本批不涉及 UI）。

### 2.3 与文档方案的差异（如实记录）

1. **PR-01**：采用文档认可的迁移期方案（入口清零），未做 `GenerationResult` 全量重构——文档 §6 明确「禁止一次大规模重构」，且单 worker 串行架构下入口清零已满足验收；
2. **PR-03**：采用「备料先行、失败不建 job」，未引入 `preparing`/`failed_prefetch` 新状态——验收等价且旧库/API 零迁移；
3. **PR-05**：待核查态命名 `needs_attention`（非文档的 `reconcile_pending`），重试策略为「启动时再探 + 手动重试端点」（非进程内退避）；
4. **PR-06**：损坏/版本不符的快照 → 任务**明确失败**（带人工处理指引），而非打入 `needs_attention`。理由：`needs_attention` 的既有语义是「ComfyUI 暂不可达、远端任务可能仍在执行」；快照损坏是本地确定性错误，明确失败 + 修复后重新触发生成更直接，也避免待核查队列混入本地错误。两条路径都满足文档核心述求：「不自动替换、需人工处理」。

## 3. 测试日志

```
$ cd backend && env -u PYTHONPATH ./.venv/Scripts/python.exe -m pytest tests -q
135 passed, 1 skipped, 1 warning in 95.62s
```

- 基线 130 passed / 1 skipped（`f51f3d5` 全量）→ 本批 135 passed / 1 skipped：**旧测试全保留，新增 5 例全过**；
- 唯一 skip 为沙箱 `mklink` 拒绝访问的符号链接用例（`link.is_symlink()` 复核后诚实跳过，非 vacuous pass）；
- 本批未动前端，未重新执行 `npm run build`（改动不触及前端产物）。

**Windows 实际验收：未执行**——沙箱无真实 ComfyUI 实例与 GPU，快照校验只经过单元/接口级测试；electron-smoke 需真实 GUI，同样未执行。按改造.md §5 门禁要求如实标注。

## 4. 剩余风险与后续

1. 真实 ComfyUI 端到端（含快照校验在实机的表现）未测；
2. PR-07（工作流能力中心：execution_kind/model_id/required_nodes/supports 元数据、`/object_info` 探测、缺节点阻断）为下一批主体，是本阶段最大的新增功能；
3. PR-08（首尾帧/多参考数据模型与冻结入队）依赖 PR-07 的能力声明，建议接续 PR-07 之后；
4. hotfix 分支尚未合入 `origin/main`（851e21a）——PR-01~06 全部落在 hotfix 上，合并时机待确认；
5. GitHub 当前不可达，本批 commit 后的 push 若失败需重试。

## 5. 回滚方式

```bash
git revert <本批提交>        # 或 git reset --hard f51f3d5（未 push 时）
```

旧快照兼容逻辑保证回滚后历史任务仍可执行；DB 无本批迁移（未改表结构）。
