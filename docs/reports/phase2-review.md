# 第二阶段源码审查报告与文件清单

日期：2026-10-17。审查基线：分支 `feature/nmvideo-freevideo-phase1`，HEAD `63c5b5a`（含此前未入库的 `docs/下一阶段改造与优化方案.md`）。回滚点：annotated tag `phase2-start`。

审查方式：通读目标源码 + 对照《下一阶段改造与优化方案》工作包编号（2A/3A/2B/2C）与用户本批四项子任务。**本文只记录审查结论，不含实现**。

## 1. 四项子任务逐项核对

### 1.1 Electron 整合

| 要求 | 现状 | 证据 |
|---|---|---|
| 审查 main.js / preload.js / renderer / App.vue | 已通读 | 见下 |
| 保留双端（桌面 + 浏览器） | **已做到** | webview 加载 `http://127.0.0.1:<port>`，前端可独立 `vite dev`/静态托管 |
| 两套侧边栏重复 | **未解决** | `desktop/renderer/index.html:30-55` 系统侧边栏（启动/安装/正在处理/ComfyUI/终端/设置）与 `frontend/src/App.vue:1-33` 创作侧边栏（作品/剧本/资产/生成/剪辑/声音/设置）并存，「设置」语义重叠 |
| 默认进入创作工作台 | **未做到** | `renderer/app.js:82` `view:'boot'` 启动默认停启动台，需手点「打开应用」 |
| 安装/服务管理/终端/更新 → 可展开系统面板 | **未做到** | 侧边栏点击后 `setView('boot')` 整页切回启动台（`app.js:318-324`），离开应用视图 |
| 返回启动台不丢工作台状态 | **已做到** | `app.js:224-230` 不销毁 webview、`loadedOrigin` 防重置；`rememberRoute` 持久化路由到 localStorage |
| WebContentsView 替换 webview 风险评估 | **未评估** | 需出结论（本报告 §3） |
| no-sandbox 与 webview 导航安全 | **已加固，需记录** | 全局无 `--no-sandbox`/`sandbox:false` 残留（grep 验证）；`main.js:124-147` 主窗 `sandbox:true`、webview 白名单（`will-attach-webview` 校验 src+partition、强制 sandbox/webSecurity）、`guardContents` 拦 will-navigate/redirect、setWindowOpenHandler 外链对话框确认；`security.js` 提供 isAppURL/isShellURL/isTrustedSender |

### 1.2 数据库安全

| 要求 | 现状 | 证据 |
|---|---|---|
| 先备份再修复 | **未做** | 无备份/恢复入口 |
| 修复失效外键与孤立记录 | **部分** | `db.py:262 _enable_foreign_keys()` 启动即 `PRAGMA foreign_keys=ON`，但先静默 DELETE 孤儿行——正是文档 D2 点名的破坏性行为，需改为只读审计 + 显式修复 |
| 逐步开启外键 | **已开启** | 同上；`_FK_TABLES` 清单完整（10 条父子关系） |
| 导入前展示改动预览 | **已做到** | `storyboard.py:535 script_preview()` + 路由 preview 分支 + 前端确认弹窗（上批 cb5b655） |
| 不允许无提示覆盖已生成镜头 | **部分** | rebuild 有前端预览确认，但**预览不绑定 revision/内容哈希**（文档 S3）：预览后项目被改动再提交，服务端无法识别过期 |
| 旧版本恢复和事务回滚 | **部分** | import_script 已单事务回滚；无数据库级备份/恢复 |
| 访问收口与事务边界（2A） | **未做** | `db.py:33` 共享连接；`storyboard.py:22-36` 的 `_row/_rows/_write` 直接摸 `db._conn`，读无锁；`assets.py`、`tracks.py` 同 |

### 1.3 分镜审核

| 要求 | 现状 | 证据 |
|---|---|---|
| 检查先于通过 | **已做到** | `routers/storyboard.py:401-417` QA 前置，error 级阻断 422 + approve_blocked 留痕（上批 cb5b655） |
| 版本归属 | **已做到** | adopt/delete/review 三处 `version["shot_id"] != shot_id` 强校验 |
| 视频文件缺失不能通过 | **已做到** | `shotcheck.py:70-81` no_video/video_missing error 级 |
| 分离设计/生成/视频审核三种状态 | **未做** | 状态机仅 `draft→queued→generating→review→approved/rework`，单一 review 态混合了三种审核语义 |
| 失败显示明确原因 | **已做到** | ReviewDialog 展示 issues/score，阻断时重拉质检 |
| **采用版本时 QA 阻断后指针已改（S1）** | **缺陷** | `routers/storyboard.py:397` 先 `adopt_version` 后 `check_shot`——阻断时 `current_version_id` 已被改写，违反「QA 失败状态和指针均不变」 |
| 删除引用保护（S2） | **缺陷** | `routers/jobs.py:87 delete_job()` 直接删视频文件；`storyboard.py:415 delete_version()` 删当前版本后自动切指针；时间线从 `shots.current_version_id` 动态取视频（`storyboard.py:648-661`），换采用版本即改变剪辑结果 |

### 1.4 ComfyUI

| 要求 | 现状 | 证据 |
|---|---|---|
| 提交与 WS 共用同一 client_id | **缺陷（J1）** | `comfyui.py:70` submit 默认 `client_id="aivideo"`；`:117` `_wait_ws` 另造 `aivideo-{prompt_id[:8]}`——实时消息可能收不到本任务进度 |
| 持久化 prompt_id | **缺陷（J3）** | `engines/comfy.py:102` 提交后 prompt_id 只活在内存 |
| WS 无消息时定期查历史 | **缺陷（J2）** | `_wait_ws` 的 `websockets.TimeoutError` 只 `continue`；history 检查在收到消息之后；只有 `ComfyUIError` 才转轮询，WS 静默/断连/缺依赖不保证触发对账 |
| i2v 参考图真实上传 input 目录 | **缺陷（I1）** | `engines/comfy.py:97-98` 把 `/outputs/...` 直接注入 LoadImage——NMVideo 的输出目录不是 ComfyUI 输入目录，节点读不到 |
| 视频保留原始产物免重编码 | **缺陷（O1）** | `engines/comfy.py:122-130` 下载视频→ffmpeg 抽帧→`queue_manager.py:191` 再合成 MP4，重复编码且有丢音轨风险 |
| 每任务存独立工作流快照 | **缺陷（J4）** | `engines/comfy.py:83` 执行时才 `load_workflow_template()`，排队期间改模板会改变任务含义 |
| 重启后先查 ComfyUI 历史再决定续/完成/失败 | **缺陷（J3）** | `queue_manager.py:34 _recover_stale_jobs()` 一律标失败，远端可能已完成 |

## 2. 与文档工作包的映射与实施顺序

用户四项子任务 ↔ 文档工作包：

| 用户子任务 | 文档工作包 | 本批范围 |
|---|---|---|
| 2 数据库安全 | 2A + 3A(部分) | db 收口、启动审计化、备份/恢复、导入 revision |
| 3 分镜审核 | 3A | 采用指针原子化、删除引用保护、三态分离 |
| 4 ComfyUI | 2B + 2C | client_id/prompt_id/对账/快照/恢复；上传/原样归档 |
| 1 Electron | 第一阶段发布收尾 | 面板化、默认工作台、侧边栏去重、安全核查记录 |

按文档§1 结论：**2A 是任务恢复、审核原子性和资产保护的共同前提，排第一**；随后 3A（数据保护前置，因为删除与审核路径仍可能破坏成果）→ 2B → 2C；Electron 整合与前四者无代码耦合，排最后但同批交付。这与用户子任务编号顺序不同，但符合文档「先保护数据正确性」的排序原则，且 Electron 改动集中在 desktop/ 与 renderer，独立 commit 不影响回滚粒度。

## 3. WebContentsView 迁移风险评估（结论：本批不迁移）

- **收益**：webview 标签已 deprecated（Electron 文档明确不建议新代码使用）；WebContentsView 无额外文档加载、权限模型更清晰、可与 BrowserWindow 更细粒度组合。
- **风险**：① `partition="persist:aivideo"` 的 session/存储迁移等价物需重做；② 现有 `will-attach-webview`/`did-attach-webview` 安全模型要改写为 `setWebBrowserView` + `webContents` 事件；③ app-bar「返回启动台」的视图切换逻辑、did-finish-load/did-fail-load 事件桥全部要重接；④ 桌面冒烟测试（`desktop/tests/electron-smoke.js`）需重写；⑤ 本批无真实 GUI 验证环境（沙箱限制），迁移后无法端到端验收。
- **结论**：保留 webview，把迁移列入 3B/发布收尾候选；当前 webview 安全配置已满足要求（§1.1 末行）。

## 4. 待修改文件清单

| 工作包 | 修改 | 新增 |
|---|---|---|
| 2A | `backend/app/db.py`、`storyboard.py`、`assets.py`、`tracks.py`、`main.py`(启动钩子，若需要) | `backend/tests/test_db_concurrency.py`、`test_transactions.py`、`test_legacy_db_audit.py`、`docs/reports/phase2a.md` |
| 3A | `backend/app/storyboard.py`、`routers/storyboard.py`、`routers/jobs.py`、`shotcheck.py`、`frontend/src/api.js`、`frontend/src/store/project.js`、`frontend/src/views/ProjectView.vue` | `backend/tests/test_protection_gates.py`、`docs/reports/phase3a.md` |
| 2B | `backend/app/comfyui.py`、`engines/comfy.py`、`engines/base.py`、`queue_manager.py`、`db.py`(jobs 新列)、`routers/storyboard.py`(generate_shot 快照)、`routers/jobs.py` | `backend/tests/test_comfy_reliability.py`、`docs/reports/phase2b.md` |
| 2C | `backend/app/comfyui.py`(upload)、`engines/comfy.py`(原样归档)、`queue_manager.py`(归档分支)、`video.py`(探测缓存，若需要) | `backend/tests/test_comfy_io.py`、`docs/reports/phase2c.md` |
| Electron | `desktop/renderer/index.html`、`renderer/app.js`、`renderer/styles.css`、`preload.js`(如需新 IPC)、`main.js`(如需新 IPC) | `docs/reports/phase2-electron.md` |

## 5. 约束遵守承诺

- 不删模块；旧表只加列不删列；旧 status 字符串保持兼容投影（2B 新 phase 通过新增字段表达，不改旧 status 语义）。
- 每个工作包独立 commit + 报告；测试必须真实执行，未执行的项明确标注「未执行」。
- 本批不做 AI 导演（文档 6）。
- 数据库迁移前对隔离库先备份；恢复演练对比表结构/行数/关键文件。
