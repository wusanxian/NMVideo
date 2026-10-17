# v0.2.1 Hotfix 续批：HF-00 / HF-04 / HF-05 / HF-06

- 分支：`hotfix/p0-comfy-robustness`（基于 `0ca645a`，不覆盖 main）
- 依据：`改造.md` §2 风险清单 + §8「优先完成 HF-00~HF-04，独立提交」
- 本批范围：只修这四个缺陷 + 最小前端联动（任务队列页新状态展示）；不开发新功能页

## 1. HF-00~HF-06 审查结论（§8 第一步）

| 编号 | 级别 | 现状（HEAD 0ca645a） | 证据 / 处置 |
|---|---|---|---|
| HF-00 原生视频串片 | P0 | **仍存在** | `engines/comfy.py` `last_native_video` 仅构造时初始化，`generate()` 不清零；引擎实例被 `queue_manager._engine_instances` 跨任务缓存 → A(MP4)/B(图片) 时 B 归档 A 的视频。本批修复 |
| HF-01 prompt_id 保存太晚 | P0 | **已修**（`0213d88`） | on_submitted 提交即落库；测试 `test_engine_on_submitted_fires_even_if_wait_raises`、`test_queue_prompt_id_persisted_before_wait_completes`、`test_restart_reconcile_resume_completes` |
| HF-02 上传失败遗留 pending | P0 | **已修**（`0213d88`） | 备料先行；测试 `test_upload_failure_leaves_no_job` 等 |
| HF-03 路径越界 | P0 | **已修**（`0213d88`） | `managed_paths.resolve_managed_asset` 四道闸；`test_managed_paths.py` 27 例 |
| HF-04 断线误判不可恢复 | P0 | **仍存在** | `probe()` 网络异常按 `lost` → 误标 failed；恢复 `limit=1000` 截断。本批修复 |
| HF-05 快照损坏静默换模板 | P1 | **仍存在** | `_resolve_workflow` 损坏时 `pass` 退模板。本批修复 |
| HF-06 取消全局打断 | P1 | **仍存在** | 取消路径调全局 `/interrupt`。本批修复 |

## 2. 变更文件清单

### 修改
| 文件 | HF | 改动 |
|---|---|---|
| `backend/app/engines/comfy.py` | 00/05 | `generate()` 入口清零 `last_prompt_id`/`last_native_video`；`_resolve_workflow` 快照存在但损坏/无节点图 → 抛 `ComfyUIError` 明确失败（仅快照文件不存在才走模板兜底并补写） |
| `backend/app/comfyui.py` | 04/06 | `probe()` 网络异常返回 `temporarily_unavailable`（≠lost）；`get_queue` 改显式状态码检查（不再依赖 `raise_for_status`，500 也判暂不可达）；`_wait_ws`/`_wait_poll` 取消**不再调全局 `/interrupt`**，仅本地标记取消并提示「远端任务可能仍在执行」；`interrupt()` 文档化注明误伤风险、仅供管理用途 |
| `backend/app/queue_manager.py` | 04/06 | `_recover_stale_jobs` 重写：分页拉取（`_iter_jobs`，`_RECOVER_PAGE=200`，原 `limit=1000` 截断）；running+暂不可达 → `needs_attention` 待核查（不重新提交、不标失败、镜头保持 generating）；新增 needs_attention 再探循环（恢复→入队 resume；仍不可达→继续待核查；确认 lost/error→标失败）；`_fail_job` 统一失败标记；`cancel()` 支持取消待核查任务并清理镜头联动；`GenerationCancelled` 的 ComfyUI 备注改为「已取消（ComfyUI 任务可能仍在远端执行）」 |
| `backend/app/db.py` | 04 | `list_jobs` 加 `offset` 参数（分页） |
| `backend/app/routers/jobs.py` | 04 | 新增 `POST /api/jobs/{id}/retry`：needs_attention 任务手动重试（重新入队走 resume），其他状态 409 |
| `backend/tests/test_comfy_reliability.py` | 00/04/05/06 | 新增 15 例（见 §4）；`test_poll_cancel_interrupts_once` → `test_poll_cancel_does_not_global_interrupt`（断言随 HF-06 需求反转：取消**不**触发 interrupt）；`_patch_http` 桩补 `raise_for_status` |
| `frontend/src/views/QueueView.vue` | 04 | 新增 `needs_attention` 状态标签「待核查」+ 重试/取消按钮 |
| `frontend/src/api.js` / `store/jobs.js` / `style.css` | 04 | `retryJob` API + store action + 待核查徽标样式 |

## 3. 修改前后差异（按 HF）

### HF-00 原生视频串片（P0）
- **前**：`last_native_video` 只在构造与「本轮产出视频」时赋值；队列缓存引擎实例 → B 继承 A 的视频被优先归档（复现：`second_last_native_video_still_set=True, same_as_old=True`）。
- **后**：`generate()` 第一行清零两个 `last_*`。失败/取消路径同样不留旧值。worker 读取逻辑不变（读本轮 generate 之后的引擎状态）。
- **验收对照**：A=MP4、B=图片序列 → B 成片来自 B 的帧（队列级回归断言字节不同）；三轮交替 + 失败后均不继承（引擎级回归）。
- 文档提及的长期方案（`GenerationResult` 返回值重构）不在本批——避免大规模重构，按「最小修复」落地。

### HF-04 断线误判（P0）
- **前**：`probe()` 任何异常（含断网）→ `lost` → running 任务标 failed + 镜头打回 rework；恢复扫描 `limit=1000` 截断。
- **后**：`probe()` 六态：`done/running/queued/error/lost/temporarily_unavailable`。断网 → 待核查：job 标 `needs_attention`（stage「待核查：ComfyUI 暂不可达」+ error 说明），不提交不失败，镜头保持 generating；下次重启自动再探，用户也可点「重试」（新端点）立即 resume；只有 history/队列明确应答「没有」才 lost → 标失败。pending/running/needs_attention 扫描全部走 `_iter_jobs` 分页（200/页），>1000 完整恢复。
- **验收对照**：断网重启 → 不误标失败、镜头不回退（`test_recover_comfy_unavailable_marks_needs_attention`）；恢复后再重启 → 重新入队 resume（`test_recover_needs_attention_resumes_when_back`）；仍不可达 → 继续待核查（`..._stays_when_still_unavailable`）；确认丢失 → 标失败（`..._confirmed_lost_fails`）；分页不截断（`test_iter_jobs_paginates_beyond_one_page`、`test_recover_reenqueues_beyond_one_page`）。

### HF-05 快照损坏静默换模板（P1）
- **前**：快照 JSON 损坏 → `pass` → 静默读当前模板，老任务重试换了工作流，输出不可复现。
- **后**：快照**文件存在**但损坏/不可读/无有效节点图 → `ComfyUIError`（job 明确失败，错误信息指引人工处理）；仅快照**文件不存在**（老任务/被清理）才走模板兜底并补写快照。
- **验收对照**：`test_engine_corrupt_snapshot_fails_explicitly`、`test_engine_snapshot_without_graph_fails`；原 `test_engine_fallback_writes_snapshot`（快照缺失走兜底）保持不变仍通过。

### HF-06 取消全局打断（P1）
- **前**：取消 → 调 ComfyUI 全局 `/interrupt`，共享服务器误伤其他客户端任务。
- **后**：取消路径不再调用 interrupt；job 标 cancelled + 镜头回 draft + 备注「已取消（ComfyUI 任务可能仍在远端执行）」；`interrupt()` 保留但文档化「仅管理用途，默认不触发」。
- **验收对照**：`test_poll_cancel_does_not_global_interrupt`（断言不调 interrupt 且抛 ComfyUICancelled）。

## 4. 测试结果（全部真实执行）

- 全量：`cd backend && env -u PYTHONPATH .venv/Scripts/python.exe -m pytest tests/ -q`
  → **130 passed, 1 skipped, 0 failed**（上批 115 passed 的基础上 +15 新增；81 个历史旧测试全保留，无删除无降级）。
- 跳过项：`test_symlink_escape`（沙箱 mklink 拒绝访问，符号链接未真实创建 → skip，不用 vacuous pass 冒充）。
- 前端：`npm run build` ✓  built in 3.93s。
- 桌面：`node --test tests/shell-ui.test.js` → 12 pass 0 fail；`run-electron-smoke.js` 需真实 Electron GUI + 隔离后端，沙箱不可跑（与上批一致，标注未执行）。
- 新增 15 例分布：HF-00 ×3（引擎交替、失败不残留、队列级串片回归）、HF-04 ×9（probe 暂不可达、probe lost 仅在可达时、待核查三态、分页两例、重试端点两例、+`_patch_http` 桩修正）、HF-05 ×2、HF-06 ×1（改写断言）。
- 中间态修复记录：首轮 3 例失败——① `test_probe_states`/新 lost 例的假响应缺 `raise_for_status`，`get_queue` 抛 AttributeError 被新 probe 判暂不可达 → 将 `get_queue` 改为显式状态码检查（与 `get_history` 一致，生产侧也更稳：500 同样判暂不可达而非 lost）；② 待核查测试未模拟「崩溃前镜头已 generating」→ 补 `set_shot_status`。

## 5. 剩余风险与未执行项

1. **真实 ComfyUI 端到端未测**：断网 30 秒再恢复的真实场景（改造.md 验收原文）需真实 ComfyUI 服务，本批以 Mock 覆盖状态机；真机建议按 §6 五镜头成片流程走一遍。
2. **`needs_attention` 为新增 job 状态值**：jobs 表无 CHECK 约束无需迁移；前端已加标签与重试按钮，但画廊/其他视图如有按状态过滤的逻辑需回归（本次构建通过，未做 GUI 回归）。
3. **HF-00 采用最小修复（入口清零）**：文档长期方案 `GenerationResult` 返回值重构未做（避免大规模重构）；若未来引擎实例并发复用（多 worker），需回到该方案。
4. **真实 Electron 冒烟未执行**（沙箱限制）；`改造.md` 继续不纳入代码 commit。
5. **HF-05 行为变化提示**：快照损坏从「静默换模板」变为「明确失败」——极少数历史任务（快照损坏且用户不知道）重试会失败并提示人工处理，这是有意为之（可复现性优先）。

## 6. 回滚方式

```bash
git checkout main            # 或删除 hotfix/p0-comfy-robustness 分支
```

纯增量：db 仅加 `list_jobs(offset=)` 参数（兼容旧调用）；新端点、新状态值均为加法；API 无破坏性变更。
