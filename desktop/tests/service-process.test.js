const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { fork, execFile } = require('node:child_process')
const { BackendService, probeBackend } = require('../backend-service')

const killTree = pid => new Promise((resolve, reject) => {
  execFile('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true, timeout: 15000 },
    error => error ? reject(error) : resolve())
})
const alive = pid => { try { process.kill(pid, 0); return true } catch { return false } }

test('Windows stop/start terminates the owned backend and worker, then starts a fresh service',
  { skip: process.platform !== 'win32', timeout: 30000 }, async t => {
    const launched = []
    let started
    const service = new BackendService({ port: 0,
      probe: port => port ? probeBackend(port) : Promise.resolve(false), changed() {}, stop: killTree,
      launch() {
        const instance = fork(path.join(__dirname, 'fixtures/service-tree.js'), [], {
          windowsHide: true, stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
        })
        started = new Promise((resolve, reject) => {
          instance.once('message', info => { service.port = info.port; launched.push({ instance, ...info }); resolve(info) })
          instance.once('error', reject)
          instance.once('exit', code => { if (code) reject(new Error(`Fixture exited ${code}`)) })
        })
        return instance
      },
    })
    t.after(async () => {
      for (const { instance } of launched) {
        if (instance.exitCode === null) await killTree(instance.pid).catch(() => {})
      }
      service.close()
    })
    for (let cycle = 0; cycle < 2; cycle++) {
      await service.enable()
      const info = await started
      await service.check()
      assert.equal(service.online, true)
      assert.equal(alive(info.workerPid), true)
      await service.disable()
      assert.equal(await probeBackend(info.port), false, 'owned listening port released')
      assert.equal(alive(info.workerPid), false, 'worker terminated with its launcher')
      await service.check(true)
      assert.equal(service.child, null, 'manual stop cannot respawn in a health check')
    }
    assert.equal(launched.length, 2)
    assert.notEqual(launched[0].instance.pid, launched[1].instance.pid)
  })
