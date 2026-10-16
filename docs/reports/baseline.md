# 第一阶段基线（2026-10-16）

范围：改造.md 的批次 0、1。批次 2–6 不在本次实现内。

## Git 与定位

- 初始本地 main：`cb5b655609ee1750be62102dd35dfd4fdf56af9a`。
- `git fetch origin main` 成功；远端 main：`ea1eaa76a4e68d4a083bc44dd4f441bebab9523f`。
- 本地比远端多一个已提交安全修复，保留本地提交，不用远端覆盖。
- 初始代码工作区干净；仅用户提供的 `改造.md` 未跟踪，保留原文件，不纳入实现提交。
- 工作分支：`feature/nmvideo-freevideo-phase1`。
- 路径存在：`desktop/{main.js,preload.js,renderer/}`、`frontend/src/{App.vue,router.js,store/project.js}`、`backend/app/`、`packaging/launcher.py`。

## 备份与恢复

本机备份：`D:/work/aivideo/.phase1/backup/`，清单 `manifest.json` 包含相对路径、长度、SHA-256。
备份开发 backend 及 packaging/dist/runtime/backend 中数据库、settings.json、outputs 全部作品文件：110 文件，21,171,328 字节。数据库用 SQLite backup API 制作一致性副本；PRAGMA integrity_check=ok。
将全部文件复制至 `.phase1/restore-check/`，逐文件 SHA-256 对比通过。备份含本地配置，仅保留本机，已忽略 Git。

恢复步骤：退出桌面端及自有后端，确认没有写入者；将 backup 中对应 backend 目录恢复至原位置（包括 settings.json 与 outputs），数据库恢复前另存当前数据库，避免覆盖新作品。打开 SQLite 执行 integrity_check 并核对项目列表。恢复时保持数据库与作品文件来自同一清单。本批次不迁移数据库。
代码回滚：对本分支批次 1 提交执行 git revert；不重置 main 或用户文件。

## 已有行为与复现

1. `npm start --prefix desktop` 进入工作台，再返回启动台，再进入工作台：setView 每次赋值 webview.src，导致重复加载，页面内未保存状态丢失。
2. renderer/index.html 的外层 sidebar 与 App.vue 的工作台 sidebar 同时存在。
3. renderer/app.js 的 applyStaticI18n 引用 el.terminalHint，但 DOM 映射中缺少该属性，启动脚本可抛异常。
4. 主进程无 IPC sender 校验；preload.on 接受任意 channel；webview allowpopups；未限制导航和 attach 配置；全局禁用 Chromium sandbox。
5. 主进程直接启动 launcher，不先探测已有后端。launcher 有 --no-browser 且桌面端已传入，此路径代码不会主动打开系统浏览器；实际 Windows 行为待批次 1 验证。
6. health 就绪后停止轮询，后端后续断开不能由壳层检测。

以上为源代码审查结果，未标记为已完成的桌面人工验收。

## 后端只读审查

ComfyUI 客户端已有 WS 与 history 轮询兜底，但队列重启仍直接将 running 标失败，尚无持久 provider prompt 关联；留待批次 2。本地已有提交修复了版本归属、审核检查、剧本导入和部分队列恢复，保留原实现。SQLite/工作流/审核状态不在本批次改动。

## 基线验证

- Node v24.14.0；Python 3.13.14；依赖已安装。
- `npm.cmd run build --prefix frontend`：通过（56 modules）。
- 在 `.phase1/api/` 复制 app/scripts/comfy_workflows，以 AIVIDEO_DB、AIVIDEO_OUTPUTS_DIR、AIVIDEO_SETTINGS 指向隔离目录启动 uvicorn，端口 8000；不使用真实数据库或设置。
- `python scripts/e2e_services.py`：10 项全部通过（mock LLM/图像 API，非真实模型服务）。首次隔离运行缺少 frontend/dist 导致 SPA 404；补齐隔离静态资源并重启后通过，属于测试环境修正。
- 其他测试结果及桌面/打包验证见 phase1.md；依赖真实模型的耗时测试单独标记，不冒充通过。

## 批次 1 实施前计划

修改 desktop/main.js、preload.js、renderer/{index.html,app.js,styles.css}、electron-builder.yml、frontend/src/router.js；必要时改进 App.vue 离线文字。新增桌面安全和重连回归测试。
风险：本地导航被误拦、沙箱兼容性、重试重复启动、误杀外部服务、打包漏带辅助模块。测试应覆盖 URL/IPC 边界、已运行服务复用、连续切页不加载、断线恢复、退出所有权与安装包。保留现有推理和数据层。

安全实现参考：[Electron 官方安全文档](https://www.electronjs.org/docs/latest/tutorial/security)。
