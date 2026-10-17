/**
 * 桌面壳 UI —— 面板化交互模型：
 *  自定义标题栏（Logo / 系统管理开关 / 中英切换 / 窗口按钮）
 *  侧栏（去重后三项）：工作台 / 系统管理 / 打开 ComfyUI
 *  两个主区视图：
 *    - boot-view（系统视图）：安装进度英雄卡 + 打开应用；系统分区收在右侧面板
 *    - app-view（应用视图）：<webview> 全屏承载创作工作台，app-bar 可返回启动台
 *  系统面板 #system-panel：右侧抽屉，含服务状态卡 / 安装信息 / 终端 / 更新；
 *    boot 视图自动展开，app 视图默认收起、可悬浮展开——任何系统操作都不离开应用视图
 *  ready 后默认进入应用视图；返回启动台不销毁 webview，路由记忆恢复
 * 中英 i18n：全局 t(key)
 */
(function () {
  'use strict'

  // ---------------- i18n ----------------
  const I18N = {
    zh: {
      appName: 'AI 动画导演工作台',
      workspace: '你的工作空间',
      workspaceSub: '后台正在忙，完成后可以操作',
      workspaceSubReady: '工作空间已就绪',
      workspaceNav: '工作台',
      openComfyUI: '打开 ComfyUI', terminal: '终端', close: '关闭',
      starting: '正在启动', installing: '正在安装环境与模型',
      installInfo: '安装信息',
      enginePath: '引擎', comfyPath: 'ComfyUI', runtimePath: '运行目录',
      createShortcut: '创建桌面快捷方式', shortcutReady: '快捷方式已就绪',
      shortcutDone: '快捷方式已创建',
      copy: '复制', export: '导出报告', clear: '清空',
      terminalHint: '这里显示最近的输出；复制或导出可获取完整日志。',
      openApp: '打开应用', backToBoot: '返回启动台',
      statusPreparing: '准备程序文件', statusPython: '准备 Python 环境',
      statusTorch: '安装 PyTorch', statusDeps: '安装依赖',
      statusModels: '下载模型', statusStarting: '启动服务',
      statusReady: '已就绪', statusError: '出错了',
      checkingUpdate: '正在检查更新…', updateAvailable: '发现新版本',
      updateNone: '已是最新版本', updateDownloading: '正在下载更新',
      updateReady: '更新已就绪，重启后生效', updateInstall: '重启并安装',
      updateDownload: '下载更新', updateError: '检查更新失败',
      lang: '中文',
      retry: '重试',
      system: '系统管理', checkUpdate: '检查更新', waitingBackend: '正在等待本地服务…',
      disconnected: '连接暂不可用，正在自动重连。可重试或打开系统管理查看终端日志。',
      hardware: '检测 GPU', detecting: '检测中…',
    },
    en: {
      appName: 'AI Anime Director Studio',
      workspace: 'Your Workspace',
      workspaceSub: 'Working in the background. Ready soon.',
      workspaceSubReady: 'Workspace is ready',
      workspaceNav: 'Workspace',
      openComfyUI: 'Open ComfyUI', terminal: 'Terminal', close: 'Close',
      starting: 'Starting', installing: 'Installing environment & models',
      installInfo: 'Installation Info',
      enginePath: 'Engine', comfyPath: 'ComfyUI', runtimePath: 'Runtime',
      createShortcut: 'Create Desktop Shortcut', shortcutReady: 'Shortcut ready',
      shortcutDone: 'Shortcut created',
      copy: 'Copy', export: 'Export', clear: 'Clear',
      terminalHint: 'Recent output shown here. Copy or export for the full log.',
      openApp: 'Open App', backToBoot: 'Back to Launcher',
      statusPreparing: 'Preparing files', statusPython: 'Setting up Python',
      statusTorch: 'Installing PyTorch', statusDeps: 'Installing dependencies',
      statusModels: 'Downloading models', statusStarting: 'Starting service',
      statusReady: 'Ready', statusError: 'Error',
      checkingUpdate: 'Checking for updates…', updateAvailable: 'New version available',
      updateNone: 'Up to date', updateDownloading: 'Downloading update',
      updateReady: 'Update ready. Restart to apply.', updateInstall: 'Restart & Install',
      updateDownload: 'Download Update', updateError: 'Update check failed',
      lang: 'EN',
      retry: 'Retry',
      system: 'System', checkUpdate: 'Check Updates', waitingBackend: 'Waiting for local service…',
      disconnected: 'Connection unavailable. Reconnecting automatically. Retry or open System to inspect logs.',
      hardware: 'Detect GPU', detecting: 'Detecting…',
    },
  }
  let lang = localStorage.getItem('aivideo.lang') || 'zh'
  const t = (k) => (I18N[lang] && I18N[lang][k]) || I18N.zh[k] || k

  // ---------------- 状态 ----------------
  const state = {
    bootState: 'preparing',   // preparing|python|torch|deps|models|starting|ready|error
    progress: 0.04,
    view: 'boot',             // boot（系统视图） | app（应用视图）
    panelOpen: false,         // 系统面板抽屉是否展开
    logs: [],
    shortcutDone: false,
    update: null,             // { phase, info, percent }
    maximized: false,
    paths: null,
    appUrl: 'http://127.0.0.1:8000',
    loadedOrigin: '',
    loadFailed: false,
    online: false,
    route: localStorage.getItem('aivideo.desktopRoute') || '/',
  }

  const MAX_LOGS = 500

  // ---------------- DOM ----------------
  const $ = (sel) => document.querySelector(sel)
  const el = {
    langBtn: $('#lang-btn'),
    winMin: $('#win-min'), winMax: $('#win-max'), winClose: $('#win-close'),
    navItems: document.querySelectorAll('.nav-item'),
    workspaceTitle: $('#workspace-title'),
    workspaceSub: $('#workspace-sub'),
    installInfo: $('#install-info'),
    enginePath: $('#engine-path'), comfyPath: $('#comfy-path'), runtimePath: $('#runtime-path'),
    shortcutBtn: $('#shortcut-btn'),
    terminal: $('#terminal'),
    terminalHint: $('#terminal-hint'),
    logBox: $('#log-box'),
    logCopy: $('#log-copy'), logExport: $('#log-export'), logClear: $('#log-clear'),
    actionBtn: $('#action-btn'),
    openAppBtn: $('#open-app-btn'),
    updateBar: $('#update-bar'),
    updateText: $('#update-text'),
    updateBtn: $('#update-btn'),
    versionTag: $('#version-tag'),
    webviewWrap: $('#webview-wrap'),
    webview: $('#app-webview'),
    backBtn: $('#back-btn'),
    bootView: $('#boot-view'),
    systemBtn: $('#system-btn'),
    systemPanel: $('#system-panel'),
    panelCloseBtn: $('#panel-close-btn'),
    serviceTimer: $('#service-timer'),
    connectionCard: $('#connection-card'),
    connectionMessage: $('#connection-message'),
  }

  // ---------------- 渲染 ----------------
  function applyStaticI18n() {
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en'
    document.title = t('appName')
    el.langBtn.textContent = t('lang')
    el.workspaceTitle.textContent = t('workspace')
    el.shortcutBtn.textContent = state.shortcutDone ? '✓ ' + t('shortcutReady') : t('createShortcut')
    el.logCopy.textContent = t('copy')
    el.logExport.textContent = t('export')
    el.logClear.textContent = t('clear')
    el.terminalHint.textContent = t('terminalHint')
    el.openAppBtn.textContent = t('openApp')
    el.backBtn.textContent = t('backToBoot')
    document.querySelectorAll('[data-i18n]').forEach((n) => {
      const k = n.getAttribute('data-i18n')
      if (I18N[lang][k]) n.textContent = I18N[lang][k]
    })
    renderProgress()
    renderUpdate()
    el.connectionMessage.textContent = t('disconnected')
  }

  const STATE_TEXT = {
    preparing: 'statusPreparing', python: 'statusPython', torch: 'statusTorch',
    deps: 'statusDeps', models: 'statusModels', starting: 'statusStarting',
    ready: 'statusReady', error: 'statusError',
  }

  /** 双卡同步：boot 视图英雄卡 + 系统面板服务卡渲染同一份状态 */
  function renderProgress() {
    const s = state.bootState
    const stateText = t(STATE_TEXT[s] || 'statusPreparing')
    for (const card of document.querySelectorAll('.progress-card')) {
      const icon = card.querySelector('.progress-icon')
      const text = card.querySelector('.progress-text')
      const desc = card.querySelector('.progress-desc')
      const bar = card.querySelector('.progress-bar')
      if (icon) icon.textContent = s === 'ready' ? '✓' : (s === 'error' ? '⚠' : 'F')
      if (text) text.textContent = stateText
      if (desc) desc.textContent = state.online ? t('workspaceSubReady') : t('disconnected')
      if (bar) bar.style.width = Math.round(state.progress * 100) + '%'
      card.classList.toggle('done', s === 'ready')
      card.classList.toggle('error', s === 'error')
    }
    el.openAppBtn.disabled = !state.online
    el.actionBtn.textContent = s === 'error' ? t('retry') : stateText
    el.actionBtn.classList.toggle('busy', s !== 'ready' && s !== 'error')
    el.workspaceSub.textContent = s === 'ready' ? t('workspaceSubReady') : t('workspaceSub')
    el.openAppBtn.classList.toggle('pulse', s === 'ready')
    // 服务计时器：启动/重连期间显示已耗时，就绪后隐藏
    el.serviceTimer.style.display = s === 'ready' ? 'none' : ''
  }

  function renderTerminal() {
    // 只渲染尾部，避免大数据量卡顿
    const lines = state.logs.slice(-MAX_LOGS)
    el.logBox.textContent = lines.map((l) => l.text).join('\n')
    el.logBox.scrollTop = el.logBox.scrollHeight
  }

  function renderUpdate() {
    const u = state.update
    if (!u) {
      el.updateBar.style.display = 'none'
      return
    }
    el.updateBar.style.display = 'flex'
    let txt = ''
    if (u.phase === 'checking') txt = t('checkingUpdate')
    else if (u.phase === 'available') txt = t('updateAvailable') + (u.info && u.info.version ? ` v${u.info.version}` : '')
    else if (u.phase === 'none') txt = t('updateNone')
    else if (u.phase === 'downloading') txt = `${t('updateDownloading')} ${u.percent || 0}%`
    else if (u.phase === 'downloaded') txt = t('updateReady')
    else if (u.phase === 'error') txt = t('updateError')
    el.updateText.textContent = txt
    if (u.phase === 'available') {
      el.updateBtn.textContent = t('updateDownload')
      el.updateBtn.style.display = ''
    } else if (u.phase === 'downloaded') {
      el.updateBtn.textContent = t('updateInstall')
      el.updateBtn.style.display = ''
    } else {
      el.updateBtn.style.display = 'none'
    }
  }

  /** 侧栏高亮：应用视图亮「工作台」，面板展开亮「系统管理」 */
  function renderNav() {
    el.navItems.forEach((n) => {
      const action = n.getAttribute('data-action')
      const active = (action === 'workspace' && state.view === 'app') ||
        (action === 'system' && state.panelOpen)
      n.classList.toggle('active', active)
    })
  }

  function setView(v) {
    const changed = state.view !== v
    state.view = v
    el.bootView.style.display = v === 'boot' ? '' : 'none'
    el.webviewWrap.style.display = v === 'app' ? '' : 'none'
    document.body.classList.toggle('workspace-open', v === 'app')
    // 视图切换时同步面板：boot 即系统视图（自动展开）；app 默认收起（可再手动展开）
    if (changed) setPanel(v === 'boot')
    if (v === 'app' && state.online && (state.loadedOrigin !== state.appUrl || state.loadFailed)) {
      // Keep the guest alive while system management is open; never reset a healthy guest.
      const route = /^\/(?!\/)[^\\]*$/.test(state.route) ? state.route : '/'
      el.webview.src = state.appUrl + route
      state.loadedOrigin = state.appUrl
      state.loadFailed = false
    }
    renderNav()
  }

  /** 系统面板抽屉开关：任何系统操作都不切换主视图（应用视图上悬浮） */
  function setPanel(open) {
    state.panelOpen = open
    el.systemPanel.classList.toggle('open', open)
    el.systemPanel.setAttribute('aria-hidden', String(!open))
    el.systemBtn.setAttribute('aria-expanded', String(open))
    el.systemBtn.classList.toggle('active', open)
    renderNav()
  }

  function showConnection(show) {
    el.connectionCard.hidden = !show
    el.connectionMessage.textContent = t('disconnected')
  }

  function backendReady(url) {
    const wasOnline = state.online
    state.online = true
    state.appUrl = url
    state.bootState = 'ready'
    state.progress = 1
    renderProgress()
    showConnection(false)
    // 默认进入创作工作台：首次就绪或从断线恢复且用户正停留在应用视图时
    if (!state.loadedOrigin || (!wasOnline && state.view === 'app')) setView('app')
  }

  async function retryBackend() {
    try {
      await window.desk.retryBackend()
      const status = await window.desk.backendStatus()
      if (status.online) {
        backendReady(`http://127.0.0.1:${status.port}`)
        if (state.loadFailed) setView('app')
      }
    } catch (error) {
      state.logs.push({ text: String(error), ts: Date.now() })
      renderTerminal()
    }
  }

  $('#retry-backend-btn').addEventListener('click', retryBackend)
  $('#reconnect-btn').addEventListener('click', retryBackend)
  $('#check-update-btn').addEventListener('click', () => window.desk.checkUpdate())
  $('#hardware-btn').addEventListener('click', async () => {
    const button = $('#hardware-btn')
    button.disabled = true
    $('#hardware-info').textContent = t('detecting')
    try {
      const h = await window.desk.hardware()
      $('#hardware-info').textContent = `${h.gpu_name} · VRAM ${h.vram_gb} GB · RAM ${h.ram_gb} GB · ${h.device}`
    } catch (error) {
      $('#hardware-info').textContent = String(error.message || error)
    } finally { button.disabled = false }
  })
  const rememberRoute = ({ url }) => {
    try {
      const u = new URL(url)
      if (u.origin !== state.appUrl) return
      state.route = u.pathname + u.search + u.hash
      localStorage.setItem('aivideo.desktopRoute', state.route)
    } catch {}
  }
  el.webview.addEventListener('did-navigate', rememberRoute)
  el.webview.addEventListener('did-navigate-in-page', rememberRoute)
  el.webview.addEventListener('did-fail-load', event => {
    if (event.isMainFrame === false || event.errorCode === -3) return
    state.loadFailed = true
    showConnection(true)
    state.logs.push({ text: `工作台加载失败：${event.errorDescription}`, ts: Date.now() })
    renderTerminal()
  })
  el.webview.addEventListener('render-process-gone', () => {
    state.loadFailed = true
    showConnection(true)
  })
  el.webview.addEventListener('did-finish-load', () => showConnection(!state.online))

  // ---------------- 事件：窗口 ----------------
  el.langBtn.addEventListener('click', () => {
    lang = lang === 'zh' ? 'en' : 'zh'
    localStorage.setItem('aivideo.lang', lang)
    applyStaticI18n()
  })
  el.winMin.addEventListener('click', () => window.desk.minimize())
  el.winMax.addEventListener('click', async () => {
    state.maximized = await window.desk.toggleMaximize()
    document.body.classList.toggle('maximized', state.maximized)
  })
  el.winClose.addEventListener('click', () => window.desk.close())

  // ---------------- 事件：系统面板（不切换主视图） ----------------
  el.systemBtn.addEventListener('click', () => setPanel(!state.panelOpen))
  el.panelCloseBtn.addEventListener('click', () => setPanel(false))

  // ---------------- 事件：侧栏（去重后三项） ----------------
  el.navItems.forEach((n) => {
    n.addEventListener('click', () => {
      const action = n.getAttribute('data-action')
      if (action === 'workspace') {
        // 离线时没有工作台可回：回系统视图展示进度（面板随视图自动展开）
        if (state.online) setView('app')
        else setView('boot')
      }
      if (action === 'system') setPanel(!state.panelOpen)
      if (action === 'comfy') window.desk.openComfyUI()
    })
  })

  // ---------------- 事件：按钮 ----------------
  el.shortcutBtn.addEventListener('click', async () => {
    const r = await window.desk.createShortcut()
    if (r.ok) {
      state.shortcutDone = true
      applyStaticI18n()
    } else {
      alert((lang === 'zh' ? '创建快捷方式失败：' : 'Failed to create shortcut: ') + (r.error || ''))
    }
  })
  el.openAppBtn.addEventListener('click', () => {
    if (state.online && state.bootState === 'ready') setView('app')
  })
  el.actionBtn.addEventListener('click', () => {
    if (state.bootState === 'error') retryBackend()
  })
  el.backBtn.addEventListener('click', () => setView('boot'))
  el.logCopy.addEventListener('click', () => {
    const text = state.logs.map((l) => l.text).join('\n')
    navigator.clipboard.writeText(text).then(() => {
      el.logCopy.textContent = lang === 'zh' ? '已复制' : 'Copied'
      setTimeout(applyStaticI18n, 1200)
    })
  })
  el.logExport.addEventListener('click', () => {
    window.desk.saveLog(state.logs.map((l) => `[${new Date(l.ts).toISOString()}] ${l.text}`).join('\n'))
  })
  el.logClear.addEventListener('click', () => {
    state.logs = []
    renderTerminal()
  })

  // ---------------- 事件：更新 ----------------
  el.updateBtn.addEventListener('click', async () => {
    const u = state.update
    if (!u) return
    if (u.phase === 'available') await window.desk.downloadUpdate()
    else if (u.phase === 'downloaded') window.desk.installUpdate()
  })

  // ---------------- 后端事件 ----------------
  window.desk.on('backend:log', ({ text, ts }) => {
    state.logs.push({ text, ts })
    if (state.logs.length > 4000) state.logs.splice(0, state.logs.length - 4000)
    renderTerminal()
  })
  window.desk.on('backend:state', ({ state: s, progress }) => {
    state.bootState = s
    if (typeof progress === 'number') state.progress = progress
    renderProgress()
  })
  window.desk.on('backend:ready', ({ url }) => {
    backendReady(url)
  })
  window.desk.on('backend:offline', () => {
    state.online = false
    state.bootState = 'starting'
    renderProgress()
    showConnection(true)
  })
  window.desk.on('backend:error', ({ text }) => {
    state.bootState = 'error'
    renderProgress()
    state.logs.push({ text: '[ERROR] ' + text, ts: Date.now() })
    renderTerminal()
  })
  window.desk.on('backend:port', ({ port }) => { state.appUrl = `http://127.0.0.1:${port}` })
  window.desk.on('backend:tick', ({ seconds }) => {
    // 服务计时器（启动/重连期间的已耗时，MM:SS）
    const total = Number(seconds) || 0
    el.serviceTimer.textContent =
      String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0')
    el.serviceTimer.style.display = state.bootState === 'ready' ? 'none' : ''
  })

  // ---------------- 更新事件 ----------------
  window.desk.on('update:checking', () => { state.update = { phase: 'checking' }; renderUpdate() })
  window.desk.on('update:available', (info) => { state.update = { phase: 'available', info }; renderUpdate() })
  window.desk.on('update:none', () => { state.update = { phase: 'none' }; renderUpdate() })
  window.desk.on('update:progress', (p) => {
    state.update = { phase: 'downloading', percent: Math.round(p.percent || 0) }
    renderUpdate()
  })
  window.desk.on('update:downloaded', (info) => { state.update = { phase: 'downloaded', info }; renderUpdate() })
  window.desk.on('update:error', () => { state.update = { phase: 'error' }; renderUpdate() })

  // ---------------- 启动 ----------------
  async function boot() {
    applyStaticI18n()
    // 初始即系统视图：面板自动展开（安装进度/终端/安装信息直接可见）
    setPanel(true)
    try {
      state.paths = await window.desk.paths()
      if (state.paths) {
        el.runtimePath.textContent = state.paths.runtime
        el.enginePath.textContent = 'AI 视频工坊 · AnimeDiff / Wan 2.2 / ComfyUI'
        el.comfyPath.textContent = 'http://127.0.0.1:8188'
      }
      el.versionTag.textContent = 'v' + (await window.desk.version())
      const status = await window.desk.backendStatus()
      if (status.online) backendReady(`http://127.0.0.1:${status.port}`)
    } catch { /* ignore */ }
    // 手动检查一次更新（设置页可见反馈）
    setTimeout(() => window.desk.checkUpdate(), 1500)
    renderTerminal()
  }
  boot()
})()
