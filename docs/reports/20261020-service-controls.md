# 桌面端服务开启与关闭

日期：2026-10-20。

## 用户操作

标题栏新增「开启服务」「关闭服务」及运行状态，创作工作台和启动台均可直接访问。关闭前提示先保存内容并说明进行中的生成、导出或安装会中断。关闭成功后展示「服务已关闭」，停止计时和重连，突出显示重新开启入口；开启后恢复上次作品路由。

## 资源释放与进程范围

- 后端管理器只结束本次桌面会话启动的 launcher PID 及其子进程，Windows 使用 taskkill /T /F；不按端口扫描或结束其他进程。
- 关闭操作等待实际结束命令完成。失败会保留进程归属和运行状态，显示错误，允许重试，不显示虚假的“已释放”。
- 主动关闭会清除健康探测与启动计时器，并移除工作台 webview，结束前端轮询；桌面壳窗口仍保留供再次开启。
- 健康探测使用操作版本号隔离，旧请求完成后不能把主动关闭状态改成已连接或启动新进程；连续点击只执行一次关闭；再次开启等待关闭结束。
- 旧进程迟到的日志和退出事件不会覆盖新进程的状态与归属。
- 若复用了其他窗口启动的后端，显示「外部服务 · 原窗口关闭」并禁用关闭按钮。独立启动的 ComfyUI 不属于本软件的子进程，须在原窗口关闭；本按钮不声称释放其显存。

## 验证

- 桌面逻辑及界面回归：26 项通过，覆盖停止、再开启、竞态、失败、外部进程保护、取消关闭和作品路由恢复。
- Windows 真实进程测试：两次开启/关闭轻量后端，验证监听端口释放、子进程退出、停止后不自动重启；1 项通过。
- 真实 Electron 测试：使用独立配置目录与轻量服务替身，沿真实 main/preload/renderer IPC 完成关闭与开启；确认服务/子进程结束、计时与探测停止、webview 销毁、重新开启与作品路由恢复。没有运行真实安装器、模型或用户数据。
- 真实窗口截图已检查，位置：`.phase1/service-controls-smoke/running.png`、`stopped.png`；测试结果：同目录 `result.json`。
- 新测试：`desktop/tests/service-process.test.js`、`service-controls-smoke.js`；后者需用 Electron 执行，不属于 node --test 集合。

Node 测试命令：`node --test --test-isolation=none tests/*.test.js`（在 desktop 下运行）。真实窗口测试：`electron tests/service-controls-smoke.js`。受限环境禁止 Node/Chromium 创建子进程时，真实进程及窗口测试需要在允许启动子进程的环境执行，不能通过关闭 Electron 沙箱规避。

本次修改针对桌面 EXE 壳；浏览器通过 start.bat 启动的服务不受桌面进程管理器控制。没有改动生成模型、任务参数或项目数据格式。

## 桌面安装包交付

用户确认使用桌面 EXE 后，已完成 0.1.1 构建：

- 安装包：`desktop/dist/service-controls-0.1.1/aivideo-desktop-setup-0.1.1.exe`，103,260,739 字节。
- 便携目录：同目录 `win-unpacked/`。该目录需整体保留，不能只复制其中一个 EXE。
- 版本号同步更新 desktop/package.json 与 package-lock.json。安装包 artifactName 固定为 `aivideo-desktop-setup-${version}.${ext}`，与 latest.yml 一致。
- 修复 build-desktop.bat 从错误的父目录执行和绑定个人 Node 路径的问题，删除构建后重命名的旧逻辑。
- 本次输出使用独立目录，保留既有安装包与运行时数据；构建没有发布更新或安装到用户系统。

交付验证：

- Vite 生产构建通过，当前前后端与工作流共 62 个文件和启动器内载荷逐字节一致；没有包含用户数据库、settings.json、模型、outputs 或虚拟环境。
- 新包的 7 个桌面源文件与工作区完全一致，生产 package.json 的版本、入口和依赖匹配；安装包 SHA-512 与 latest.yml 两处校验值一致。
- 直接运行新生成的 `win-unpacked` EXE，沿真实打包后的 main/preload/renderer 路径验证服务停止、子进程终止、停止探测、销毁 webview、重新启动和恢复作品路由，全部通过。测试使用轻量替身服务，不涉及实际模型。
- 打包后再次执行桌面完整回归，27 项通过。
- 交付校验记录：`.phase1/desktop-release-0.1.1/payload-verification.json`、`installer-verification.json`；打包 EXE 测试结果及截图：`.phase1/service-controls-packaged/`。
