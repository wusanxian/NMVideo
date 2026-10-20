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

function child(pid) { return Object.assign(new EventEmitter(), { pid, exitCode: null }) }

test('manual stop releases owned service, stays stopped during polling and can start again', async () => {
  const children = [child(2001), child(2002)]
  let launches = 0
  let online = false
  let probes = 0
  const stopped = []
  const service = new BackendService({
    probe: async () => { probes++; return online },
    launch: () => children[launches++], changed() {},
    stop: async pid => { stopped.push(pid); online = false },
  })
  await service.enable()
  online = true
  await service.check()
  assert.equal(service.status().owned, true)
  await service.disable()
  const probesAtStop = probes
  await service.check(true)
  await service.check()
  assert.equal(probes, probesAtStop, 'no polling while intentionally stopped')
  assert.deepEqual(stopped, [2001])
  assert.equal(service.status().enabled, false)
  assert.equal(service.status().online, false)
  await service.enable()
  assert.equal(launches, 2)
  assert.equal(service.child.pid, 2002)
  // 旧进程的迟到退出事件不能清除新进程的归属。
  children[0].emit('exit', 1)
  assert.equal(service.child.pid, 2002)
  service.close()
})

test('stop invalidates an in-flight health response without launching or reconnecting', async () => {
  let finish
  let changed = 0
  const service = new BackendService({ probe: () => new Promise(resolve => { finish = resolve }),
    launch: () => assert.fail('stale probe must not launch'), changed: () => { changed++ }, stop() {} })
  const checking = service.check(true)
  await service.disable()
  finish(true)
  await checking
  assert.equal(service.online, false)
  assert.equal(changed, 0)
  assert.equal(service.enabled, false)
  service.close()
})

test('repeated stop is one operation and restart waits until termination finishes', async () => {
  let finish
  let stops = 0
  let launches = 0
  const service = new BackendService({ probe: async () => false, changed() {},
    launch: () => child(++launches), stop: () => { stops++; return new Promise(resolve => { finish = resolve }) } })
  await service.enable()
  const stopping = service.disable()
  const repeated = service.disable()
  assert.equal(stopping, repeated)
  const restarting = service.enable()
  assert.equal(service.stopping, true)
  assert.equal(launches, 1)
  finish()
  await Promise.all([stopping, restarting])
  assert.equal(stops, 1)
  assert.equal(launches, 2)
  service.stop = () => {}
  service.close()
})

test('failed termination preserves ownership and allows retry instead of claiming resources released', async () => {
  const service = new BackendService({ probe: async () => false, changed() {},
    launch: () => child(2003), stop: async () => { throw new Error('Access denied') } })
  await service.enable()
  await assert.rejects(service.disable(), /Access denied/)
  assert.equal(service.enabled, true)
  assert.equal(service.status().owned, true)
  assert.equal(service.stopping, false)
  service.stop = () => {}
  await service.disable()
  assert.equal(service.status().owned, false)
  service.close()
})

test('manual stop refuses to terminate an externally started service', async () => {
  const service = new BackendService({ probe: async () => true, changed() {},
    launch: () => assert.fail(), stop: () => assert.fail('external process must survive') })
  await service.enable()
  await assert.rejects(service.disable(), /其他窗口/)
  assert.equal(service.online, true)
  assert.equal(service.enabled, true)
  service.close()
})
