/**
 * 桌面壳 renderer UI 单测（jsdom，node --test 可真实执行）：
 *  - 侧边栏去重（工作台/系统管理/打开 ComfyUI）
 *  - 系统面板为右侧抽屉：boot 视图自动展开，app 视图悬浮展开、不离开工作台
 *  - ready 后默认进入创作工作台；返回启动台保留 webview 与路由记忆
 *  - 双卡状态同步、服务计时器、离线重连、i18n 切换
 * 说明：jsdom 无布局引擎，样式行为（transform/display 计算值）由 electron-smoke.js
 * 在真实 Electron 中验证；这里只断言 DOM 状态与交互逻辑。
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { JSDOM } = require('jsdom')

const RENDERER = path.resolve(__dirname, '..', 'renderer')
const HTML = fs.readFileSync(path.join(RENDERER, 'index.html'), 'utf8')
const APP = fs.readFileSync(path.join(RENDERER, 'app.js'), 'utf8')

const flush = async () => {
  for (let i = 0; i < 5; i++) await new Promise(resolve => setTimeout(resolve, 0))
}

/** 忠实 stub preload 暴露的 desk 桥（main.js IPC 面的镜像） */
function makeDesk() {
  const listeners = new Map()
  const calls = []
  const desk = {
    statusOnline: false,
    serviceEnabled: true,
    serviceOwned: true,
    calls,
    on(channel, cb) {
      if (!listeners.has(channel)) listeners.set(channel, [])
      listeners.get(channel).push(cb)
    },
    emit(channel, payload) {
      for (const cb of listeners.get(channel) || []) cb(payload)
    },
    async paths() { return { runtime: 'D:/rt', launcher: 'l.exe', userData: 'ud', logs: 'lg' } },
    async version() { return '9.9.9' },
    async backendStatus() { return { online: desk.statusOnline, port: 8000, enabled: desk.serviceEnabled, owned: desk.serviceOwned } },
    async retryBackend() { return { online: true, port: 8000 } },
    async startBackend() { calls.push('startBackend'); desk.serviceEnabled = true; return { online: false } },
    async stopBackend() { calls.push('stopBackend'); desk.statusOnline = false; desk.serviceEnabled = false; return { online: false } },
    async checkUpdate() { calls.push('checkUpdate') },
    async hardware() { return { gpu_name: 'Fake', vram_gb: 8, ram_gb: 32, device: 'cuda' } },
    async minimize() {}, async toggleMaximize() { return false }, async close() {},
    async createShortcut() { return { ok: true } }, async saveLog() { return { ok: true } },
    async openComfyUI() { calls.push('openComfyUI') },
    async downloadUpdate() {}, async installUpdate() {},
  }
  return desk
}

/** 加载桌面壳：index.html + app.js，url 用 http 使 localStorage 可用 */
async function loadShell(seedRoute) {
  const desk = makeDesk()
  const dom = new JSDOM(HTML, {
    url: 'http://127.0.0.1/',
    runScripts: 'dangerously',
    beforeParse(window) {
      window.desk = desk
      if (seedRoute) window.localStorage.setItem('aivideo.desktopRoute', seedRoute)
    },
  })
  dom.window.eval(APP)
  await flush()
  return { dom, desk, doc: dom.window.document }
}

test('sidebar is de-duplicated to workspace/system/comfy', async (t) => {
  const { dom, doc } = await loadShell()
  t.after(() => dom.window.close())
  const items = [...doc.querySelectorAll('.nav-item')]
  assert.deepEqual(items.map(n => n.getAttribute('data-action')), ['workspace', 'system', 'comfy'])
  // 旧入口（启动/安装/正在处理/终端/设置）已移除，不再与前端创作侧边栏语义重叠
  for (const gone of ['start', 'install', 'processing', 'terminal', 'settings']) {
    assert.equal(doc.querySelector(`.nav-item[data-action="${gone}"]`), null, gone)
  }
})

test('system panel carries every system section and opens on the launcher view', async (t) => {
  const { dom, doc } = await loadShell()
  t.after(() => dom.window.close())
  const panel = doc.querySelector('#system-panel')
  for (const sel of ['#panel-status', '#install-info', '#terminal', '#update-bar']) {
    assert.ok(panel.querySelector(sel), sel)
  }
  // boot 视图即系统视图：面板自动展开（安装进度/终端/安装信息直接可见）
  assert.equal(panel.classList.contains('open'), true)
  assert.equal(panel.getAttribute('aria-hidden'), 'false')
  assert.equal(doc.querySelector('#system-btn').getAttribute('aria-expanded'), 'true')
  assert.equal(doc.querySelector('#service-timer').style.display, '')
})

test('launcher boot fills install info and version', async (t) => {
  const { dom, doc } = await loadShell()
  t.after(() => dom.window.close())
  assert.equal(doc.querySelector('#runtime-path').textContent, 'D:/rt')
  assert.equal(doc.querySelector('#version-tag').textContent, 'v9.9.9')
  assert.equal(doc.querySelector('#engine-path').textContent.includes('Wan 2.2'), true)
})

test('backend ready enters the workspace by default', async (t) => {
  const { dom, desk, doc } = await loadShell()
  t.after(() => dom.window.close())
  assert.equal(doc.body.classList.contains('workspace-open'), false)
  assert.equal(doc.querySelector('#open-app-btn').disabled, true)
  desk.emit('backend:ready', { url: 'http://127.0.0.1:8000' })
  assert.equal(doc.body.classList.contains('workspace-open'), true)
  assert.equal(doc.querySelector('#boot-view').style.display, 'none')
  assert.equal(doc.querySelector('#webview-wrap').style.display, '')
  assert.equal(doc.querySelector('#app-webview').src, 'http://127.0.0.1:8000/')
  assert.equal(doc.querySelector('#open-app-btn').disabled, false)
  // 面板随应用视图收起；侧栏高亮工作台
  assert.equal(doc.querySelector('#system-panel').classList.contains('open'), false)
  assert.equal(doc.querySelector('.nav-item[data-action="workspace"]').classList.contains('active'), true)
})

test('system button toggles the panel without leaving the workspace', async (t) => {
  const { dom, desk, doc } = await loadShell()
  t.after(() => dom.window.close())
  desk.emit('backend:ready', { url: 'http://127.0.0.1:8000' })
  const src = doc.querySelector('#app-webview').src
  doc.querySelector('#system-btn').click()
  assert.equal(doc.querySelector('#system-panel').classList.contains('open'), true)
  assert.equal(doc.querySelector('#system-btn').getAttribute('aria-expanded'), 'true')
  assert.equal(doc.querySelector('.nav-item[data-action="system"]').classList.contains('active'), true)
  // 任何系统操作都不离开应用视图，webview 不重载
  assert.equal(doc.body.classList.contains('workspace-open'), true)
  assert.equal(doc.querySelector('#webview-wrap').style.display, '')
  assert.equal(doc.querySelector('#app-webview').src, src, 'webview must not reload')
  // 侧栏「系统管理」是同一个开关
  doc.querySelector('.nav-item[data-action="system"]').click()
  assert.equal(doc.querySelector('#system-panel').classList.contains('open'), false)
  doc.querySelector('.nav-item[data-action="system"]').click()
  assert.equal(doc.querySelector('#system-panel').classList.contains('open'), true)
  // 面板关闭按钮
  doc.querySelector('#panel-close-btn').click()
  assert.equal(doc.querySelector('#system-panel').classList.contains('open'), false)
  assert.equal(doc.querySelector('#system-btn').getAttribute('aria-expanded'), 'false')
  assert.equal(doc.querySelector('#app-webview').src, src)
})

test('returning to the launcher keeps the webview and restores it on re-entry', async (t) => {
  const { dom, desk, doc } = await loadShell()
  t.after(() => dom.window.close())
  desk.emit('backend:ready', { url: 'http://127.0.0.1:8000' })
  const src = doc.querySelector('#app-webview').src
  doc.querySelector('#back-btn').click()
  assert.equal(doc.querySelector('#webview-wrap').style.display, 'none')
  assert.equal(doc.querySelector('#boot-view').style.display, '')
  // 启动台即系统视图：面板自动展开
  assert.equal(doc.querySelector('#system-panel').classList.contains('open'), true)
  assert.equal(doc.querySelector('#app-webview').src, src, 'webview element survives')
  doc.querySelector('#open-app-btn').click()
  assert.equal(doc.querySelector('#webview-wrap').style.display, '')
  assert.equal(doc.querySelector('#app-webview').src, src, 'same guest, no reload')
})

test('in-page navigation is remembered and restored on the next launch', async (t) => {
  const first = await loadShell()
  t.after(() => first.dom.window.close())
  first.desk.emit('backend:ready', { url: 'http://127.0.0.1:8000' })
  const event = new first.dom.window.Event('did-navigate-in-page')
  event.url = 'http://127.0.0.1:8000/projects/42?tab=timeline'
  first.doc.querySelector('#app-webview').dispatchEvent(event)
  assert.equal(first.dom.window.localStorage.getItem('aivideo.desktopRoute'), '/projects/42?tab=timeline')

  // 新会话（同一持久化存储）：路由记忆恢复
  const second = await loadShell('/projects/42?tab=timeline')
  t.after(() => second.dom.window.close())
  second.desk.emit('backend:ready', { url: 'http://127.0.0.1:8000' })
  assert.equal(second.doc.querySelector('#app-webview').src, 'http://127.0.0.1:8000/projects/42?tab=timeline')
})

test('progress renders synchronously on the hero card and the panel service card', async (t) => {
  const { dom, desk, doc } = await loadShell()
  t.after(() => dom.window.close())
  desk.emit('backend:state', { state: 'models', progress: 0.5 })
  const cards = [...doc.querySelectorAll('.progress-card')]
  assert.equal(cards.length, 2)
  for (const card of cards) {
    assert.equal(card.querySelector('.progress-text').textContent, '下载模型')
    assert.equal(card.querySelector('.progress-bar').style.width, '50%')
    assert.equal(card.querySelector('.progress-desc').textContent.includes('连接暂不可用'), true)
  }
  assert.equal(doc.querySelector('#action-btn').textContent, '下载模型')
  assert.equal(doc.querySelector('#action-btn').classList.contains('busy'), true)
  // 服务计时器（启动期间已耗时）
  desk.emit('backend:tick', { seconds: 65 })
  assert.equal(doc.querySelector('#service-timer').textContent, '01:05')
  assert.equal(doc.querySelector('#service-timer').style.display, '')
  // 就绪后双卡同步为完成态，计时器隐藏
  desk.emit('backend:ready', { url: 'http://127.0.0.1:8000' })
  assert.equal(doc.querySelector('#service-timer').style.display, 'none')
  for (const card of doc.querySelectorAll('.progress-card')) {
    assert.equal(card.querySelector('.progress-text').textContent, '已就绪')
    assert.equal(card.classList.contains('done'), true)
    assert.equal(card.querySelector('.progress-bar').style.width, '100%')
  }
})

test('offline shows the reconnect overlay; the workspace nav falls back to the launcher', async (t) => {
  const { dom, desk, doc } = await loadShell()
  t.after(() => dom.window.close())
  desk.emit('backend:ready', { url: 'http://127.0.0.1:8000' })
  assert.equal(doc.querySelector('#connection-card').hidden, true)
  desk.emit('backend:offline', {})
  assert.equal(doc.querySelector('#connection-card').hidden, false)
  assert.equal(doc.querySelector('#connection-message').textContent.includes('连接暂不可用'), true)
  // 离线时没有工作台可回：侧栏「工作台」回系统视图（面板随视图自动展开）
  doc.querySelector('.nav-item[data-action="workspace"]').click()
  assert.equal(doc.querySelector('#boot-view').style.display, '')
  assert.equal(doc.querySelector('#system-panel').classList.contains('open'), true)
  desk.emit('backend:ready', { url: 'http://127.0.0.1:8000' })
  assert.equal(doc.querySelector('#connection-card').hidden, true)
})

test('retry from the error state reconnects and enters the workspace', async (t) => {
  const { dom, desk, doc } = await loadShell()
  t.after(() => dom.window.close())
  desk.statusOnline = true
  desk.emit('backend:error', { text: 'boom' })
  assert.equal(doc.querySelector('#action-btn').textContent, '重试')
  assert.equal(doc.querySelector('#progress-card').classList.contains('error'), true)
  doc.querySelector('#action-btn').click()
  await flush()
  assert.equal(doc.body.classList.contains('workspace-open'), true)
})

test('language toggle switches the shell copy', async (t) => {
  const { dom, doc } = await loadShell()
  t.after(() => dom.window.close())
  assert.equal(doc.title, 'AI 动画导演工作台')
  doc.querySelector('#lang-btn').click()
  assert.equal(doc.title, 'AI Anime Director Studio')
  assert.equal(doc.querySelector('.nav-item[data-action="workspace"] span[data-i18n]').textContent, 'Workspace')
  assert.equal(doc.querySelector('#panel-close-btn').textContent, 'Close')
  assert.equal(doc.querySelector('#lang-btn').textContent, 'EN')
  doc.querySelector('#lang-btn').click()
  assert.equal(doc.title, 'AI 动画导演工作台')
})

test('comfy nav item opens ComfyUI through the preload bridge', async (t) => {
  const { dom, desk, doc } = await loadShell()
  t.after(() => dom.window.close())
  doc.querySelector('.nav-item[data-action="comfy"]').click()
  assert.deepEqual(desk.calls, ['openComfyUI'])
})

test('stop releases the guest and restart restores the previous project route', async t => {
  const { dom, desk, doc } = await loadShell('/projects/42?tab=timeline')
  t.after(() => dom.window.close())
  desk.emit('backend:ready', { url: 'http://127.0.0.1:8000', owned: true })
  assert.equal(doc.querySelector('#service-stop-btn').disabled, false)
  doc.querySelector('#service-stop-btn').click()
  await flush()
  assert.deepEqual(desk.calls, ['stopBackend'])
  assert.equal(doc.querySelector('#service-status').textContent, '服务已关闭')
  assert.equal(doc.querySelector('#app-webview'), null, 'guest removed to stop frontend polling')
  assert.equal(doc.querySelector('#service-start-btn').disabled, false)
  assert.equal(doc.querySelector('#service-stop-btn').disabled, true)
  assert.equal(doc.querySelector('#service-timer').style.display, 'none')
  desk.emit('backend:offline', {})
  assert.equal(doc.querySelector('#service-status').textContent, '服务已关闭', 'no reconnect during manual stop')
  doc.querySelector('#service-start-btn').click()
  await flush()
  assert.deepEqual(desk.calls, ['stopBackend', 'startBackend'])
  desk.emit('backend:ready', { url: 'http://127.0.0.1:8001', owned: true })
  assert.equal(doc.querySelector('#app-webview').src, 'http://127.0.0.1:8001/projects/42?tab=timeline')
  assert.equal(doc.body.classList.contains('workspace-open'), true)
})

test('cancelled stop keeps the workspace and duplicate clicks cannot submit twice', async t => {
  const { dom, desk, doc } = await loadShell()
  t.after(() => dom.window.close())
  desk.statusOnline = true
  desk.emit('backend:ready', { url: 'http://127.0.0.1:8000', owned: true })
  let finish
  let calls = 0
  desk.stopBackend = () => { calls++; return new Promise(resolve => { finish = resolve }) }
  doc.querySelector('#service-stop-btn').click()
  doc.querySelector('#service-stop-btn').click()
  assert.equal(calls, 1)
  assert.equal(doc.querySelector('#service-stop-btn').disabled, true)
  finish({ cancelled: true })
  await flush()
  assert.ok(doc.querySelector('#app-webview'))
  assert.equal(doc.body.classList.contains('workspace-open'), true)
  assert.equal(doc.querySelector('#service-stop-btn').disabled, false)
})

test('external service is labelled and stop errors stay visible without a false stopped state', async t => {
  const { dom, desk, doc } = await loadShell()
  t.after(() => dom.window.close())
  desk.emit('backend:ready', { url: 'http://127.0.0.1:8000', owned: false })
  assert.equal(doc.querySelector('#service-status').textContent, '外部服务 · 原窗口关闭')
  assert.equal(doc.querySelector('#service-stop-btn').disabled, true)
  desk.emit('backend:ready', { url: 'http://127.0.0.1:8000', owned: true })
  desk.stopBackend = async () => { throw new Error('关闭失败：Access denied') }
  doc.querySelector('#service-stop-btn').click()
  await flush()
  assert.equal(doc.querySelector('#service-message').hidden, false)
  assert.match(doc.querySelector('#service-message').textContent, /Access denied/)
  assert.equal(doc.querySelector('#service-status').textContent, '服务已开启')
  assert.ok(doc.querySelector('#app-webview'))
})
