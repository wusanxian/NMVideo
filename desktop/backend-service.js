const http = require('http')

function probeBackend(port) {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/api/health`, { timeout: 2500 }, (res) => {
      let body = ''
      res.on('data', chunk => {
        body += chunk
        if (body.length > 4096) req.destroy()
      })
      res.on('end', () => {
        try { resolve(res.statusCode === 200 && JSON.parse(body).ok === true) }
        catch { resolve(false) }
      })
      res.on('error', () => resolve(false))
    })
    req.on('error', () => resolve(false))
    req.on('timeout', () => { req.destroy(); resolve(false) })
  })
}

// Own only the child returned by launch(); never kill processes discovered on a port.
class BackendService {
  constructor({ probe = probeBackend, launch, changed, stop, port = 8000 }) {
    Object.assign(this, { probe, launch, changed, stop, port })
    this.child = null
    this.online = false
    this.busy = false
    this.closed = false
    this.enabled = true
    this.stopping = false
    this.revision = 0
    this.pendingCheck = null
    this.pendingStop = null
  }

  status() {
    return { online: this.online, port: this.port, enabled: this.enabled,
      stopping: this.stopping, owned: Boolean(this.child) }
  }

  check(start = false) {
    if (this.busy || this.closed || !this.enabled || this.stopping) return this.pendingCheck || Promise.resolve()
    this.pendingCheck = this.runCheck(start)
    return this.pendingCheck
  }

  async runCheck(start) {
    this.busy = true
    const revision = this.revision
    try {
      const online = await this.probe(this.port)
      if (this.closed || !this.enabled || revision !== this.revision) return
      if (online !== this.online) {
        this.online = online
        this.changed(online, this.port)
      }
      if (!online && start && !this.child) {
        const child = this.launch()
        this.child = child || null
        if (child) {
          const clear = () => { if (this.child === child) this.child = null }
          child.once('exit', clear)
          child.once('error', clear)
        }
      }
    } finally { this.busy = false }
  }

  async enable() {
    if (this.closed) throw new Error('Service manager is closed')
    if (this.pendingStop) await this.pendingStop
    if (this.closed) throw new Error('Service manager is closed')
    if (!this.enabled) {
      this.enabled = true
      this.revision++
    }
    if (this.busy) await this.pendingCheck
    await this.check(true)
    return this.status()
  }

  disable() {
    if (this.closed) return Promise.reject(new Error('Service manager is closed'))
    if (this.pendingStop) return this.pendingStop
    if (this.online && !this.child) return Promise.reject(new Error('当前服务由其他窗口启动，请在原启动窗口关闭；本软件不会结束其他程序。'))
    this.enabled = false
    this.stopping = true
    this.revision++
    this.pendingStop = this.stopOwned().finally(() => {
      this.stopping = false
      this.pendingStop = null
    })
    return this.pendingStop
  }

  async stopOwned() {
    const child = this.child
    try {
      if (child && child.exitCode === null && child.pid) await this.stop(child.pid)
      if (this.child === child) this.child = null
      if (this.online) {
        this.online = false
        this.changed(false, this.port)
      }
    } catch (error) {
      // 子进程已经自行退出时，taskkill 的“进程不存在”不属于关闭失败。
      if (this.child === child && child?.exitCode === null) {
        this.enabled = true
        throw error
      }
      this.online = false
    }
  }

  close() {
    if (this.closed) return
    this.closed = true
    this.enabled = false
    this.revision++
    if (this.stopping) return
    const child = this.child
    this.child = null
    if (child && child.exitCode === null && child.pid) Promise.resolve(this.stop(child.pid)).catch(() => {})
  }
}

module.exports = { BackendService, probeBackend }
