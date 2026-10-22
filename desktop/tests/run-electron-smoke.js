const { spawn, execFile } = require('node:child_process')
const path = require('node:path')
const fs = require('node:fs')
const { probeBackend } = require('../backend-service')
const root = path.resolve(__dirname, '../..')
const packaged = process.argv.includes('--packaged')
const restored = process.argv.includes('--restore')
const proxyTarget = process.argv.find(arg => arg.startsWith('--proxy-target='))?.split('=')[1]
const appDirArg = process.argv.find(arg => arg.startsWith('--app-dir='))?.slice('--app-dir='.length)
const appDir = appDirArg ? path.resolve(root, appDirArg) : path.join(root, 'desktop/dist/win-unpacked')
const profileName = appDirArg ? 'smoke-delivery-012' : (packaged ? 'smoke-packaged' : 'smoke-dev')
const profile = path.join(root, '.phase1', profileName)
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

async function attachPackagedTest() {
  let info
  for (let i = 0; i < 100; i++) {
    try {
      info = await (await fetch('http://127.0.0.1:9235/json/list')).json()
      if (info[0]) break
    } catch {}
    await delay(100)
  }
  if (!info?.[0]) throw new Error('Packaged main-process inspector unavailable')
  const ws = new WebSocket(info[0].webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true })
    ws.addEventListener('error', reject, { once: true })
  })
  let id = 0
  let paused
  const pending = new Map()
  ws.addEventListener('message', event => {
    const data = JSON.parse(event.data)
    if (data.method === 'Debugger.paused') paused = data.params
    const request = pending.get(data.id)
    if (request) {
      pending.delete(data.id)
      data.error ? request.reject(data.error) : request.resolve(data.result)
    }
  })
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const requestId = ++id
    pending.set(requestId, { resolve, reject })
    ws.send(JSON.stringify({ id: requestId, method, params }))
  })
  try {
    await call('Runtime.enable')
    await call('Debugger.enable')
    await call('Runtime.runIfWaitingForDebugger')
    for (let i = 0; i < 100 && !paused; i++) await delay(100)
    if (!paused) throw new Error('Packaged main process did not pause')
    const script = path.join(__dirname, 'electron-smoke.js')
    const result = await call('Debugger.evaluateOnCallFrame', {
      callFrameId: paused.callFrames[0].callFrameId,
      expression: `require(${JSON.stringify(script)})`,
    })
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
    await call('Debugger.resume')
    await call('Debugger.disable')
  } finally { ws.close() }
}

async function main() {
  const port = Number(proxyTarget || 8000)
  if (!Number.isInteger(port) || port < 1 || port > 65535 || !await probeBackend(port)) {
    throw new Error('Start an isolated backend first; pass --proxy-target=8002 for reconnection tests')
  }
  fs.mkdirSync(profile, { recursive: true })
  fs.rmSync(path.join(profile, 'result.json'), { force: true })
  const fixture = path.join(profile, 'test-state.json')
  if (!fs.existsSync(fixture)) {
    // Prepare fixtures before UI polling starts, so the UI test performs no concurrent DB writes.
    const response = await fetch(`http://127.0.0.1:${port}/api/projects`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Smoke integration project' }),
    })
    const project = await response.json()
    if (!response.ok || !project?.id) throw new Error('Could not create isolated project fixture')
    fs.writeFileSync(fixture, JSON.stringify({ projectId: project.id, route: `/projects/${project.id}?tab=timeline&smoke=1` }))
  }
  const env = { ...process.env, SMOKE_PROFILE: profileName, SMOKE_RESTORE: restored ? '1' : '',
    SMOKE_PROXY_TARGET: proxyTarget || '' }
  delete env.ELECTRON_RUN_AS_NODE
  delete env.SMOKE_MISSING
  delete env.SMOKE_APP
  const executable = packaged ? path.join(appDir, 'AI动画导演工作台.exe') : require('electron')
  if (!fs.existsSync(executable)) throw new Error('Build or install the Electron executable before running this test')
  if (packaged) env.SMOKE_APP = path.join(appDir, 'resources/app.asar/main.js')
  if (appDirArg) env.SMOKE_DELIVERY = '1'
  const args = packaged ? ['--inspect-brk=9235'] : [path.join(__dirname, 'electron-smoke.js')]
  const child = spawn(executable, args, { cwd: root, env, stdio: 'inherit', windowsHide: true })
  const ended = new Promise((resolve, reject) => { child.once('exit', resolve); child.once('error', reject) })
  const timeout = setTimeout(() => {
    if (child.exitCode === null && child.pid) execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], () => {})
  }, 120000)
  try {
    if (packaged) await attachPackagedTest()
    const code = await ended
    const result = JSON.parse(fs.readFileSync(path.join(profile, 'result.json'), 'utf8'))
    console.log(JSON.stringify(result, null, 2))
    if (code !== 0 || !result.passed) throw new Error('Electron integration test failed')
  } catch (error) {
    if (child.exitCode === null && child.pid) {
      await new Promise(resolve => execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], resolve))
    }
    throw error
  } finally { clearTimeout(timeout) }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
