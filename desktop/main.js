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
const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron')
const path = require('path')
const fs = require('fs')
const { spawn, execFile } = require('child_process')
const { autoUpdater } = require('electron-updater')
const { isAppURL, isShellURL, isTrustedSender, isExternalURL } = require('./security')
const { BackendService } = require('./backend-service')
const SHELL_FILE = path.join(__dirname, 'renderer', 'index.html')

// Keep Chromium's sandbox enabled; compatibility failures must be diagnosed explicitly.

const IS_DEV = !app.isPackaged
const LAUNCHER_EXE = `AI视频工坊-v${require('./package.json').version}.exe`
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
let backend = null
let stopRequest = null

async function openExternal(url) {
  if (!isExternalURL(url)) return
  const { response } = await dialog.showMessageBox(mainWin, {
    type: 'question', title: '打开外部链接', message: '使用系统浏览器打开此链接？',
    detail: url, buttons: ['取消', '打开'], defaultId: 0, cancelId: 0,
  })
  if (response === 1) await shell.openExternal(url)
}

function guardContents(contents, allowed) {
  const navigate = (event, url) => {
    if (allowed(url)) return
    event.preventDefault()
    // Redirects are blocked without opening a browser automatically.
  }
  contents.on('will-navigate', navigate)
  contents.on('will-redirect', navigate)
  contents.on('will-frame-navigate', (event) => {
    if (!allowed(event.url)) event.preventDefault()
  })
  contents.setWindowOpenHandler(({ url }) => {
    openExternal(url).catch(() => {})
    return { action: 'deny' }
  })
  // Preserve plain-text copy buttons without granting clipboard read or device access.
  const canWriteClipboard = (permission, details) => permission === 'clipboard-sanitized-write' &&
    details.isMainFrame && allowed(details.requestingUrl)
  contents.session.setPermissionRequestHandler((_wc, permission, callback, details) => {
    callback(canWriteClipboard(permission, details))
  })
  contents.session.setPermissionCheckHandler((_wc, permission, _origin, details) => canWriteClipboard(permission, details))
}

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
      sandbox: true,
      webviewTag: true,
    },
  })
  guardContents(mainWin.webContents, url => isShellURL(url, SHELL_FILE))
  mainWin.webContents.on('will-attach-webview', (event, prefs, params) => {
    if (!isAppURL(params.src, launcherPort) || params.partition !== 'persist:aivideo') {
      event.preventDefault()
      return
    }
    delete prefs.preload
    Object.assign(prefs, { nodeIntegration: false, nodeIntegrationInSubFrames: false,
      nodeIntegrationInWorker: false, contextIsolation: true, sandbox: true,
      webSecurity: true, allowRunningInsecureContent: false, webviewTag: false })
    params.allowpopups = false
  })
  mainWin.webContents.on('did-attach-webview', (_event, contents) => {
    guardContents(contents, url => isAppURL(url, launcherPort))
    contents.on('will-attach-webview', event => event.preventDefault())
  })
  mainWin.loadFile(SHELL_FILE)
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
  if (backend && !backend.enabled) return

  const m = text.match(/^\[AIVIDEO\]\s+STATE=(\w+)/)
  if (m) {
    const state = m[1]
    send('backend:state', { state, progress: STAGE_PROGRESS[state] ?? 0 })
    if (state === 'ready') pollHealth().catch(() => {})
    if (state === 'error') send('backend:error', { text })
    return
  }
  const p = text.match(/^\[AIVIDEO\]\s+PORT=(\d+)/)
  if (p) {
    const port = Number(p[1])
    if (!Number.isInteger(port) || port < 1 || port > 65535) return
    launcherPort = port
    if (backend) backend.port = port
    send('backend:port', { port: launcherPort })
  }
}

function spawnLauncher() {
  const exe = launcherPath()
  console.log(`[desktop] spawn launcher: ${exe}`)
  if (!fs.existsSync(exe)) {
    send('backend:error', { text: `找不到启动器：${exe}\n请先运行 packaging/build.py 生成 exe，或用 npm run dist 打出完整安装包。` })
    return null
  }
  const portFile = path.join(app.getPath('temp'), `aivideo-port-${process.pid}.txt`)
  try { fs.unlinkSync(portFile) } catch { /* 首次不存在 */ }

  send('backend:state', { state: 'preparing', progress: STAGE_PROGRESS.preparing, owned: true })
  send('backend:log', { text: `$ ${LAUNCHER_EXE} --yes --no-browser --plain --port-file ${portFile}`, stream: 'meta', ts: Date.now() })

  launcherProc = spawn(exe, ['--yes', '--no-browser', '--plain', '--port-file', portFile], {
    cwd: path.dirname(exe),
    windowsHide: true,
    // launcher 及其子进程（pip/uvicorn/下载脚本）输出统一 UTF-8，终端面板按 UTF-8 解码
    env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
  })
  const child = launcherProc
  try {
    fs.writeFileSync(path.join(app.getPath('userData'), 'owned-service.json'), JSON.stringify({
      ownerPid: process.pid, launcherPid: launcherProc.pid, executable: exe, startedAt: new Date().toISOString(),
    }))
  } catch (error) { logLine(`进程记录写入失败：${error.message}`, 'stderr') }

  for (const stream of ['stdout', 'stderr']) {
    require('readline').createInterface({ input: launcherProc[stream] })
      .on('line', line => { if (launcherProc === child) logLine(line, stream) })
  }
  launcherProc.on('error', (e) => {
    if (launcherProc === child && backend?.enabled) send('backend:error', { text: `启动器进程错误: ${e.message}` })
  })
  launcherProc.on('exit', (code) => {
    send('backend:log', { text: `启动器退出（code=${code}）`, stream: 'meta', ts: Date.now() })
    if (launcherProc !== child) return
    launcherProc = null
    try { fs.unlinkSync(path.join(app.getPath('userData'), 'owned-service.json')) } catch {}
    // 用户手动关窗口导致的退出不报错；异常退出提示
    if (code !== 0 && code !== null && !quitting && backend?.enabled) {
      send('backend:error', { text: `后端进程异常退出（code=${code}），请把终端日志反馈给开发者` })
    }
  })

  // health 轮询兜底（不等 launcher 的 ready 行，端口文件/双保险）
  return launcherProc
}

async function pollHealth() {
  if (!backend?.enabled || backend.stopping || backend.closed) return
  // 端口文件优先（launcher 可能顺延了端口）
  try {
    const pf = path.join(app.getPath('temp'), `aivideo-port-${process.pid}.txt`)
    if (fs.existsSync(pf)) {
      const v = parseInt(fs.readFileSync(pf, 'utf8').trim(), 10)
      if (Number.isInteger(v) && v > 0 && v <= 65535) {
        launcherPort = v
        backend.port = v
      }
    }
  } catch { /* ignore */ }
  await backend.check()
}

function finishBoot() {
  if (readyNotified || !backend?.enabled || backend.stopping) return
  readyNotified = true
  console.log(`[desktop] backend ready at http://127.0.0.1:${launcherPort}`)
  send('backend:ready', { ...backend.status(), url: `http://127.0.0.1:${launcherPort}` })
}

/** 杀 launcher 进程树（uvicorn 子进程一起杀，否则留孤儿占端口） */
function killLauncherTree() {
  if (backend) backend.close()
}

function watchBackend() {
  if (!healthTimer) healthTimer = setInterval(() => pollHealth().catch(() => {}), 2000)
  if (!bootTimer) bootTimer = setInterval(() => {
    if (backend?.enabled && !backend.online) send('backend:tick', { seconds: ++bootSeconds })
  }, 1000)
}

function startBackend() {
  if (!backend) backend = new BackendService({
    launch: spawnLauncher,
    changed: (online) => {
      if (online) finishBoot()
      else if (backend.enabled) {
        readyNotified = false
        send('backend:offline', { text: '本地服务已断开，正在等待重连；可在系统管理中重试。' })
      }
    },
    stop: pid => new Promise((resolve, reject) => {
      execFile('taskkill', ['/pid', String(pid), '/T', '/F'], { windowsHide: true, timeout: 15000 }, error => error ? reject(error) : resolve())
    }),
  })
  watchBackend()
  return backend.enable().catch(e => send('backend:error', { text: e.message }))
}

async function stopBackend() {
  if (!backend || !backend.enabled) return backend?.status()
  // 先等当前探测结束，避免把尚未识别的外部服务当作已经停止。
  if (!backend.child) await backend.check()
  if (backend.online && !backend.child) throw new Error('当前连接的是从其他窗口启动的服务，请到原启动窗口关闭。')
  if (backend.child) {
    const { response } = await dialog.showMessageBox(mainWin, {
      type: 'question', title: '关闭本地服务', message: '关闭服务并释放后台占用？',
      detail: '请先保存编辑内容。进行中的生成、导出或安装会中断；已保存的作品会保留，之后可点击“开启服务”继续使用。独立启动的 ComfyUI 需在原窗口关闭。',
      buttons: ['保持运行', '关闭服务'], defaultId: 0, cancelId: 0,
    })
    if (response !== 1) return { ...backend.status(), cancelled: true }
  }
  send('backend:state', { state: 'stopping', progress: 0 })
  clearInterval(healthTimer)
  clearInterval(bootTimer)
  healthTimer = bootTimer = null
  try {
    await backend.disable()
    readyNotified = false
    bootSeconds = 0
    const status = backend.status()
    send('backend:stopped', status)
    return status
  } catch (error) {
    watchBackend()
    send('backend:state', { state: backend.online ? 'ready' : 'starting', progress: backend.online ? 1 : 0 })
    throw error
  }
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
function handle(channel, handler) {
  ipcMain.handle(channel, (event, ...args) => {
    if (!isTrustedSender(event, mainWin && mainWin.webContents, SHELL_FILE)) throw new Error('Untrusted IPC sender')
    return handler(event, ...args)
  })
}
handle('backend:status', () => backend?.status() || { online: false, port: launcherPort, enabled: true, owned: false })
handle('backend:start', () => {
  if (stopRequest) return stopRequest
  bootSeconds = 0
  return startBackend()
})
handle('backend:stop', () => {
  if (!stopRequest) stopRequest = stopBackend().finally(() => { stopRequest = null })
  return stopRequest
})
handle('backend:retry', () => {
  if (stopRequest) return stopRequest
  return startBackend()
})
handle('backend:hardware', async () => {
  if (!backend?.online) throw new Error('本地服务尚未连接，请重试连接后检测。')
  const response = await fetch(`http://127.0.0.1:${launcherPort}/api/hardware`, { signal: AbortSignal.timeout(20000), redirect: 'error' })
  if (!response.ok) throw new Error(`硬件检测失败：HTTP ${response.status}`)
  return response.json()
})
handle('win:minimize', () => mainWin && mainWin.minimize())
handle('win:toggleMaximize', () => {
  if (!mainWin) return false
  if (mainWin.isMaximized()) { mainWin.unmaximize(); return false }
  mainWin.maximize(); return true
})
handle('win:close', () => { quitting = true; killLauncherTree(); app.quit() })
handle('win:isMaximized', () => mainWin && mainWin.isMaximized())

handle('app:version', () => app.getVersion())
handle('app:paths', () => ({
  runtime: runtimeDir(),
  launcher: launcherPath(),
  userData: app.getPath('userData'),
  logs: app.getPath('logs'),
}))

handle('app:openPath', async (_e, p) => {
  const allowed = [runtimeDir(), path.dirname(launcherPath()), app.getPath('userData'), app.getPath('logs')]
  if (typeof p !== 'string' || !allowed.some(dir => path.resolve(dir) === path.resolve(p))) return { ok: false, error: 'Unsupported path' }
  try {
    const error = await shell.openPath(p)
    return { ok: !error, error }
  } catch (e) {
    return { ok: false, error: String(e.message || e) }
  }
})

/** 打开 ComfyUI（本地 8188，浏览器打开） */
handle('app:openComfyUI', async () => {
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
handle('app:createShortcut', async () => {
  if (process.platform !== 'win32') return { ok: false, error: 'only windows' }
  try {
    const desktop = app.getPath('desktop')
    const lnk = path.join(desktop, 'AI 动画导演工作台.lnk')
    const target = process.execPath
    const args = app.isPackaged ? '' : path.join(__dirname, 'main.js')
    const workDir = app.isPackaged ? path.dirname(process.execPath) : __dirname
    const icon = path.join(__dirname, 'build', 'icon.png')
    // 用 PowerShell WScript.Shell 写快捷方式（零依赖）
    const quote = value => value.replace(/'/g, "''")
    const ps = [
      `$s=(New-Object -ComObject WScript.Shell).CreateShortcut('${quote(lnk)}');`,
      `$s.TargetPath='${quote(target)}';`,
      `$s.Arguments='${quote(args)}';`,
      `$s.WorkingDirectory='${quote(workDir)}';`,
      `$s.IconLocation='${quote(icon)}';`,
      `$s.Description='AI 动画导演工作台';`,
      `$s.Save()`,
    ].join(' ')
    await new Promise((resolve, reject) => {
      execFile('powershell', ['-NoProfile', '-Command', ps], (err) => (err ? reject(err) : resolve()))
    })
    return { ok: true, path: lnk }
  } catch (e) {
    return { ok: false, error: String(e.message || e) }
  }
})

handle('app:saveLog', async (_e, content) => {
  if (typeof content !== 'string' || content.length > 5_000_000) return { ok: false, error: 'Invalid log' }
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

handle('app:relaunch', () => {
  quitting = true
  killLauncherTree()
  app.relaunch()
  app.exit(0)
})

// ---------- 更新 IPC ----------
handle('update:check', async () => {
  if (IS_DEV) { send('update:none'); return }
  try { await autoUpdater.checkForUpdates() } catch { /* error 事件已发 */ }
})
handle('update:download', async () => {
  try { await autoUpdater.downloadUpdate() } catch { /* error 事件已发 */ }
})
handle('update:install', () => {
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
    mainWin.webContents.once('did-finish-load', startBackend)

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
