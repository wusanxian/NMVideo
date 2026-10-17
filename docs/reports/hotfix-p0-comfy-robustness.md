# Hotfix 0：ComfyUI 任务可靠性与媒体路径安全（P0-01/02/03）

- 分支：`hotfix/p0-comfy-robustness`（基于 main `851e21a`，不覆盖 main）
- 基线：`git rev-parse HEAD` = `851e21a4877f96a66e9b275c90571d9a2e4dc422`
- 依据：`改造.md` §4 Hotfix 0（用户截图 + 文档指出的三个 P0 缺陷）
- 范围：本批只修这三个 P0，不开发新 UI、不做大规模重构

## 1. 变更文件清单

### 新增
| 文件 | 作用 |
|---|---|
| `backend/app/managed_paths.py` | P0-03 核心：统一受管媒体路径解析 `resolve_managed_asset()` |
| `backend/tests/test_managed_paths.py` | P0-03 回归：27 例（越界/编码穿越/盘符/反斜杠/符号链接/白名单/上传/导出接入） |

### 修改
| 文件 | P0 | 改动 |
|---|---|---|
| `backend/app/engines/base.py` | 01 | 引擎契约新增 `on_submitted(prompt_id, client_id)` 回调与 `resume_client_id` 参数 |
| `backend/app/engines/comfy.py` | 01 | `/prompt` 返回即触发 `on_submitted` 落库（不等 `_wait`）；resume 路径带 `resume_client_id` 续 WS |
| `backend/app/engines/anime.py` / `wan.py` | 01 | 签名同步加两参数并忽略（队列统一传参不炸本地引擎） |
| `backend/app/queue_manager.py` | 01 | `_run_job` 定义 `on_submitted` 闭包（线程安全写库：prompt_id + client_id + stage）；resume 读 `client_id`；generate 后补写降级为兜底 |
| `backend/app/db.py` | 01 | jobs 表轻量迁移新增 `client_id TEXT DEFAULT ''`（`PRAGMA table_info` 检查后 `ALTER TABLE`，旧库平滑升级） |
| `backend/app/routers/storyboard.py` | 02/03 | `generate_shot` 重排：模板加载→参考图上传→构建 graph→非空预检全部在 `create_job` **之前**，任一失败 400 且不落 job；续接取帧改安全解析 |
| `backend/app/comfyui.py` | 03 | `local_path_from_output_url` 改走 `resolve_managed_asset`（显式传本模块 `OUTPUTS_DIR` 绑定，保持旧 patch 语义） |
| `backend/app/export.py` | 03 | `_url_to_path`/`_exists` 改走安全解析，返回 `Optional[Path]`；`run_export` 缺失/不受管抛 `FileNotFoundError`；`_mix_audio` 两处 append 前判空 |
| `backend/app/storyboard.py` | 03 | `_video_path_ok`、`timeline_layout` 改走安全解析（默认根运行时读 `app.config`，兼容测试 patch） |
| `backend/app/tracks.py` | 03 | `delete_voice`/`delete_audio_track` 改走安全解析，判空后 unlink |
| `backend/app/shotcheck.py` | 03 | 视频存在检查改安全解析，失败报 `video_missing`（message 加「或不受管」） |
| `backend/tests/test_comfy_reliability.py` | 01/02 | 新增 8 例：on_submitted 先于 wait、wait 抛异常也已完成落库、回调失败不阻断、队列级「提交后等待期崩溃」job 行有 prompt_id+client_id、重启对账全流程（running→重新入队→resume 不重复提交→wait 带 client_id→done 归档）、上传失败 400 不留 job、空模板预检 400、成功路径 201+快照+恰好一个可执行任务 |

## 2. 修改前后差异（按 P0）

### P0-01 prompt_id 持久化时间点
- **前**：`ComfyEngine.generate` 先 `_wait()` 才设 `last_prompt_id`；`queue_manager` 在 `generate()` 返回后才写库。ComfyUI 已受理但本服务在等待期间崩溃 → DB 无 prompt_id，重启恢复把任务误标「中断失败」或对不上账。
- **后**：`/prompt` HTTP 返回后**立即**经 `on_submitted(prompt_id, client_id)` 交队列落库（worker 线程内调用，db 有锁）；`client_id` 同列落库。重启时 `_recover_stale_jobs` 用持久化 prompt_id 探 `/history`+`/queue`：done/running/queued → 重新入队走 resume（跳过提交）；error/lost → 标失败。resume 的 `wait` 带上持久化 client_id 可续 WS 进度，为空（旧任务）走轮询（兼容旧行为）。
- **回调本身失败不阻断生成**：该任务重启时按 lost 标失败，由用户重试——比静默丢账可诊断。

### P0-02 上传失败仍创建 pending 任务
- **前**：`generate_shot` 先 `create_job()` 再上传参考图；`upload_reference_image` 失败抛 `HTTPException(400)` 不回滚 job → 留下无 prompt_id 的 pending 僵尸任务，重启后还被恢复器捡起来重跑。
- **后**：顺序重排为「可用性检查 → 参数组装 → **模板加载 + 参考图上传 + 构建 graph + graph 非空预检** → `create_job` → 写快照（本就非阻断）→ 入队」。上传失败/模板为空在 400 时 DB 不落任何可执行任务。选「先备料后建 job」而非 preparing 中间态：graph 可内存中构建，无需新状态值，旧数据与 API 完全兼容。

### P0-03 输出路径拼接未阻止目录越界
- **前**：`comfyui.py`/`export.py` 等 6 处直接 `OUTPUTS_DIR / url[len("/outputs/"):]`；`/outputs/../x`、`/outputs/C:/x`（Windows 盘符片段替换 base）、反斜杠、`%2e%2e%2f`、符号链接均可逃逸。
- **后**：统一 `resolve_managed_asset(url, allowed_types, base_dir=None)` 四道闸：① 段检查（拒 `%`、反斜杠、控制字符/空白、`.`/`..` 段，只认 `/outputs/` 前缀）；② 盘符/绝对路径拦截；③ `resolve(strict=False)` realpath 后 `is_relative_to(base)` 边界（符号链接逃逸在此被拒）；④ 扩展名白名单（图片/视频/音频按调用方传入）。默认根**调用时**动态读 `app.config.OUTPUTS_DIR`（旧代码多为函数内 import，测试 monkeypatch config 隔离目录）；`app.comfyui`/`app.export` 两个历史上用模块级冻结绑定的调用点显式传 `base_dir`，patch 语义不变。
- **兼容性**：解析失败一律返回 `None`，由调用方决定 400 / 跳过 / 标缺失；不改旧 URL 格式、不改 API。

## 3. 测试结果（全部真实执行）

- 基线核对：`git worktree` 检出 `851e21a` 原样跑全量 → **81 passed, 0 failed**（确认 12 个中间态失败均为本批引入，非历史红）。
- 本批全量：`cd backend && env -u PYTHONPATH .venv/Scripts/python.exe -m pytest tests/ -q`
  → **115 passed, 1 skipped, 0 failed**（= 81 旧测试全保留 + 34 新增；无删除、无降级）。
- 跳过项说明：`test_symlink_escape` 在 WorkBuddy 沙箱内 `mklink` 被拒（拒绝访问），符号链接未真实创建 → `pytest.skip`，**不用 vacuous pass 冒充通过**；realpath 边界逻辑本身由单测覆盖，真机/CI 上该例会真实执行。
- 关键回归用例（改造.md 点名）：
  - 「提交完成、等待阻塞期间杀进程」→ `test_engine_on_submitted_fires_even_if_wait_raises` + `test_queue_prompt_id_persisted_before_wait_completes`：wait 抛异常时 submitted 已落库，job 行有 `prompt_id`+`client_id`。
  - 「重启不重复提交并完成归档」→ `test_restart_reconcile_resume_completes`：probe=running → 重新入队 → resume 不调 submit → wait 带持久化 client_id → done + video_url。
  - 「失败后不能出现待执行任务」→ `test_upload_failure_leaves_no_job`：400 且 job 计数不变、无 pending/running、分镜不进 queued。
  - 「目录穿越与符号链接」→ `test_managed_paths.py` 27 例。
- 中间态修复记录：首轮全量曾 12 failed——`resolve_managed_asset` 默认根在 import 时绑定，测试 patch `app.config.OUTPUTS_DIR` 失效（旧代码是函数内 import）。改为调用时动态读 config + 冻结绑定调用点显式传 `base_dir` 后恢复全绿（见 §2 P0-03「兼容性」）。

## 4. 剩余风险与未执行项

1. **符号链接逃逸未在沙箱实测**：mklink 被拒，该用例为 skip。逻辑由 realpath 边界单测覆盖，但建议真机补跑一次 `tests/test_managed_paths.py::TestRejected::test_symlink_escape`。
2. **真实 ComfyUI 端到端未测**：本批全部用 Mock 客户端/假 HTTP，未连真实 ComfyUI 服务器；「崩溃恢复对账」的真实 WS 行为需显式批准后实测。
3. **前端未动**：本批按 §8 不开发新 UI；前端构建与 Electron 测试不在本批范围（上一批 18/18 通过的结果属历史记录）。
4. **`改造.md` 未纳入本 commit**：该文件是用户正在编辑的需求文档，按约定不混入代码提交。
5. **旧任务恢复语义**：重启恢复只对 `engine=comfyui` 且 `prompt_id` 非空生效；P0-01 之前崩溃的任务（有 prompt_id 无 client_id）恢复后走轮询（兼容路径，已有单测覆盖）。

## 5. 回滚方式

```bash
git checkout main            # 或直接删除 hotfix/p0-comfy-robustness 分支
```

本批为纯增量：db 迁移只加列（`client_id`），无删表/改列；API 无破坏性变更；旧库打开即自动迁移。
