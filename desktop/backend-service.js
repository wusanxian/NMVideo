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
  }

  async check(start = false) {
    if (this.busy || this.closed) return
    this.busy = true
    try {
      const online = await this.probe(this.port)
      if (this.closed) return
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

  close() {
    this.closed = true
    const child = this.child
    this.child = null
    if (child && child.exitCode === null && child.pid) this.stop(child.pid)
  }
}

module.exports = { BackendService, probeBackend }
