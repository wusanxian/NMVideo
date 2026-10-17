// Real Electron integration test. Requires an isolated backend at 127.0.0.1:8000.
// Run through Electron, never through Node. SMOKE_APP can point to packaged app.asar.
const { app, BrowserWindow, webContents } = require('electron')
const path = require('path')
const fs = require('fs')
const assert = require('assert/strict')
const root = path.resolve(__dirname, '../..')
const profile = path.join(root, '.phase1', process.env.SMOKE_PROFILE || 'electron-profile')
fs.mkdirSync(profile, { recursive: true })
app.setPath('userData', profile)
const messages = []
app.on('web-contents-created', (_event, contents) => {
  contents.on('console-message', (_e, level, message) => {
    messages.push({ type: contents.getType(), level, message })
    fs.writeFileSync(path.join(profile, 'console.json'), JSON.stringify(messages, null, 2))
  })
})
let healthOnline = true
let proxy
if (process.env.SMOKE_PROXY_TARGET) {
  const http = require('http')
  proxy = http.createServer((req, res) => {
    if (req.url === '/api/health' && !healthOnline) { res.writeHead(503); res.end('{}'); return }
    const upstream = http.request({ hostname: '127.0.0.1', port: Number(process.env.SMOKE_PROXY_TARGET),
      path: req.url, method: req.method, headers: req.headers }, incoming => {
      res.writeHead(incoming.statusCode, incoming.headers)
      incoming.pipe(res)
    })
    upstream.on('error', () => { res.writeHead(503); res.end('{}') })
    req.pipe(upstream)
  }).listen(8000, '127.0.0.1')
}
require(process.env.SMOKE_APP || '../main')
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(check, label) {
  const end = Date.now() + 45000
  while (Date.now() < end) {
    if (await Promise.race([check(), sleep(10000).then(() => { throw new Error('Unresponsive renderer: ' + label) })])) return
    await sleep(200)
  }
  throw new Error('Timed out: ' + label)
}
app.whenReady().then(async () => {
  const watchdog = setTimeout(() => { console.error('Integration test watchdog expired'); app.exit(1) }, 90000)
  app.once('before-quit', () => clearTimeout(watchdog))
  try {
    await until(() => BrowserWindow.getAllWindows().length === 1, 'shell')
    const win = BrowserWindow.getAllWindows()[0]
    const errors = []
    win.webContents.on('console-message', (_e, level, message) => { if (level === 3) errors.push(message) })
    const evaluate = code => win.webContents.executeJavaScript(code)
    if (process.env.SMOKE_MISSING === '1') {
      await until(() => evaluate('document.querySelector("#progress-card").classList.contains("error")'), 'missing launcher error')
      assert.equal(await evaluate('document.querySelector("#boot-view").style.display'), '')
      await evaluate('document.querySelector("#retry-backend-btn").click()')
      await sleep(500)
      assert.ok(await evaluate('document.querySelector("#log-box").textContent.includes("找不到启动器")'))
      fs.writeFileSync(path.join(profile, 'result.json'), JSON.stringify({ passed: true, checks: ['missing launcher visible error', 'retry remains available'] }))
      app.quit()
      return
    }
    await until(() => evaluate('Boolean(window.desk && document.body.classList.contains("workspace-open"))'), 'backend ready and workspace open')
    const guest = webContents.getAllWebContents().find(wc => wc.getType() === 'webview')
    assert.ok(guest, 'guest attached')
    assert.equal(guest.getLastWebPreferences().nodeIntegration, false)
    assert.equal(guest.getLastWebPreferences().sandbox, true)
    assert.equal(await guest.executeJavaScript('typeof window.desk'), 'undefined')
    assert.equal(await guest.executeJavaScript('typeof require'), 'undefined')
    assert.equal(await evaluate('navigator.permissions.query({name:"clipboard-write"}).then(p=>p.state)'), 'granted', 'log copy remains available')
    assert.equal(await evaluate('navigator.permissions.query({name:"clipboard-read"}).then(p=>p.state)'), 'denied', 'clipboard read remains denied')
    const savedStateFile = path.join(profile, 'test-state.json')
    const savedState = fs.existsSync(savedStateFile) ? JSON.parse(fs.readFileSync(savedStateFile, 'utf8')) : null
    if (process.env.SMOKE_RESTORE === '1') {
      assert.ok(guest.getURL().endsWith(savedState?.route || '/projects?smoke=1'), 'previous route restored: ' + guest.getURL())
      if (savedState) assert.equal(await guest.executeJavaScript('localStorage.getItem("aivideo.currentProject")'), savedState.projectId)
    }
    assert.equal(await evaluate('getComputedStyle(document.querySelector(".layout > .sidebar")).display'), 'none')
    // 系统管理是右侧抽屉：展开不离开应用视图（侧栏保持隐藏、webview 不重建）
    await evaluate(`document.querySelector('#system-btn').click()`)
    assert.ok(await evaluate('document.querySelector("#system-panel").classList.contains("open")'), 'system panel opens')
    assert.equal(await evaluate('getComputedStyle(document.querySelector(".layout > .sidebar")).display'), 'none', 'panel must not leave the workspace')
    assert.equal(await evaluate('document.querySelector("#webview-wrap").style.display'), '', 'app view stays visible')
    await evaluate(`document.querySelector('#hardware-btn').click()`)
    await until(() => evaluate('document.querySelector("#hardware-info").textContent.includes("VRAM")'), 'GPU detection result')
    await evaluate(`document.querySelector('#system-btn').click()`)
    assert.equal(await evaluate('document.querySelector("#system-panel").classList.contains("open")'), false, 'system panel closes')
    if (!savedState) throw new Error('Run through run-electron-smoke.js to prepare an isolated project fixture')
    const project = savedState
    await guest.executeJavaScript(`document.querySelector('#app').__vue_app__.config.globalProperties.$router.isReady()`)
    await guest.executeJavaScript(`document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(project.route)})`)
    await until(() => guest.executeJavaScript('document.querySelector(".proj-name")?.value === "Smoke integration project"'), 'project loaded')
    await until(() => guest.executeJavaScript('document.querySelector(".tabs .active")?.textContent.includes("时间线")'), 'restored timeline tab')
    await guest.executeJavaScript(`document.querySelector('a[href="/projects/${project.projectId}?tab=assets"]').click()`)
    await until(() => guest.executeJavaScript('document.querySelector(".tabs .active")?.textContent.includes("资产库")'), 'sidebar switches tab without reloading')
    await guest.executeJavaScript(`document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(project.route)})`)
    await until(() => guest.executeJavaScript('document.querySelector(".tabs .active")?.textContent.includes("时间线")'), 'return timeline tab')
    await guest.executeJavaScript(`window.__retainedDraft = 'draft'`)
    await evaluate(`document.querySelector('#system-btn').click()`)
    assert.ok(await evaluate('document.querySelector("#system-panel").classList.contains("open")'), 'panel opens over the workspace')
    await sleep(300)
    await evaluate(`document.querySelector('#system-btn').click()`)
    assert.equal(await evaluate('document.querySelector("#system-panel").classList.contains("open")'), false, 'panel closes again')
    assert.equal(await guest.executeJavaScript('window.__retainedDraft'), 'draft', 'system toggle must preserve guest')
    // 返回启动台：webview 不销毁、路由记忆；再进工作台恢复同一 guest
    await evaluate(`document.querySelector('#back-btn').click()`)
    assert.equal(await evaluate('document.querySelector("#webview-wrap").style.display'), 'none', 'launcher visible')
    assert.equal(await evaluate('document.querySelector("#system-panel").classList.contains("open")'), true, 'launcher auto-expands the panel')
    assert.equal(await guest.executeJavaScript('window.__retainedDraft'), 'draft', 'launcher keeps guest alive')
    await evaluate(`document.querySelector('#open-app-btn').click()`)
    assert.equal(await evaluate('document.querySelector("#webview-wrap").style.display'), '', 'workspace restored')
    assert.equal(await guest.executeJavaScript('window.__retainedDraft'), 'draft', 're-entering the workspace keeps guest')
    await sleep(300)
    assert.equal(await evaluate('localStorage.getItem("aivideo.desktopRoute")'), project.route)
    await evaluate(`(() => { try { window.desk.on('arbitrary:event', () => {}) } catch { return true } return false })()`)
      .then(value => assert.equal(value, true, 'preload event whitelist'))
    // Page-initiated navigation must be intercepted by the main process.
    await guest.executeJavaScript(`location.href = 'https://example.com/blocked'`).catch(() => {})
    await sleep(500)
    assert.ok(guest.getURL().startsWith('http://127.0.0.1:8000/'), guest.getURL())
    assert.equal(await guest.executeJavaScript('window.__retainedDraft'), 'draft')
    if (proxy) {
      healthOnline = false
      await until(() => evaluate('!document.querySelector("#connection-card").hidden'), 'offline overlay')
      assert.equal(await guest.executeJavaScript('window.__retainedDraft'), 'draft')
      healthOnline = true
      await until(() => evaluate('document.querySelector("#connection-card").hidden'), 'automatic reconnection')
      assert.equal(await guest.executeJavaScript('window.__retainedDraft'), 'draft', 'reconnection keeps guest state')
    }
    const image = await win.webContents.capturePage()
    fs.writeFileSync(path.join(profile, 'workspace.png'), image.toPNG())
    assert.deepEqual(errors, [], 'no shell renderer errors')
    assert.deepEqual(messages.filter(entry => entry.level === 3 && /(?:Reference|Type|Syntax)Error/.test(entry.message)), [], 'no guest JavaScript runtime errors')
    assert.equal(fs.existsSync(path.join(profile, 'owned-service.json')), false, 'external backend reused')
    fs.writeFileSync(path.join(profile, 'result.json'), JSON.stringify({ passed: true,
      restored: process.env.SMOKE_RESTORE === '1', reconnected: Boolean(proxy),
      checks: ['preload whitelist', 'sandbox', 'single navigation', 'retained draft', 'route persistence', 'external navigation blocked', 'existing backend reused'] }, null, 2))
    console.log('PASS: real Electron preload, sandbox, single navigation, retained state, route persistence, external navigation blocked, existing service reused')
    // Reopening with this profile validates restoration in the next run.
    if (process.env.SMOKE_RESTORE === '1') {
      console.log('PASS: workspace reopened with persisted profile')
    }
    app.quit()
  } catch (error) {
    const guest = webContents.getAllWebContents().find(wc => wc.getType() === 'webview')
    if (guest) {
      fs.writeFileSync(path.join(profile, 'diagnostic.json'), JSON.stringify({ url: guest.getURL(), messages,
        page: await guest.executeJavaScript('({text:document.body.innerText, inputs:[...document.querySelectorAll("input")].map(n=>({class:n.className,value:n.value}))})') }, null, 2))
      const win = BrowserWindow.getAllWindows()[0]
      if (win) fs.writeFileSync(path.join(profile, 'failure.png'), (await win.webContents.capturePage()).toPNG())
    }
    fs.writeFileSync(path.join(profile, 'result.json'), JSON.stringify({ passed: false, error: error.stack }))
    console.error(error.stack)
    app.exit(1)
  }
})
app.on('will-quit', () => { if (proxy) proxy.close() })
