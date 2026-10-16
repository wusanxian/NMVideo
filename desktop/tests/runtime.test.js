const { test } = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { pathToFileURL } = require('node:url')
const { isAppURL, isShellURL, isTrustedSender, isExternalURL } = require('../security')
const { BackendService, probeBackend } = require('../backend-service')
const http = require('node:http')

test('only exact local backend origin may be embedded', () => {
  assert.ok(isAppURL('http://127.0.0.1:8000/projects/a?tab=timeline', 8000))
  for (const url of ['https://example.com', 'http://127.0.0.1:8001/', 'http://127.0.0.1.evil:8000',
    'http://user@127.0.0.1:8000', 'file:///C:/x', 'javascript:alert(1)', 'http://localhost:8000']) {
    assert.equal(isAppURL(url, 8000), false, url)
  }
  assert.ok(isExternalURL('https://example.com/docs'))
  assert.equal(isExternalURL('file:///C:/Windows/test.exe'), false)
  assert.equal(isExternalURL('https://user:pass@example.com'), false)
})

test('IPC accepts only the shell main frame and its own webContents', () => {
  const filename = 'C:/app/renderer/index.html'
  const frame = { url: pathToFileURL(filename).href }
  const wc = { mainFrame: frame }
  assert.ok(isShellURL(frame.url, filename))
  assert.ok(isTrustedSender({ sender: wc, senderFrame: frame }, wc, filename))
  assert.equal(isTrustedSender({ sender: {}, senderFrame: frame }, wc, filename), false)
  assert.equal(isTrustedSender({ sender: wc, senderFrame: { ...frame } }, wc, filename), false)
  frame.url = 'http://127.0.0.1:8000/'
  assert.equal(isTrustedSender({ sender: wc, senderFrame: frame }, wc, filename), false)
})

test('reuse external backend, recover disconnection, never terminate external service', async () => {
  let online = true
  const changes = []
  const service = new BackendService({ probe: async () => online,
    launch: () => assert.fail('must not launch'), stop: () => assert.fail('must not kill'),
    changed: value => changes.push(value) })
  await service.check(true)
  await service.check(true)
  online = false
  await service.check()
  online = true
  await service.check()
  assert.deepEqual(changes, [true, false, true])
  service.close()
})

test('retry is serialized and starts only one owned child; close stops only that PID once', async () => {
  const child = Object.assign(new EventEmitter(), { pid: 1234, exitCode: null })
  let launches = 0
  const stops = []
  const service = new BackendService({ probe: async () => false,
    launch: () => { launches++; return child }, stop: pid => stops.push(pid), changed() {} })
  await Promise.all([service.check(true), service.check(true), service.check(true)])
  await service.check(true)
  assert.equal(launches, 1)
  child.emit('exit', 1)
  await service.check(true)
  assert.equal(launches, 2)
  service.close()
  service.close()
  assert.deepEqual(stops, [1234])
})

test('a probe completing after shutdown cannot launch or change state', async () => {
  let finish
  const service = new BackendService({ probe: () => new Promise(resolve => { finish = resolve }),
    launch: () => assert.fail(), stop: () => assert.fail(), changed: () => assert.fail() })
  const pending = service.check(true)
  service.close()
  finish(false)
  await pending
})

test('health probe rejects generic HTML, invalid JSON and error responses', async t => {
  let body = '<html>Other app</html>'
  let status = 200
  const server = http.createServer((_req, res) => { res.writeHead(status); res.end(body) })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => server.close())
  const port = server.address().port
  assert.equal(await probeBackend(port), false)
  body = '{"ok":true}'
  assert.equal(await probeBackend(port), true)
  status = 500
  assert.equal(await probeBackend(port), false)
})
