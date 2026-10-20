/**
 * preload：渲染进程安全桥（contextIsolation 下暴露受控 API）
 */
const { contextBridge, ipcRenderer } = require('electron')
const EVENTS = new Set([
  'win:maximized', 'backend:log', 'backend:state', 'backend:ready',
  'backend:error', 'backend:offline', 'backend:stopped', 'backend:port', 'backend:tick',
  'update:checking', 'update:available', 'update:none', 'update:progress',
  'update:downloaded', 'update:error',
])

contextBridge.exposeInMainWorld('desk', {
  // 窗口控制
  minimize: () => ipcRenderer.invoke('win:minimize'),
  toggleMaximize: () => ipcRenderer.invoke('win:toggleMaximize'),
  close: () => ipcRenderer.invoke('win:close'),
  isMaximized: () => ipcRenderer.invoke('win:isMaximized'),

  // 应用信息
  version: () => ipcRenderer.invoke('app:version'),
  paths: () => ipcRenderer.invoke('app:paths'),
  openPath: (p) => ipcRenderer.invoke('app:openPath', p),
  openComfyUI: () => ipcRenderer.invoke('app:openComfyUI'),
  createShortcut: () => ipcRenderer.invoke('app:createShortcut'),
  saveLog: (content) => ipcRenderer.invoke('app:saveLog', content),
  relaunch: () => ipcRenderer.invoke('app:relaunch'),
  backendStatus: () => ipcRenderer.invoke('backend:status'),
  retryBackend: () => ipcRenderer.invoke('backend:retry'),
  startBackend: () => ipcRenderer.invoke('backend:start'),
  stopBackend: () => ipcRenderer.invoke('backend:stop'),
  hardware: () => ipcRenderer.invoke('backend:hardware'),

  // 更新
  checkUpdate: () => ipcRenderer.invoke('update:check'),
  downloadUpdate: () => ipcRenderer.invoke('update:download'),
  installUpdate: () => ipcRenderer.invoke('update:install'),

  // 事件订阅（返回清理函数）
  on: (channel, cb) => {
    if (!EVENTS.has(channel) || typeof cb !== 'function') throw new Error('Unsupported event subscription')
    const listener = (_e, payload) => cb(payload)
    ipcRenderer.on(channel, listener)
    return () => ipcRenderer.removeListener(channel, listener)
  },
})
