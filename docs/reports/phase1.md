# 批次 1 实施与验收（2026-10-16）

本次仅执行改造.md 的批次 0、1。分支 `feature/nmvideo-freevideo-phase1`，基于本地 main `cb5b655`，保留其比远端 main 多出的安全修复。批次 0 独立提交为 `359f7be`。未推送远端，未修改 main 指针。

## 已实现

- 工作台仅显示前端主导航。标题栏「系统管理」可展开启动、安装、终端、更新、GPU 检测等入口。
- 系统管理切换保持同一个 webview；保存本地路由、query、hash，重开恢复作品与标签页。后端离线显示等待与重试，恢复后保留健康 guest 的内存状态；加载失败或 renderer 崩溃可重试加载。
- 补齐 shell 的 terminalHint DOM 引用；修复 ProjectView 缺少 reactive 导入造成的作品内容空白；恢复标签页时加载对应数据，侧栏 query 变化同步到项目标签。
- preload 只允许订阅明确事件；全部 main IPC 校验 sender webContents、主框架与精确 shell 文件 URL。openPath 限定应用目录，日志输入限制类型和大小。
- 启用 sandbox/contextIsolation，关闭 guest Node 与嵌套 webview，移除 allowpopups。校验 webview src、partition、preload、安全选项；拦截任意站点导航及重定向。HTTPS 外部窗口请求需明确确认后才使用系统浏览器；ComfyUI 仍走固定本地地址。
- 主进程先检测本地后端再决定启动 launcher；健康轮询持续运行，重试串行，已有自有 launcher 不重复启动。仅停止本次 launch 返回的子进程 PID 树，不按端口杀进程、不自动接管旧 PID 文件。
- 自有 launcher PID 诊断记录在 userData/owned-service.json；launcher 继续传入 --no-browser，stdout/stderr 分别解析。当前桌面端不自动启动 ComfyUI，因而不会额外启动或终止用户已有 ComfyUI。
- 打包排除 settings.json 与数据库 WAL/SHM；升级同步保留运行时的配置、数据库、作品与模型。无数据库迁移、无模型推理实现变更。

实现依据：[Electron 官方安全文档](https://www.electronjs.org/docs/latest/tutorial/security)。

## 真实测试结果

| 命令/检查 | 结果及边界 |
|---|---|
| `npm.cmd run build --prefix frontend` | 通过，56 modules；最终产物包含 reactive 与标签同步修复 |
| `npm.cmd test --prefix desktop` | 6/6 通过：URL 边界、IPC 来源、外部服务复用、断线状态、串行启动/自有 PID、关机竞态、HTTP 健康响应 |
| `backend/.venv/Scripts/python.exe packaging/test_payload.py` | 2/2 通过：包内无本机配置/数据库；更新同步保留原文件 |
| `python scripts/e2e_services.py`，隔离 backend | 原有 10 项全通过；mock LLM/图像服务，不代表真实模型生成 |
| `python scripts/e2e_assets.py`，隔离 backend | 原有资产、角色、edge-tts、审核门禁、返工、时间线、字幕、混音 MP4 导出全通过；导出 7.13 秒、约 110 KB |
| `python scripts/e2e_storyboard.py`，隔离 backend | 失败：约 39 秒后报告隔离目录缺少 anime-diffusers，未运行后续生成审核导出；未下载或改动用户模型 |
| `test_load_diffusers.py` / `test_export.py` | 未执行：分别需要模型加载和至少两个已完成历史生成任务；隔离测试库没有这些条件 |
| `node desktop/tests/run-electron-smoke.js --proxy-target=8002`，及加 `--restore` | 真实 Electron 开发版通过首次进入及重开恢复；真实项目/时间线标签、资产侧栏切换、单导航、草稿保留、导航拦截、断线恢复、服务复用通过 |
| 同上加 `--packaged`，首次及 `--restore` | 真实 win-unpacked exe 通过相同断言，并验证 GPU 按钮显示检测结果、日志文本复制写权限可用且剪贴板读取被拒绝（未读取或改写系统剪贴板内容）；不是模拟 app.isPackaged。测试在本机调试器中加载测试脚本，运行正常打包 main；没有在产品中加入测试入口 |
| 缺少 launcher 的隔离副本 | 真实 Electron 显示明确错误，重试仍可用，通过；未移动/删除真实 launcher |
| PyInstaller 启动器构建 | 通过，当前前端与配置保留规则已嵌入 |
| `npm.cmd run dist --prefix desktop` | 通过，生成 Windows NSIS 包，`--publish never`，未发布 |
| 备份/恢复/原文件核对 | 110 文件备份和恢复 SHA-256 校验通过；最后核对全部 108 个非数据库原文件与备份一致 |
| `git diff --check` | 通过 |

测试曾受 Windows 沙箱 `spawn EPERM` 影响，提权重跑通过。测试中发现的错误未按通过处理：隔离资源遗漏导致的 SPA 404 已修正测试部署；项目页面 reactive 缺失已修复；手工构造 history 事件改为真实 Vue 路由导航。最终断言检查 shell 与 guest 的 JavaScript 运行时错误。

## 复现集成测试

先创建独立 backend 副本（app、scripts、comfy_workflows）及其同级 frontend/dist，避免测试使用真实数据库、输出和设置；基线测试副本为 `.phase1/api/`。在副本目录设置 AIVIDEO_DB、AIVIDEO_OUTPUTS_DIR、AIVIDEO_SETTINGS 为该目录内绝对路径，以项目 Python 执行：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

另一个终端在仓库根执行：

```powershell
node desktop/tests/run-electron-smoke.js --proxy-target=8002
node desktop/tests/run-electron-smoke.js --proxy-target=8002 --restore
node desktop/tests/run-electron-smoke.js --packaged --proxy-target=8002
node desktop/tests/run-electron-smoke.js --packaged --proxy-target=8002 --restore
```

runner 先检查后端，再于 UI 启动前创建隔离项目夹具；本机 8000 用作可切断健康状态的代理，9235 为打包测试临时本机调试端口，需保持空闲。测试配置、JSON 结果与截图保存在 `.phase1/smoke-dev/`、`.phase1/smoke-packaged/`。真实生成、用户数据、发布服务不参与这些桌面测试。

## 未完成的验收及后续风险

- Windows 原生界面工具经过重试、重置会话仍报 `Computer Use native pipe is unavailable ... os error 2`。未执行安装向导/卸载向导人工操作；真实打包 exe 自动化验证与截图已完成，不能把它写成安装向导人工验收通过。
- 真实 packaged 自动更新检查报 `No published versions on GitHub`；应用仍可工作，并通过 update:error 显示失败。下载、安装更新、真实版本升级链未验证。构建提示未签名，本包为本地验收包。
- 开发 guest 当前仍有前端未设置 CSP 的 Electron 警告；shell 已设置 CSP，guest 已隔离、沙箱化并限制导航。未修改全站资源策略以免损坏已有素材地址。
- 测试期间在 UI 请求并发下观察到旧后端共享 SQLite 连接的异常：音轨 GET 偶发 500（事务异常）、一次项目 POST 返回 null。测试夹具改为 UI 启动前创建；**未把这一数据层问题宣称修复**，应在批次 2/3 专门处理连接/事务边界并补并发回归。
- 自有进程停止路径有单测覆盖；未在最终完整安装环境中实际启动需自动下载模型的 launcher，并验证其全部后代退出。未执行真实 ComfyUI/8GB GPU 生成验收。
- 重启只保证最后页面/作品/标签和已保存数据；未保存表单的内存状态只在同一进程的系统管理切换/断线重连期间保留，不承诺跨进程恢复。

## 产物与回滚

安装包：`desktop/dist/AI动画导演工作台 Setup 0.1.0.exe`（103,142,812 字节）。
SHA-256：`FA0FEB87580205ACD1E5449F9A931C26E27DDDC0D2995D7C79584C7749E298D3`。
旧目录可能保留更早的 `aivideo-desktop-setup-0.1.0.exe`，本次应使用上面明确命名的产物。

基线/备份/数据恢复见 baseline.md。代码回滚采用 git revert 本批次提交；无数据库迁移需逆转。旧 launcher 保存在 `.phase1/launcher-before-phase1.exe`，可在所有自有进程退出后复制回 `packaging/dist/AI视频工坊-v1.0.0.exe` 并用旧代码重新构建桌面包；不要覆盖用户运行时配置与作品目录。

批次 0 与批次 1 分别提交，任务书原文件保持未跟踪。实现与测试文件的变更统计如下（不包含本报告自身）：

```text
 desktop/backend-service.js          |  62 +++++++++++
 desktop/electron-builder.yml        |   2 +
 desktop/main.js                     | 204 +++++++++++++++++++++++++-----------
 desktop/package.json                |   1 +
 desktop/preload.js                  |  10 ++
 desktop/renderer/app.js             | 116 ++++++++++++++++++--
 desktop/renderer/index.html         |  13 ++-
 desktop/renderer/styles.css         |   9 ++
 desktop/security.js                 |  27 +++++
 desktop/tests/electron-smoke.js     | 142 +++++++++++++++++++++++++
 desktop/tests/run-electron-smoke.js | 108 +++++++++++++++++++
 desktop/tests/runtime.test.js       |  87 +++++++++++++++
 frontend/src/App.vue                |   9 +-
 frontend/src/views/ProjectView.vue  |   9 +-
 packaging/build.py                  |   2 +-
 packaging/launcher.py               |   3 +-
 packaging/test_payload.py           |  54 ++++++++++
 17 files changed, 773 insertions(+), 85 deletions(-)
```
