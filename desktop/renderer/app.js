/**
 * 桌面壳 UI —— 对齐 FreeVideo 示意图：
 *  自定义标题栏（Logo / 中英切换 / 窗口按钮）
 *  侧栏：启动 / 安装 / 正在处理 / 打开 ComfyUI / 终端 / 设置 / 版本号
 *  主区：工作空间标题 → 进度卡 → 安装信息卡 → 终端面板
 *  ready 后：主区切换为 <webview> 加载本地应用
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
      start: '启动', install: '安装', processing: '正在处理',
      openComfyUI: '打开 ComfyUI', terminal: '终端', settings: '设置',
      starting: '正在启动', installing: '正在安装环境与模型',
      connecting: '已连接你的模型与运行环境。',
      installInfo: '安装信息',
      enginePath: '引擎', comfyPath: 'ComfyUI', runtimePath: '运行目录',
      createShortcut: '创建桌面快捷方式', shortcutReady: '快捷方式已就绪',
      shortcutDone: '快捷方式已创建',
      processingBtn: '正在处理...', pauseBtn: '暂停',
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
      lang: '中文', version: '版本',
      retry: '重试', stop: '停止',
    },
    en: {
      appName: 'AI Anime Director Studio',
      workspace: 'Your Workspace',
      workspaceSub: 'Working in the background. Ready soon.',
      workspaceSubReady: 'Workspace is ready',
      start: 'Start', install: 'Install', processing: 'Processing',
      openComfyUI: 'Open ComfyUI', terminal: 'Terminal', settings: 'Settings',
      starting: 'Starting', installing: 'Installing environment & models',
      connecting: 'Models and runtime connected.',
      installInfo: 'Installation Info',
      enginePath: 'Engine', comfyPath: 'ComfyUI', runtimePath: 'Runtime',
      createShortcut: 'Create Desktop Shortcut', shortcutReady: 'Shortcut ready',
      shortcutDone: 'Shortcut created',
      processingBtn: 'Processing...', pauseBtn: 'Pause',
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
      lang: 'EN', version: 'Version',
      retry: 'Retry', stop: 'Stop',
    },
  }
  let lang = localStorage.getItem('aivideo.lang') || 'zh'
  const t = (k) => (I18N[lang] && I18N[lang][k]) || I18N.zh[k] || k

  // ---------------- 状态 ----------------
  const state = {
    bootState: 'preparing',   // preparing|python|torch|deps|models|starting|ready|error
    progress: 0.04,
    seconds: 0,
    view: 'boot',             // boot | app
    logs: [],
    shortcutDone: false,
    update: null,             // { phase, info, percent }
    maximized: false,
    paths: null,
    appUrl: 'http://127.0.0.1:8000',
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
    progressCard: $('#progress-card'),
    progressIcon: $('#progress-icon'),
    progressText: $('#progress-text'),
    progressDesc: $('#progress-desc'),
    progressBar: $('#progress-bar'),
    installInfo: $('#install-info'),
    enginePath: $('#engine-path'), comfyPath: $('#comfy-path'), runtimePath: $('#runtime-path'),
    shortcutBtn: $('#shortcut-btn'),
    terminal: $('#terminal'),
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
  }

  // ---------------- 渲染 ----------------
  function applyStaticI18n() {
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en'
    document.title = t('appName')
    el.langBtn.textContent = t('lang')
    el.workspaceTitle.textContent = t('workspace')
    el.workspaceSub.textContent = state.bootState === 'ready' ? t('workspaceSubReady') : t('workspaceSub')
    el.progressDesc.textContent = t('connecting')
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
  }

  const STATE_TEXT = {
    preparing: 'statusPreparing', python: 'statusPython', torch: 'statusTorch',
    deps: 'statusDeps', models: 'statusModels', starting: 'statusStarting',
    ready: 'statusReady', error: 'statusError',
  }

  function renderProgress() {
    const s = state.bootState
    el.progressText.textContent = s === 'ready' ? t('starting') : t('installing')
    el.progressIcon.textContent = s === 'ready' ? '✓' : (s === 'error' ? '⚠' : 'F')
    el.progressCard.classList.toggle('done', s === 'ready')
    el.progressCard.classList.toggle('error', s === 'error')
    el.progressBar.style.width = Math.round(state.progress * 100) + '%'
    const stateText = t(STATE_TEXT[s] || 'statusPreparing')
    el.actionBtn.textContent = s === 'ready' ? t('processingBtn') : stateText
    el.actionBtn.classList.toggle('busy', s !== 'ready')
    el.workspaceSub.textContent = s === 'ready' ? t('workspaceSubReady') : t('workspaceSub')
    if (s === 'ready') {
      el.openAppBtn.classList.add('pulse')
    } else {
      el.openAppBtn.classList.remove('pulse')
    }
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

  function setView(v) {
    state.view = v
    el.bootView.style.display = v === 'boot' ? '' : 'none'
    el.webviewWrap.style.display = v === 'app' ? '' : 'none'
    if (v === 'app') {
      el.webview.src = state.appUrl
    }
  }

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

  // ---------------- 事件：侧栏 ----------------
  el.navItems.forEach((n) => {
    n.addEventListener('click', () => {
      const action = n.getAttribute('data-action')
      if (action === 'start') { setView('boot'); el.progressCard.scrollIntoView({ behavior: 'smooth' }) }
      if (action === 'install') { setView('boot'); el.installInfo.scrollIntoView({ behavior: 'smooth' }) }
      if (action === 'processing') { setView('boot') }
      if (action === 'terminal') { setView('boot'); el.terminal.scrollIntoView({ behavior: 'smooth' }) }
      if (action === 'comfy') window.desk.openComfyUI()
      if (action === 'settings') { setView('boot'); el.updateBar.scrollIntoView({ behavior: 'smooth' }) }
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
    if (state.bootState === 'ready') setView('app')
  })
  el.actionBtn.addEventListener('click', () => {
    if (state.bootState === 'error') window.desk.relaunch()
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
    state.appUrl = url
    state.bootState = 'ready'
    state.progress = 1
    renderProgress()
    // 自动进入应用视图（如图：后台就绪后直接可操作）
    setTimeout(() => { if (state.bootState === 'ready') setView('app') }, 600)
  })
  window.desk.on('backend:error', ({ text }) => {
    state.bootState = 'error'
    renderProgress()
    state.logs.push({ text: '[ERROR] ' + text, ts: Date.now() })
    renderTerminal()
  })
  window.desk.on('backend:port', ({ port }) => { state.appUrl = `http://127.0.0.1:${port}` })
  window.desk.on('backend:tick', ({ seconds }) => {
    // 「正在处理」计时
    const badge = document.querySelector('.nav-item[data-action="processing"] .nav-badge')
    if (badge) {
      badge.style.display = state.bootState === 'ready' ? 'none' : ''
      badge.textContent = String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0')
    }
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
    try {
      state.paths = await window.desk.paths()
      if (state.paths) {
        el.runtimePath.textContent = state.paths.runtime
        el.enginePath.textContent = 'AI 视频工坊 · AnimeDiff / Wan 2.2 / ComfyUI'
        el.comfyPath.textContent = 'http://127.0.0.1:8188'
      }
      el.versionTag.textContent = 'v' + (await window.desk.version())
    } catch { /* ignore */ }
    // 手动检查一次更新（设置页可见反馈）
    setTimeout(() => window.desk.checkUpdate(), 1500)
    renderTerminal()
  }
  boot()
})()
