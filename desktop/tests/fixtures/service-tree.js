// Isolated lightweight backend plus child worker. Never loads models or user projects.
const http = require('node:http')
const { spawn } = require('node:child_process')
const worker = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {
  env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' }, windowsHide: true, stdio: 'ignore',
})
worker.on('error', error => { console.error(error); process.exit(1) })
const server = http.createServer((req, res) => {
  if (req.url === '/api/health') {
    res.setHeader('Content-Type', 'application/json')
    res.end('{"ok":true}')
  } else {
    res.setHeader('Content-Type', 'text/html')
    res.end('<!doctype html><title>Isolated workspace</title><h1>Service lifecycle test</h1>')
  }
}).listen(0, '127.0.0.1', () => {
  const port = server.address().port
  console.log(`[AIVIDEO] PORT=${port}`)
  console.log(`[TEST_WORKER] PID=${worker.pid}`)
  console.log('[AIVIDEO] STATE=ready')
  process.send?.({ port, workerPid: worker.pid })
})
