/**
 * 启动包装：清除 ELECTRON_RUN_AS_NODE 后再启动 Electron。
 *
 * 背景：当本机已运行基于 Electron 的宿主（如 WorkBuddy 桌面端）时，
 * 环境里可能有 ELECTRON_RUN_AS_NODE=1（防止嵌套 Electron GUI），
 * 该变量会把 electron.exe 降级成纯 Node 运行（require('electron') 返回桩字符串、
 * process.type 为 undefined、app 不存在）。桌面端必须原生运行，故启动前清除。
 */
const { spawn } = require('child_process')
const path = require('path')

const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE

const electron = require('electron')
const child = spawn(electron, [path.join(__dirname)], {
  stdio: 'inherit',
  env,
  windowsHide: false,
})

child.on('exit', (code, signal) => {
  process.exit(code ?? (signal ? 1 : 0))
})
