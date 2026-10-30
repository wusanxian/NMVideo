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
      appName: 'NMVideo',
      workspace: '你的工作空间',
      workspaceSub: '正在准备运行环境与生成资源，完成后即可开始生成',
      workspaceSubReady: '工作空间已就绪',
      workspaceNav: '工作台',
      openComfyUI: '打开 ComfyUI', terminal: '终端', close: '关闭',
      starting: '正在启动', installing: '正在准备运行环境',
      // 环境准备清单：只讲「这一步在干什么」，不出现具体模型名
      setupTitle: '环境准备 · 首次启动需要完成以下步骤',
      setupSummary: (i, n, el) => `第 ${i} / ${n} 步 · 已用时 ${el}`,
      setupSummaryDone: (n) => `${n} 项全部完成`,
      setupSummaryFail: (i, n) => `第 ${i} / ${n} 步未完成`,
      stepFilesT: '准备程序文件', stepFilesD: '解压并校验程序本体，安装后不再重复',
      stepPyT: '准备运行环境', stepPyD: '独立的运行环境，不改动系统里已装的软件',
      stepGpuT: '安装显卡加速组件', stepGpuD: '让视频生成跑在显卡上，速度提升数倍',
      stepDepT: '安装运行依赖', stepDepD: '视频合成、音频处理需要的基础组件',
      stepResT: '下载生成资源', stepResD: '视频生成所需的本地资源包，装好后可离线生成',
      stepSrvT: '启动本地服务', stepSrvD: '引擎在后台常驻，只在本机监听，不对外网开放',
      stepElapsed: (el) => `已用时 ${el}`,
      stepNoProgress: (el) => `已停滞 ${el} 无进展`,
      stallHint: '下载已长时间没有进展。可点「重试」重新开始，或在系统管理里查看终端日志；换网络后重试通常更快。',
      engineValue: '本地视频生成引擎 · GPU 加速',
      heroDescSetup: '正在准备运行环境与生成资源，请保持网络连接；长时间无进展可重试。',
      installInfo: '安装信息',
      enginePath: '引擎', comfyPath: 'ComfyUI', runtimePath: '运行目录',
      createShortcut: '创建桌面快捷方式', shortcutReady: '快捷方式已就绪',
      shortcutDone: '快捷方式已创建',
      copy: '复制', export: '导出报告', clear: '清空',
      terminalHint: '这里显示最近的输出；复制或导出可获取完整日志。',
      openApp: '打开应用', backToBoot: '返回启动台',
      statusPreparing: '准备程序文件', statusPython: '准备运行环境',
      statusTorch: '安装显卡加速组件', statusDeps: '安装运行依赖',
      statusModels: '下载生成资源', statusStarting: '启动本地服务',
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
      startService: '开启服务', stopService: '关闭服务',
      serviceStarting: '服务启动中', serviceOnline: '服务已开启',
      serviceStopping: '正在关闭服务', serviceStopped: '服务已关闭',
      serviceIdle: '已释放本软件的后台占用，需要时点击“开启服务”。',
      serviceExternal: '外部服务 · 原窗口关闭',
      serviceHint: '不用生成时可关闭本地服务，释放本软件的内存与显存。独立启动的 ComfyUI 需在原窗口关闭。',
      mcpBtn: '🤖 AI 连接', mcpTitle: 'AI 助手连接',
      mcpOn: '已开启 · 等待 AI 助手接入', mcpOff: '未开启',
      mcpEnable: '一键开启', mcpDisable: '关闭', mcpCopy: '复制地址', mcpCopied: '已复制 ✓',
      mcpHint: '复制地址粘贴到 AI 助手（WorkBuddy / Claude Code 等），即可对话直接生成视频。地址含访问令牌，请勿外传。',
      mcpConfig: '配置示例', mcpLive: '连接状态', mcpRecent: '最近调用',
      mcpNoClient: '还没有 AI 助手连入。粘贴地址并对话后，这里会实时显示连接与工具调用。',
      mcpNoActivity: '暂无调用记录',
      mcpBackendOffline: '后端服务未就绪，开启服务后再试。',
    },
    en: {
      appName: 'NMVideo',
      workspace: 'Your Workspace',
      workspaceSub: 'Preparing the runtime and generation assets. Ready soon.',
      workspaceSubReady: 'Workspace is ready',
      workspaceNav: 'Workspace',
      openComfyUI: 'Open ComfyUI', terminal: 'Terminal', close: 'Close',
      starting: 'Starting', installing: 'Preparing runtime',
      setupTitle: 'Setup · these steps run on first launch',
      setupSummary: (i, n, el) => `Step ${i} of ${n} · ${el} elapsed`,
      setupSummaryDone: (n) => `All ${n} steps done`,
      setupSummaryFail: (i, n) => `Step ${i} of ${n} did not finish`,
      stepFilesT: 'Prepare program files', stepFilesD: 'Unpack and verify the app itself — only once',
      stepPyT: 'Prepare runtime', stepPyD: 'Isolated runtime; nothing already on your system is touched',
      stepGpuT: 'Install GPU acceleration', stepGpuD: 'Run generation on the graphics card — several times faster',
      stepDepT: 'Install dependencies', stepDepD: 'Building blocks for video compositing and audio',
      stepResT: 'Download generation assets', stepResD: 'Local assets the engine needs; works offline afterwards',
      stepSrvT: 'Start local service', stepSrvD: 'Engine runs in the background, listening on this machine only',
      stepElapsed: (el) => `${el} elapsed`,
      stepNoProgress: (el) => `No progress for ${el}`,
      stallHint: 'The download has not moved for a while. Hit Retry to restart, or open System to read the terminal; retrying on another network is often faster.',
      engineValue: 'Local video engine · GPU accelerated',
      heroDescSetup: 'Preparing the runtime and generation assets. Keep the network on; retry if it stalls.',
      installInfo: 'Installation Info',
      enginePath: 'Engine', comfyPath: 'ComfyUI', runtimePath: 'Runtime',
      createShortcut: 'Create Desktop Shortcut', shortcutReady: 'Shortcut ready',
      shortcutDone: 'Shortcut created',
      copy: 'Copy', export: 'Export', clear: 'Clear',
      terminalHint: 'Recent output shown here. Copy or export for the full log.',
      openApp: 'Open App', backToBoot: 'Back to Launcher',
      statusPreparing: 'Preparing files', statusPython: 'Preparing runtime',
      statusTorch: 'Installing GPU acceleration', statusDeps: 'Installing dependencies',
      statusModels: 'Downloading assets', statusStarting: 'Starting service',
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
      startService: 'Start service', stopService: 'Stop service',
      serviceStarting: 'Service starting', serviceOnline: 'Service running',
      serviceStopping: 'Stopping service', serviceStopped: 'Service stopped',
      serviceIdle: 'Background resources released. Start the service when needed.',
      serviceExternal: 'External service · stop in its launcher',
      serviceHint: 'Stop the local service to release its RAM and GPU memory. Close independently launched ComfyUI in its own window.',
      mcpBtn: '🤖 AI Link', mcpTitle: 'AI Assistant Link',
      mcpOn: 'Enabled · waiting for an AI client', mcpOff: 'Disabled',
      mcpEnable: 'Enable', mcpDisable: 'Disable', mcpCopy: 'Copy URL', mcpCopied: 'Copied ✓',
      mcpHint: 'Paste the URL into your AI assistant (WorkBuddy / Claude Code, etc.) to generate videos by chat. The URL contains an access token — keep it private.',
      mcpConfig: 'Config examples', mcpLive: 'Connections', mcpRecent: 'Recent tool calls',
      mcpNoClient: 'No AI client connected yet. Paste the URL and chat — connections and tool calls will show up here live.',
      mcpNoActivity: 'No calls yet',
      mcpBackendOffline: 'Backend is not ready. Start the service first.',
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
    serviceEnabled: true,
    serviceOwned: false,
    controlBusy: false,
    route: localStorage.getItem('aivideo.desktopRoute') || '/',
    // 环境准备清单：stepIndex = 当前进行到第几步；stage = 当前步骤的实时进度
    stepIndex: 0,
    stageKey: '',
    elapsed: 0,
    stage: { pct: null, speed: '', changedAt: 0 },
  }

  const MAX_LOGS = 500

  /**
   * 启动器上报的粗阶段（[AIVIDEO] STATE=xxx）→ 用户视角的「这一步在干什么」。
   * 这里刻意不出现任何模型/框架名：首启的人不认识它们，只关心这一步干什么、还要多久。
   */
  const STAGE_ORDER = ['preparing', 'python', 'torch', 'deps', 'models', 'starting']
  const SETUP_STEPS = [
    { title: 'stepFilesT', desc: 'stepFilesD' },
    { title: 'stepPyT', desc: 'stepPyD' },
    { title: 'stepGpuT', desc: 'stepGpuD' },
    { title: 'stepDepT', desc: 'stepDepD' },
    { title: 'stepResT', desc: 'stepResD' },
    { title: 'stepSrvT', desc: 'stepSrvD' },
  ]
  // 只有这几个阶段有真实下载百分比可解析（pip / huggingface 进度行）
  const DOWNLOAD_STAGES = new Set(['torch', 'deps', 'models'])
  const STALL_MS = 90 * 1000

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
    setupSteps: $('#setup-steps'),
    setupList: $('#setup-list'),
    setupSummary: $('#setup-summary'),
    setupStall: $('#setup-stall'),
    systemBtn: $('#system-btn'),
    systemPanel: $('#system-panel'),
    panelCloseBtn: $('#panel-close-btn'),
    serviceTimer: $('#service-timer'),
    connectionCard: $('#connection-card'),
    connectionMessage: $('#connection-message'),
    serviceStatus: $('#service-status'),
    serviceStart: $('#service-start-btn'), serviceStop: $('#service-stop-btn'),
    serviceMessage: $('#service-message'),
    // AI 助手连接（MCP）弹框
    mcpBtn: $('#mcp-btn'),
    mcpModal: $('#mcp-modal'), mcpBackdrop: $('#mcp-backdrop'), mcpClose: $('#mcp-close-btn'),
    mcpStateDot: $('#mcp-state-dot'), mcpStateText: $('#mcp-state-text'), mcpToggle: $('#mcp-toggle-btn'),
    mcpUrl: $('#mcp-url'), mcpCopy: $('#mcp-copy-btn'),
    mcpSnippetWb: $('#mcp-snippet-wb'), mcpSnippetCc: $('#mcp-snippet-cc'),
    mcpSessionCount: $('#mcp-session-count'),
    mcpSessions: $('#mcp-sessions'), mcpActivity: $('#mcp-activity'),
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
    applySetupI18n()
    el.connectionMessage.textContent = t('disconnected')
  }

  const STATE_TEXT = {
    preparing: 'statusPreparing', python: 'statusPython', torch: 'statusTorch',
    deps: 'statusDeps', models: 'statusModels', starting: 'statusStarting',
    ready: 'statusReady', error: 'statusError',
    stopping: 'serviceStopping', stopped: 'serviceStopped',
  }

  // ---------------- 环境准备清单 ----------------
  const stepEls = []
  const mmss = (sec) => {
    const s = Math.max(0, Math.round(Number(sec) || 0))
    return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')
  }

  function buildSetupList() {
    el.setupList.textContent = ''
    stepEls.length = 0
    SETUP_STEPS.forEach((_, i) => {
      const li = document.createElement('li')
      li.className = 'step pending'
      const mark = document.createElement('span')
      mark.className = 'step-mark'
      mark.textContent = String(i + 1)
      const body = document.createElement('div')
      body.className = 'step-body'
      const title = document.createElement('div')
      title.className = 'step-title'
      const desc = document.createElement('div')
      desc.className = 'step-desc'
      const meta = document.createElement('div')
      meta.className = 'step-meta'
      body.append(title, desc, meta)
      li.append(mark, body)
      el.setupList.appendChild(li)
      stepEls.push({ root: li, mark, title, desc, meta })
    })
  }

  function applySetupI18n() {
    SETUP_STEPS.forEach((s, i) => {
      const e = stepEls[i]
      if (!e) return
      e.title.textContent = t(s.title)
      e.desc.textContent = t(s.desc)
    })
    renderSetup()
  }

  /** 清单状态：已完成 / 进行中 / 未开始 / 失败（失败停留在他卡住的那一步） */
  function renderSetup() {
    const s = state.bootState
    const total = SETUP_STEPS.length
    el.setupSteps.hidden = s === 'stopped'
    stepEls.forEach((e, i) => {
      let cls
      let mark
      if (s === 'ready' || i < state.stepIndex) {
        cls = 'step done'
        mark = '✓'
      } else if (i === state.stepIndex) {
        cls = s === 'error' ? 'step error' : 'step active'
        mark = s === 'error' ? '!' : '•'
      } else {
        cls = 'step pending'
        mark = String(i + 1)
      }
      e.root.className = cls
      e.mark.textContent = mark
    })
    renderSetupSummary()
    renderStepMeta()
  }

  function renderSetupSummary() {
    const s = state.bootState
    const total = SETUP_STEPS.length
    if (s === 'stopped') el.setupSummary.textContent = ''
    else if (s === 'ready') el.setupSummary.textContent = t('setupSummaryDone')(total)
    else if (s === 'error') el.setupSummary.textContent = t('setupSummaryFail')(state.stepIndex + 1, total)
    else el.setupSummary.textContent = t('setupSummary')(state.stepIndex + 1, total, mmss(state.elapsed))
  }

  /**
   * 当前步骤的实时细节。只用真数据：
   *  有解析到下载百分比 → 百分比 + 速度；没有 → 只说已用时。
   * 超过 STALL_MS 没变化才报「已停滞」，避免把慢当成死。
   */
  function renderStepMeta() {
    stepEls.forEach((e) => {
      e.meta.textContent = ''
      e.meta.className = 'step-meta'
    })
    const e = stepEls[state.stepIndex]
    if (!e) {
      el.setupStall.hidden = true
      return
    }
    const s = state.bootState
    if (s === 'ready' || s === 'stopped') {
      el.setupStall.hidden = true
      return
    }
    const stage = state.stage
    const downloading = DOWNLOAD_STAGES.has(s) && stage.pct !== null
    const idleSec = downloading ? (Date.now() - stage.changedAt) / 1000 : 0
    const stalled = downloading && idleSec * 1000 > STALL_MS
    const parts = []
    if (downloading) {
      parts.push(stage.pct + '%')
      if (stage.speed) parts.push(stage.speed)
    }
    parts.push(stalled ? t('stepNoProgress')(mmss(idleSec)) : t('stepElapsed')(mmss(state.elapsed)))
    e.meta.textContent = parts.join(' · ')
    e.meta.className = 'step-meta' + (stalled || s === 'error' ? ' warn' : '')
    el.setupStall.hidden = !stalled
    if (stalled) el.setupStall.textContent = t('stallHint')
  }

  /** 从启动器日志里刮真实进度（tqdm / pip 的 `43%|... 472.72s/it`、`12%|.. 1.2MB/s`） */
  function noteProgress(text) {
    if (!DOWNLOAD_STAGES.has(state.bootState)) return
    const m = String(text).match(/(\d{1,3}(?:\.\d+)?)\s*%/)
    if (!m) return
    const pct = Math.round(Number(m[1]))
    if (!(pct >= 0 && pct <= 100)) return
    const sp = String(text).match(/(\d+(?:\.\d+)?\s*(?:[KMGT]i?B\/s|[a-zA-Z]+\/it))/i)
    const now = Date.now()
    if (state.stage.pct !== pct) state.stage.changedAt = now
    if (!state.stage.changedAt) state.stage.changedAt = now
    state.stage.pct = pct
    state.stage.speed = sp ? sp[1].replace(/\s+/g, '') : state.stage.speed
    renderStepMeta()
  }

  /** 双卡同步：boot 视图英雄卡 + 系统面板服务卡渲染同一份状态 */
  function renderProgress() {
    const s = state.bootState
    // 阶段变化时推进清单，并清空上一阶段的下载进度（否则会显示错位的百分比）
    if (s !== state.stageKey) {
      state.stageKey = s
      const i = STAGE_ORDER.indexOf(s)
      if (i >= 0) state.stepIndex = i
      if (s === 'ready') state.stepIndex = SETUP_STEPS.length - 1
      state.stage = { pct: null, speed: '', changedAt: 0 }
    }
    const stateText = t(STATE_TEXT[s] || 'statusPreparing')
    for (const card of document.querySelectorAll('.progress-card')) {
      const icon = card.querySelector('.progress-icon')
      const text = card.querySelector('.progress-text')
      const desc = card.querySelector('.progress-desc')
      const bar = card.querySelector('.progress-bar')
      if (icon) icon.textContent = s === 'ready' ? '✓' : (s === 'error' ? '⚠' : 'N')
      if (text) text.textContent = stateText
      if (desc) {
        desc.textContent = s === 'stopped' ? t('serviceIdle')
          : (state.online ? t('workspaceSubReady')
            : (['ready', 'error'].includes(s) ? t('disconnected') : t('heroDescSetup')))
      }
      if (bar) bar.style.width = Math.round(state.progress * 100) + '%'
      card.classList.toggle('done', s === 'ready')
      card.classList.toggle('error', s === 'error')
    }
    el.openAppBtn.disabled = !state.online
    el.openAppBtn.style.display = s === 'stopped' ? 'none' : ''
    el.actionBtn.textContent = s === 'stopped' ? t('startService') : (s === 'error' ? t('retry') : stateText)
    el.actionBtn.classList.toggle('primary', s === 'stopped')
    el.actionBtn.classList.toggle('ghost', s !== 'stopped')
    el.actionBtn.disabled = state.controlBusy || s === 'stopping'
    el.actionBtn.classList.toggle('busy', !['ready', 'error', 'stopped'].includes(s))
    el.workspaceSub.textContent = s === 'stopped' ? t('serviceIdle') : (s === 'ready' ? t('workspaceSubReady') : t('workspaceSub'))
    el.openAppBtn.classList.toggle('pulse', s === 'ready')
    // 服务计时器：启动/重连期间显示已耗时，就绪后隐藏
    el.serviceTimer.style.display = ['ready', 'stopped', 'stopping'].includes(s) ? 'none' : ''
    el.serviceStatus.textContent = t(s === 'stopped' ? 'serviceStopped' : s === 'stopping' ? 'serviceStopping'
      : state.online ? (state.serviceOwned ? 'serviceOnline' : 'serviceExternal') : 'serviceStarting')
    el.serviceStatus.classList.toggle('online', state.online)
    el.serviceStatus.classList.toggle('stopped', s === 'stopped')
    el.serviceStart.disabled = state.controlBusy || s === 'stopping' || (state.serviceEnabled && s !== 'error')
    el.serviceStop.disabled = state.controlBusy || s === 'stopping' || !state.serviceEnabled || (state.online && !state.serviceOwned)
    el.serviceStop.title = state.online && !state.serviceOwned ? t('serviceExternal') : t('serviceHint')
    renderSetup()
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
      if (!el.webview.isConnected) el.webviewWrap.appendChild(el.webview)
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
    state.serviceEnabled = true
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

  function backendStopped() {
    state.serviceEnabled = false
    state.serviceOwned = false
    state.online = false
    state.bootState = 'stopped'
    state.progress = 0
    state.loadedOrigin = ''
    state.loadFailed = false
    // 销毁工作台 guest，停止页面轮询；再次开启时按保存的路由重建。
    el.webview.remove()
    showConnection(false)
    setView('boot')
    setPanel(false)
    renderProgress()
  }

  async function changeService(enable) {
    if (state.controlBusy) return
    state.controlBusy = true
    el.serviceMessage.hidden = true
    renderProgress()
    try {
      const result = await (enable ? window.desk.startBackend() : window.desk.stopBackend())
      if (result?.cancelled) return
      const status = await window.desk.backendStatus()
      state.serviceOwned = Boolean(status.owned)
      if (status.enabled === false) backendStopped()
      else if (status.online) backendReady(`http://127.0.0.1:${status.port}`)
      else if (enable && state.bootState !== 'error') {
        state.serviceEnabled = true
        state.bootState = 'starting'
        state.progress = 0.04
      }
    } catch (error) {
      el.serviceMessage.textContent = String(error.message || error)
      el.serviceMessage.hidden = false
    } finally {
      state.controlBusy = false
      renderProgress()
    }
  }

  el.serviceStart.addEventListener('click', () => changeService(true))
  el.serviceStop.addEventListener('click', () => changeService(false))

  async function retryBackend() {
    try {
      await window.desk.retryBackend()
      const status = await window.desk.backendStatus()
      if (status.online) {
        state.serviceOwned = Boolean(status.owned)
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
    if (!state.serviceEnabled) return
    if (event.isMainFrame === false || event.errorCode === -3) return
    state.loadFailed = true
    showConnection(true)
    state.logs.push({ text: `工作台加载失败：${event.errorDescription}`, ts: Date.now() })
    renderTerminal()
  })
  el.webview.addEventListener('render-process-gone', () => {
    if (!state.serviceEnabled) return
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
    if (state.bootState === 'stopped') changeService(true)
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
    noteProgress(text)
  })
  window.desk.on('backend:state', ({ state: s, progress, owned }) => {
    if (typeof owned === 'boolean') state.serviceOwned = owned
    if (!state.serviceEnabled && s !== 'stopped' && s !== 'stopping') state.serviceEnabled = true
    state.bootState = s
    if (typeof progress === 'number') state.progress = progress
    renderProgress()
  })
  window.desk.on('backend:ready', ({ url, owned }) => {
    state.serviceOwned = Boolean(owned)
    backendReady(url)
  })
  window.desk.on('backend:stopped', backendStopped)
  window.desk.on('backend:offline', () => {
    if (!state.serviceEnabled) return
    state.online = false
    state.bootState = 'starting'
    renderProgress()
    showConnection(true)
  })
  window.desk.on('backend:error', ({ text }) => {
    state.bootState = 'error'
    renderProgress()
    // 出错时把终端摆出来，用户不用自己去找日志
    if (!state.panelOpen) setPanel(true)
    state.logs.push({ text: '[ERROR] ' + text, ts: Date.now() })
    renderTerminal()
  })
  window.desk.on('backend:port', ({ port }) => {
    state.appUrl = `http://127.0.0.1:${port}`
    mcp.url = ''  // 端口变了，旧连接地址作废，下次打开弹框重新取
  })
  window.desk.on('backend:tick', ({ seconds }) => {
    // 服务计时器（启动/重连期间的已耗时，MM:SS）
    const total = Number(seconds) || 0
    state.elapsed = total
    el.serviceTimer.textContent =
      String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0')
    el.serviceTimer.style.display = ['ready', 'stopped', 'stopping'].includes(state.bootState) ? 'none' : ''
    renderSetupSummary()
    renderStepMeta()
  })

  // ---------------- AI 助手连接（MCP）：顶栏按钮 → 弹框，复制即用 + 实时连接状态 ----------------
  const mcp = { open: false, timer: null, busy: false, url: '' }

  async function mcpApi(path, opts) {
    const r = await fetch(state.appUrl + path, opts)
    if (!r.ok) throw new Error('HTTP ' + r.status)
    return r.json()
  }

  function fmtTime(ts) {
    const d = new Date(ts * 1000)
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
  }

  function renderMcp() {
    if (!mcp.state) return
    const on = Boolean(mcp.state.enabled)
    el.mcpStateDot.className = 'mcp-dot ' + (on ? 'on' : 'off')
    el.mcpStateText.textContent = on ? t('mcpOn') : t('mcpOff')
    el.mcpToggle.textContent = on ? t('mcpDisable') : t('mcpEnable')
    el.mcpUrl.value = mcp.url
    el.mcpCopy.disabled = !on || !mcp.url
    const u = mcp.url || '<连接地址>'
    el.mcpSnippetWb.textContent = JSON.stringify({ mcpServers: { nmvideo: { url: u } } }, null, 2)
    el.mcpSnippetCc.textContent = `claude mcp add --transport http nmvideo "${u}"`
  }

  function renderMcpActivity(act) {
    const sessions = (act && act.sessions) || []
    el.mcpSessionCount.textContent = sessions.length
      ? (lang === 'zh' ? `${sessions.length} 个 AI 助手已连入` : `${sessions.length} client(s) connected`)
      : ''
    if (!sessions.length) {
      el.mcpSessions.innerHTML = `<div class="mcp-empty">${t('mcpNoClient')}</div>`
    } else {
      el.mcpSessions.innerHTML = sessions.map((s) => `
        <div class="mcp-session">
          <span class="mcp-dot on"></span>
          <span class="mcp-session-sid">AI 助手 · ${s.sid}</span>
          <span class="mcp-session-meta">${s.calls} 次调用 · ${s.last_tool || '—'}</span>
        </div>`).join('')
    }
    const recent = (act && act.recent) || []
    if (!recent.length) {
      el.mcpActivity.innerHTML = `<div class="mcp-empty">${t('mcpNoActivity')}</div>`
    } else {
      el.mcpActivity.innerHTML = recent.map((a) => `
        <div class="mcp-call ${a.ok ? '' : 'fail'}">
          <span class="mcp-call-time">${fmtTime(a.ts)}</span>
          <span class="mcp-call-kind">${a.kind === 'tool' ? '🔧' : a.kind === 'connect' ? '🔌' : '⛔'} ${a.tool || a.kind}</span>
          <span class="mcp-call-detail">${a.detail || ''}</span>
        </div>`).join('')
    }
  }

  async function mcpRefresh() {
    try {
      mcp.state = await mcpApi('/api/services/mcp')
      if (mcp.state.enabled && !mcp.url) {
        const rev = await mcpApi('/api/services/mcp/reveal', { method: 'POST' })
        mcp.url = `${state.appUrl}/mcp?token=${rev.token}`
      } else if (!mcp.state.enabled) {
        mcp.url = ''
      }
      renderMcp()
      if (mcp.open) {
        renderMcpActivity(await mcpApi('/api/services/mcp/activity'))
      }
    } catch { /* 后端离线时保持现状 */ }
  }

  function setMcpOpen(open) {
    mcp.open = open
    el.mcpModal.hidden = !open
    el.mcpBtn.setAttribute('aria-expanded', String(open))
    el.mcpBtn.classList.toggle('active', open)
    clearInterval(mcp.timer)
    if (open) {
      mcpRefresh()
      mcp.timer = setInterval(mcpRefresh, 3000)
    } else {
      mcp.timer = null
    }
  }

  el.mcpBtn.addEventListener('click', () => setMcpOpen(!mcp.open))
  el.mcpClose.addEventListener('click', () => setMcpOpen(false))
  el.mcpBackdrop.addEventListener('click', () => setMcpOpen(false))

  el.mcpToggle.addEventListener('click', async () => {
    if (mcp.busy || !mcp.state) return
    mcp.busy = true
    el.mcpToggle.disabled = true
    try {
      const enable = !mcp.state.enabled
      mcp.state = await mcpApi('/api/services/mcp', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: enable }),
      })
      mcp.url = ''
      if (enable) {
        // 开启即出地址，方便直接复制
        const rev = await mcpApi('/api/services/mcp/reveal', { method: 'POST' })
        mcp.url = `${state.appUrl}/mcp?token=${rev.token}`
      }
      renderMcp()
    } catch {
      el.mcpStateText.textContent = t('mcpBackendOffline')
    } finally {
      mcp.busy = false
      el.mcpToggle.disabled = false
    }
  })

  el.mcpCopy.addEventListener('click', async () => {
    if (!mcp.url) return
    try { await navigator.clipboard.writeText(mcp.url) } catch { el.mcpUrl.select(); document.execCommand('copy') }
    el.mcpCopy.textContent = t('mcpCopied')
    setTimeout(() => { el.mcpCopy.textContent = t('mcpCopy') }, 1400)
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
    buildSetupList()
    applyStaticI18n()
    // 本地兜底计时：启动器每 5s 会推更准的 backend:tick 覆盖
    setInterval(() => {
      if (['ready', 'stopped', 'stopping'].includes(state.bootState)) return
      state.elapsed += 1
      renderSetupSummary()
      renderStepMeta()
    }, 1000)
    // 主区已经承载「状态 + 完整准备清单」，系统面板默认收起；出错时才自动展开给日志
    setPanel(false)
    try {
      state.paths = await window.desk.paths()
      if (state.paths) {
        el.runtimePath.textContent = state.paths.runtime
        el.enginePath.textContent = t('engineValue')
        el.comfyPath.textContent = 'http://127.0.0.1:8188'
      }
      el.versionTag.textContent = 'v' + (await window.desk.version())
      const status = await window.desk.backendStatus()
      state.serviceOwned = Boolean(status.owned)
      if (status.enabled === false) backendStopped()
      else if (status.online) backendReady(`http://127.0.0.1:${status.port}`)
      else renderProgress()
    } catch { /* ignore */ }
    // 手动检查一次更新（设置页可见反馈）
    setTimeout(() => window.desk.checkUpdate(), 1500)
    renderTerminal()
  }
  boot()
})()
