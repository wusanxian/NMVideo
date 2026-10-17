# 第二批 2B：ComfyUI 任务可靠性 — 实施报告

> 对应《下一阶段改造与优化方案.md》工作包 2B。
> 前置依赖 2A（ec77a38）、3A（807344c）已提交，本包全部建立在其上。
> 目标：ComfyUI 任务的进度推送、断点恢复、工作流存档、取消语义四类可靠性问题。

## 一、变更内容

### 1. 数据库 schema（`app/db.py`）

- `jobs.prompt_id TEXT DEFAULT ''`：ComfyUI prompt_id 持久化（重启恢复/对账的前提）
- `jobs.workflow_snapshot TEXT DEFAULT ''`：入队时固化的工作流快照路径
- 沿用 2A/3A 同一套 `ALTER TABLE` 增量迁移，旧库平滑升级

### 2. 客户端可靠性（`app/comfyui.py`）

- **client_id 一致**：新增 `ComfyUIClient.new_client_id()`，提交与 WS 订阅共用同一 id。
  修复旧 bug：提交用默认 `"aivideo"`、WS 却按 prompt_id 派生 id 订阅——ComfyUI 的
  WS 只把进度推给提交时携带同一 client_id 的连接，旧代码下一条进度都收不到
- **WS 静默期对账**：`_wait_ws` 循环内每 5s 查一次 history——任务可能在 WS 连接
  建立前就已完成、或推送丢失，完成检测不依赖收到消息
- **WS 失败降级轮询**：连接失败（`connect()` 抛异常）、`websockets` 未安装、
  ImportError 统一转 `ComfyUIError`，由 `wait_for_completion` 捕获后降级
  `_wait_poll` 历史轮询（带平滑假进度，前端不空转）
- **v14+ 兼容**：`import websockets` 不再暴露 `websockets.sync`，显式
  `import websockets.sync.client`；v17 `recv(timeout)` 超时抛内置 `TimeoutError`
- **取消语义**：新增 `ComfyUICancelled(ComfyUIError)`；取消时先 `interrupt` 再抛，
  引擎层转 `GenerationCancelled` 走取消路径（job 标 cancelled、镜头回 draft）
- **`probe(prompt_id)`**：五态判定 done/running/queued/error/lost（history 优先，
  再查 queue_running/queue_pending，异常保守按 lost）
- **`build_shot_workflow(params)`**：从引擎层抽到模块级，入队（router）与执行
  （引擎兜底）共用同一构建逻辑

### 3. 引擎层（`app/engines/base.py` / `anime.py` / `wan.py` / `comfy.py`）

- 抽象 `generate` 增 `resume_prompt_id: str = ""` / `workflow_snapshot: str = ""`，
  本地引擎（anime/wan）忽略，签名向后兼容
- `ComfyEngine`：
  - resume 路径（`resume_prompt_id` 非空）**跳过提交**，`client_id=""` 直接轮询
    （原 client_id 已随上次进程丢失，新 id 订阅不到旧任务推送）
  - `_wait()` 把 `ComfyUICancelled` 转 `GenerationCancelled`
  - `_resolve_workflow()`：优先读入队快照；无快照/快照损坏才现场读模板并**补写
    快照**（每个 ComfyUI 任务都有独立工作流存档）
  - `self.last_prompt_id`：最近一次提交的 prompt_id，worker 负责落库

### 4. 队列层（`app/queue_manager.py`）

- 新增 `enqueue(job_id)`：router「先建 job → 落快照 → 再入队」用
- `_run_job` 透传 `resume_prompt_id` / `workflow_snapshot`；生成成功后把
  `engine.last_prompt_id` 持久化到 job（重启恢复与对账的前提）
- `_recover_stale_jobs`（崩溃恢复从一刀切改为分级）：
  - running + comfyui + 有 prompt_id → 先 `probe()`：
    - done/running/queued → 重新入队走 resume，镜头保持 `generating`
    - error → 明确失败「重启前已执行失败」，镜头 rework
    - lost → 标记中断，镜头 rework
  - 本地引擎（无 prompt_id）→ 一律标记中断（无断点续算），镜头 rework
  - pending → 仍按 FIFO 重新入队（3A 行为不变）

### 5. 接口层（`app/routers/storyboard.py`）

- `generate_shot(engine=comfyui)`：`create_job` → `build_shot_workflow` 构建并写
  快照 `outputs/workflows/{job_id}.json` → `update_job(workflow_snapshot=...)` →
  `job_queue.enqueue(job_id)`。**入队即固化**：排队期间用户在设置里改模板不影响
  已排队任务；快照写失败不阻断（执行时兜底补写）

### 6. 测试

- 新增 `tests/test_comfy_reliability.py`（18 用例）：client_id 一致、WS URL 校验、
  静默期历史对账、WS 失败降级、resume 直连轮询、probe 四态、取消 interrupt、
  引擎层 client_id 一致/取消转换/快照优先/兜底补写/参数注入、队列层 prompt_id
  持久化/resume 跳过提交/取消标 cancelled、重启恢复四场景、接口层快照固化
- `tests/conftest.py`：`client` fixture（TestClient，不进 lifespan）从
  test_http_protection_api.py 上提为共享 fixture

## 二、测试结果（全部真实执行）

```
backend/.venv/Scripts/python.exe -m pytest tests/     # env -u PYTHONPATH
======================= 61 passed, 1 warning in 59.51s ======================
```

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| 2A 既有（test_db_concurrency 等） | 13 | 并发/事务/旧库审计（ec77a38） |
| 3A 数据层 test_protection_gates.py | 24 | 审核三态/引用保护/导入保护（807344c） |
| 3A 接口 test_http_protection_api.py | 6 | 409/422/400 契约 |
| **2B test_comfy_reliability.py（新增）** | **18** | 见上 |

导入冒烟通过（app.main / queue_manager / comfyui 均正常 import）。

## 三、实施中修复的 bug（测试抓出）

1. **`import websockets` 不暴露 `websockets.sync`**（v14+ 起）：AttributeError
   且无兜底 → 显式 `import websockets.sync.client`，ImportError 转 ComfyUIError 降级
2. **submit 与 WS client_id 不一致**（既有 bug）：进度推送一条收不到 →
   `new_client_id()` 两边共用同一 id
3. **`websockets.sync.client.TimeoutError` 不存在**（v17）：`recv(timeout)`
   超时抛内置 `TimeoutError` → except 子句改用内置异常
4. **WS 连接失败不被兜底捕获**：`connect()` 本身抛原生
   ConnectionRefusedError，外层只捕 `ComfyUIError` → 降级失效、测试直接崩 →
   connect 包 try 转 `ComfyUIError`
5. **测试基建两处**：`fake_engine_env` 的假 `frames_to_video` 用了未导入的
   `Path`（NameError → job 误标「视频合成失败」）；`client` fixture 跨文件不可见
   → 顶层补导入 + fixture 上提 conftest

## 四、剩余风险 / 后续

- **恢复执行假设 ComfyUI 未重启**：ComfyUI 若也重启过，history/queue 清空 →
  probe=lost → 标中断让用户重试。保守策略（不无限等待可能已不存在的任务），
  但存在「任务其实已完成却被判 lost」的误伤窗口；更精确可在 job 表记
  ComfyUI 启动时间戳，下批可选优化
- resume 后下载依赖 history 条目里的 outputs；ComfyUI 若配置了定期清理
  history，长任务恢复可能拿不到产物（vanilla 默认不清理，风险低）
- 降级轮询的假进度是平滑估值，不是真实采样进度（WS 不可用时的展示降级）
- i2v 参考图经 ComfyUI input 目录上传后注入属于 2C（inject 已预留位置）
- 与真实 ComfyUI 服务器的联调（提交→推送→下载全链路）排在 Windows
  Electron 全链路验证时统一做

## 五、回滚点

- 本包开始前提交：807344c（3A）
- 本包提交：见 git log（`phase2b` 系列）
