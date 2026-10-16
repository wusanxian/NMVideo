/**
 * Electron 主进程：AI 动画导演工作台桌面壳
 *
 * 职责：
 *  - 无框窗口 + 自定义标题栏（renderer 发 IPC 控制）
 *  - spawn launcher.exe（PyInstaller 单文件，全自动：环境/依赖/模型/起服务）
 *    stdout/stderr 实时转发到「终端」面板；解析 [AIVIDEO] STATE=xxx 驱动状态机
 *  - health 轮询兜底：launcher 就绪行之外双保险，ready 后通知 renderer 切应用视图
 *  - electron-updater 自动更新（GitHub Releases，latest.yml）
 *  - 退出时 taskkill /T 杀 launcher 进程树（uvicorn 是它的子进程，直接 kill 会留孤儿）
 *  - 单实例锁
 */
const { app, BrowserWindow, ipcMain, shell, dialog, nativeImage } = require('electron')
const path = require('path')
const fs = require('fs')
const { spawn, execFile } = require('child_process')
const { autoUpdater } = require('electron-updater')

// 本机（部分 Windows 环境/安全软件）Chromium 沙箱初始化失败会导致 GPU 进程
// 0xC0000005 连环崩溃直至 FATAL；禁用进程沙箱即可正常渲染（VS Code/WorkBuddy 同做法）
app.commandLine.appendSwitch('no-sandbox')

const IS_DEV = !app.isPackaged
const LAUNCHER_EXE = 'AI视频工坊-v1.0.0.exe'
const APP_PORT_DEFAULT = 8000
let quitting = false

// ---------- 路径 ----------
function launcherPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'launcher', LAUNCHER_EXE)
  }
  return path.join(__dirname, '..', 'packaging', 'dist', LAUNCHER_EXE)
}

/** runtime 目录：与 launcher 保持一致——exe 同级 runtime/，写不进去退 %LOCALAPPDATA% */
function runtimeDir() {
  const base = path.dirname(launcherPath())
  try {
    const probe = path.join(base, '.write_test')
    fs.writeFileSync(probe, '')
    fs.unlinkSync(probe)
    return path.join(base, 'runtime')
  } catch {
    return path.join(process.env.LOCALAPPDATA || require('os').homedir(), 'AI视频工坊', 'runtime')
  }
}

// ---------- 全局状态 ----------
let mainWin = null
let launcherProc = null
let launcherPort = APP_PORT_DEFAULT
let readyNotified = false
let healthTimer = null
let bootSeconds = 0
let bootTimer = null

// 阶段 → 进度权重（模型下载是大头，按阶段推进度条）
const STAGE_PROGRESS = {
  preparing: 0.04,
  python: 0.14,
  torch: 0.34,
  deps: 0.55,
  models: 0.86,
  starting: 0.95,
  ready: 1.0,
}

function send(channel, payload) {
  if (mainWin && !mainWin.isDestroyed()) {
    try {
      mainWin.webContents.send(channel, payload)
    } catch { /* renderer 崩溃/重建时静默 */ }
  }
}

// ---------- 窗口 ----------
function createWindow() {
  mainWin = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 960,
    minHeight: 640,
    frame: false,
    titleBarStyle: 'hidden',
    show: false,
    backgroundColor: '#0f1115',
    icon: path.join(__dirname, 'build', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true,
    },
  })
  mainWin.loadFile(path.join(__dirname, 'renderer', 'index.html'))
  mainWin.once('ready-to-show', () => mainWin.show())
  mainWin.on('maximize', () => send('win:maximized', true))
  mainWin.on('unmaximize', () => send('win:maximized', false))
  mainWin.on('closed', () => { mainWin = null })
}

// ---------- 后端进程管理 ----------
function logLine(line, stream = 'stdout') {
  const text = String(line).replace(/\x1b\[[0-9;]*m/g, '') // 去 ANSI 颜色码（双保险）
  if (!text.trim()) return
  send('backend:log', { text, stream, ts: Date.now() })

  const m = text.match(/^\[AIVIDEO\]\s+STATE=(\w+)/)
  if (m) {
    const state = m[1]
    send('backend:state', { state, progress: STAGE_PROGRESS[state] ?? 0 })
    if (state === 'ready') finishBoot()
    if (state === 'error') send('backend:error', { text })
    return
  }
  const p = text.match(/^\[AIVIDEO\]\s+PORT=(\d+)/)
  if (p) {
    launcherPort = parseInt(p[1], 10) || APP_PORT_DEFAULT
    send('backend:port', { port: launcherPort })
  }
}

function spawnLauncher() {
  const exe = launcherPath()
  console.log(`[desktop] spawn launcher: ${exe}`)
  if (!fs.existsSync(exe)) {
    send('backend:error', { text: `找不到启动器：${exe}\n请先运行 packaging/build.py 生成 exe，或用 npm run dist 打出完整安装包。` })
    return
  }
  const portFile = path.join(app.getPath('temp'), `aivideo-port-${process.pid}.txt`)
  try { fs.unlinkSync(portFile) } catch { /* 首次不存在 */ }

  send('backend:state', { state: 'preparing', progress: STAGE_PROGRESS.preparing })
  send('backend:log', { text: `$ ${LAUNCHER_EXE} --yes --no-browser --plain --port-file ${portFile}`, stream: 'meta', ts: Date.now() })

  launcherProc = spawn(exe, ['--yes', '--no-browser', '--plain', '--port-file', portFile], {
    cwd: path.dirname(exe),
    windowsHide: true,
    // launcher 及其子进程（pip/uvicorn/下载脚本）输出统一 UTF-8，终端面板按 UTF-8 解码
    env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
  })

  let buf = ''
  const onData = (stream) => (chunk) => {
    buf += chunk.toString('utf8')
    let idx
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).replace(/\r$/, '')
      buf = buf.slice(idx + 1)
      if (line.trim()) logLine(line, stream)
    }
  }
  launcherProc.stdout.on('data', onData('stdout'))
  launcherProc.stderr.on('data', onData('stderr'))
  launcherProc.on('error', (e) => send('backend:error', { text: `启动器进程错误: ${e.message}` }))
  launcherProc.on('exit', (code) => {
    send('backend:log', { text: `启动器退出（code=${code}）`, stream: 'meta', ts: Date.now() })
    launcherProc = null
    // 用户手动关窗口导致的退出不报错；异常退出提示
    if (code !== 0 && code !== null && !quitting) {
      send('backend:error', { text: `后端进程异常退出（code=${code}），请把终端日志反馈给开发者` })
    }
  })

  // health 轮询兜底（不等 launcher 的 ready 行，端口文件/双保险）
  healthTimer = setInterval(pollHealth, 2000)
  bootTimer = setInterval(() => {
    bootSeconds += 1
    send('backend:tick', { seconds: bootSeconds })
  }, 1000)
}

async function pollHealth() {
  // 端口文件优先（launcher 可能顺延了端口）
  try {
    const pf = path.join(app.getPath('temp'), `aivideo-port-${process.pid}.txt`)
    if (fs.existsSync(pf)) {
      const v = parseInt(fs.readFileSync(pf, 'utf8').trim(), 10)
      if (v > 0) launcherPort = v
    }
  } catch { /* ignore */ }
  const http = require('http')
  const req = http.get(`http://127.0.0.1:${launcherPort}/api/health`, { timeout: 3000 }, (res) => {
    if (res.statusCode === 200) finishBoot()
    res.resume()
  })
  req.on('error', () => { /* 未就绪，继续等 */ })
  req.on('timeout', () => req.destroy())
}

function finishBoot() {
  if (readyNotified) return
  readyNotified = true
  clearInterval(healthTimer)
  clearInterval(bootTimer)
  console.log(`[desktop] backend ready at http://127.0.0.1:${launcherPort}`)
  send('backend:ready', { port: launcherPort, url: `http://127.0.0.1:${launcherPort}` })
}

/** 杀 launcher 进程树（uvicorn 子进程一起杀，否则留孤儿占端口） */
function killLauncherTree() {
  if (!launcherProc || launcherProc.exitCode !== null) return
  const pid = launcherProc.pid
  execFile('taskkill', ['/pid', String(pid), '/T', '/F'], () => {})
}

// ---------- 自动更新 ----------
function setupAutoUpdater() {
  autoUpdater.autoDownload = false
  autoUpdater.on('checking', () => send('update:checking'))
  autoUpdater.on('update-available', (info) => send('update:available', info))
  autoUpdater.on('update-not-available', () => send('update:none'))
  autoUpdater.on('download-progress', (p) => send('update:progress', p))
  autoUpdater.on('update-downloaded', (info) => send('update:downloaded', info))
  autoUpdater.on('error', (e) => send('update:error', { message: String(e && e.message || e) }))

  // 启动 10 秒后检查一次（不阻塞启动）；开发模式跳过
  if (!IS_DEV) {
    setTimeout(() => autoUpdater.checkForUpdates().catch(() => {}), 10_000)
  }
}

// ---------- IPC ----------
ipcMain.handle('win:minimize', () => mainWin && mainWin.minimize())
ipcMain.handle('win:toggleMaximize', () => {
  if (!mainWin) return false
  if (mainWin.isMaximized()) { mainWin.unmaximize(); return false }
  mainWin.maximize(); return true
})
ipcMain.handle('win:close', () => { quitting = true; killLauncherTree(); app.quit() })
ipcMain.handle('win:isMaximized', () => mainWin && mainWin.isMaximized())

ipcMain.handle('app:version', () => app.getVersion())
ipcMain.handle('app:paths', () => ({
  runtime: runtimeDir(),
  launcher: launcherPath(),
  userData: app.getPath('userData'),
  logs: app.getPath('logs'),
}))

ipcMain.handle('app:openPath', async (_e, p) => {
  if (!p) return { ok: false }
  try {
    await shell.openPath(p)
    return { ok: true }
  } catch (e) {
    return { ok: false, error: String(e.message || e) }
  }
})

/** 打开 ComfyUI（本地 8188，浏览器打开） */
ipcMain.handle('app:openComfyUI', async () => {
  const http = require('http')
  await new Promise((resolve) => {
    const req = http.get('http://127.0.0.1:8188', { timeout: 2000 }, (res) => { res.resume(); resolve(true) })
    req.on('error', () => resolve(false))
    req.on('timeout', () => { req.destroy(); resolve(false) })
  }).then((alive) => {
    if (alive) shell.openExternal('http://127.0.0.1:8188')
    else dialog.showMessageBox(mainWin, {
      type: 'warning',
      title: 'ComfyUI 未运行',
      message: '没有检测到本机 8188 端口的 ComfyUI。\n请先启动 ComfyUI，再使用「打开 ComfyUI」。',
    })
  })
})

/** 创建桌面快捷方式（Windows: 写 .lnk 到桌面） */
ipcMain.handle('app:createShortcut', async () => {
  if (process.platform !== 'win32') return { ok: false, error: 'only windows' }
  try {
    const desktop = app.getPath('desktop')
    const lnk = path.join(desktop, 'AI 动画导演工作台.lnk')
    const target = process.execPath
    const args = app.isPackaged ? '' : path.join(__dirname, 'main.js')
    const workDir = app.isPackaged ? path.dirname(process.execPath) : __dirname
    const icon = path.join(__dirname, 'build', 'icon.png')
    // 用 PowerShell WScript.Shell 写快捷方式（零依赖）
    const ps = [
      '-NoProfile', '-Command',
      `$s=(New-Object -ComObject WScript.Shell).CreateShortcut('${lnk}');`,
      `$s.TargetPath='${target}';`,
      `$s.Arguments='${args}';`,
      `$s.WorkingDirectory='${workDir}';`,
      `$s.IconLocation='${icon}';`,
      `$s.Description='AI 动画导演工作台';`,
      `$s.Save()`,
    ].join(' ')
    await new Promise((resolve, reject) => {
      execFile('powershell', ps, (err) => (err ? reject(err) : resolve()))
    })
    return { ok: true, path: lnk }
  } catch (e) {
    return { ok: false, error: String(e.message || e) }
  }
})

ipcMain.handle('app:saveLog', async (_e, content) => {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const defaultName = `aivideo-log-${stamp}.txt`
  const { canceled, filePath } = await dialog.showSaveDialog(mainWin, {
    title: '导出终端日志',
    defaultPath: path.join(app.getPath('downloads'), defaultName),
  })
  if (canceled || !filePath) return { ok: false }
  fs.writeFileSync(filePath, content, 'utf8')
  return { ok: true, path: filePath }
})

ipcMain.handle('app:relaunch', () => {
  quitting = true
  killLauncherTree()
  app.relaunch()
  app.exit(0)
})

// ---------- 更新 IPC ----------
ipcMain.handle('update:check', async () => {
  if (IS_DEV) { send('update:none'); return }
  try { await autoUpdater.checkForUpdates() } catch { /* error 事件已发 */ }
})
ipcMain.handle('update:download', async () => {
  try { await autoUpdater.downloadUpdate() } catch { /* error 事件已发 */ }
})
ipcMain.handle('update:install', () => {
  quitting = true
  killLauncherTree()
  autoUpdater.quitAndInstall(false, true)
})

// ---------- 生命周期 ----------
if (!app.requestSingleInstanceLock()) {
  quitting = true
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWin) {
      if (mainWin.isMinimized()) mainWin.restore()
      mainWin.focus()
    }
  })

  app.whenReady().then(() => {
    createWindow()
    setupAutoUpdater()
    spawnLauncher()

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  })

  app.on('before-quit', () => { quitting = true })
  app.on('will-quit', () => {
    killLauncherTree()
    clearInterval(healthTimer)
    clearInterval(bootTimer)
  })
  // 点 X 走 IPC win:close；兜底：窗口全关也杀进程树
  app.on('window-all-closed', () => {
    killLauncherTree()
    app.quit()
  })
}
