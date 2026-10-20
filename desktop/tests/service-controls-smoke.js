// Run with Electron. Uses only a lightweight fixture backend and an isolated profile.
const { app, BrowserWindow, webContents, dialog } = require('electron')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const childProcess = require('node:child_process')
const { probeBackend } = require('../backend-service')
const root = path.resolve(__dirname, '../..')
const applicationRoot = process.env.SERVICE_SMOKE_APP_ROOT || path.join(root, 'desktop')
const profile = path.join(root, '.phase1', process.env.SERVICE_SMOKE_APP_ROOT ? 'service-controls-packaged' : 'service-controls-smoke')
fs.mkdirSync(profile, { recursive: true })
app.setPath('userData', profile)
app.setPath('logs', profile)
app.commandLine.appendSwitch('disable-gpu')
app.on('browser-window-created', (_event, win) => { win.show = () => {} })
dialog.showMessageBox = async () => ({ response: 1 })

// Inject fixture processes at the launcher boundary; never execute the real installer.
const launcher = app.isPackaged
  ? path.join(process.resourcesPath, 'launcher/AI视频工坊-v1.0.0.exe')
  : path.join(applicationRoot, '../packaging/dist/AI视频工坊-v1.0.0.exe')
const realExists = fs.existsSync
fs.existsSync = filename => filename === launcher || realExists(filename)
const realSpawn = childProcess.spawn
const children = []
childProcess.spawn = (filename, args, options) => {
  if (filename !== launcher) return realSpawn(filename, args, options)
  const process = realSpawn(global.process.execPath, [path.join(__dirname, 'fixtures/service-tree.js')], {
    ...options, env: { ...options.env, ELECTRON_RUN_AS_NODE: '1' },
  })
  const record = { process, workerPid: null, port: null }
  children.push(record)
  let output = ''
  process.stdout.on('data', data => {
    output += data.toString()
    record.workerPid = Number(output.match(/\[TEST_WORKER\] PID=(\d+)/)?.[1]) || null
    record.port = Number(output.match(/\[AIVIDEO\] PORT=(\d+)/)?.[1]) || null
  })
  return process
}
const serviceModule = require(path.join(applicationRoot, 'backend-service'))
const OriginalService = serviceModule.BackendService
serviceModule.BackendService = class extends OriginalService {
  constructor(options) { super({ ...options, port: 0 }) }
}
// Keep update checks offline while still loading the real packaged dependencies.
require(path.join(applicationRoot, 'node_modules/electron-updater')).autoUpdater.checkForUpdates = async () => null
require(path.join(applicationRoot, 'main'))

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(check, label) {
  const end = Date.now() + 30000
  while (Date.now() < end) {
    if (await check()) return
    await sleep(100)
  }
  throw new Error('Timed out: ' + label)
}
const alive = pid => { try { global.process.kill(pid, 0); return true } catch { return false } }
async function cleanup() {
  for (const child of children) {
    if (child.process.exitCode === null) await new Promise(resolve => {
      childProcess.execFile('taskkill', ['/PID', String(child.process.pid), '/T', '/F'], { windowsHide: true }, resolve)
    })
  }
}

app.whenReady().then(async () => {
  const watchdog = setTimeout(() => { cleanup().finally(() => app.exit(1)) }, 60000)
  try {
    await until(() => BrowserWindow.getAllWindows().length === 1, 'shell window')
    const win = BrowserWindow.getAllWindows()[0]
    const evaluate = code => win.webContents.executeJavaScript(code)
    await until(() => evaluate('document.body.classList.contains("workspace-open")'), 'first backend ready')
    const first = children[0]
    assert.ok(first?.workerPid)
    const guest = webContents.getAllWebContents().find(contents => contents.getType() === 'webview')
    const oldGuestId = guest.id
    await guest.loadURL(`http://127.0.0.1:${first.port}/projects/42?tab=timeline`)
    await until(() => evaluate('localStorage.getItem("aivideo.desktopRoute") === "/projects/42?tab=timeline"'), 'route saved')
    fs.writeFileSync(path.join(profile, 'running.png'), (await win.capturePage()).toPNG())

    await evaluate('document.querySelector("#service-stop-btn").click()')
    await until(() => evaluate('document.querySelector("#service-status").textContent === "服务已关闭"'), 'manual stop')
    const status = await evaluate('window.desk.backendStatus()')
    assert.equal(status.enabled, false)
    assert.equal(status.online, false)
    assert.equal(await probeBackend(first.port), false)
    assert.equal(alive(first.workerPid), false)
    await until(() => !webContents.getAllWebContents().some(contents => contents.id === oldGuestId), 'old guest destroyed')
    await sleep(2400)
    assert.equal(children.length, 1, 'stopped service cannot auto-respawn')
    assert.equal(await evaluate('document.querySelector("#service-timer").style.display'), 'none')
    fs.writeFileSync(path.join(profile, 'stopped.png'), (await win.capturePage()).toPNG())
    win.setSize(960, 640)
    await evaluate('document.querySelector("#lang-btn").click()')
    await sleep(150)
    assert.equal(await evaluate(`(() => {
      const brand = document.querySelector('.brand').getBoundingClientRect()
      const controls = document.querySelector('.titlebar-right').getBoundingClientRect()
      return brand.right <= controls.left
    })()`), true, 'service controls fit at minimum window width in English')
    fs.writeFileSync(path.join(profile, 'stopped-en.png'), (await win.capturePage()).toPNG())
    await evaluate('document.querySelector("#lang-btn").click()')
    win.setSize(1280, 840)

    await evaluate('document.querySelector("#service-start-btn").click()')
    await until(() => evaluate('document.body.classList.contains("workspace-open")'), 'restart workspace')
    assert.equal(children.length, 2)
    const restored = webContents.getAllWebContents().find(contents => contents.getType() === 'webview')
    assert.notEqual(restored.id, oldGuestId)
    await until(() => restored.getURL().endsWith('/projects/42?tab=timeline'), 'route restored')
    assert.ok(await probeBackend(children[1].port))
    await evaluate('window.desk.stopBackend()')
    assert.equal(await probeBackend(children[1].port), false)
    const result = { passed: true, checks: ['owned process tree stopped', 'worker terminated',
      'polling disabled', 'guest destroyed', 'service restarted', 'project route restored'] }
    fs.writeFileSync(path.join(profile, 'result.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result))
    clearTimeout(watchdog)
    app.quit()
  } catch (error) {
    console.error(error)
    clearTimeout(watchdog)
    await cleanup()
    app.exit(1)
  }
})
