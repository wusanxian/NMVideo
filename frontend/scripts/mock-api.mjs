/**
 * 页面渲染验收用的 mock 服务：静态托管 dist + 用假数据响应 /api/*。
 * 只用于本地验收，不参与打包。
 *
 *   node scripts/mock-api.mjs [port]
 */
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { FIXTURES } from './fixtures.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DIST = path.resolve(HERE, '../dist')
const PORT = Number(process.argv[2] || 4173)

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.woff2': 'font/woff2',
  '.mp4': 'video/mp4',
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`)
  const pathname = url.pathname

  if (pathname.startsWith('/api/')) {
    const body = pathname in FIXTURES ? FIXTURES[pathname] : {}
    const payload = JSON.stringify(body)
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
    res.end(payload)
    return
  }

  // 静态资源
  let file = path.join(DIST, pathname)
  if (!file.startsWith(DIST) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    file = path.join(DIST, 'index.html') // SPA 回退
  }
  const ext = path.extname(file)
  res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' })
  fs.createReadStream(file).pipe(res)
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(`mock server: http://127.0.0.1:${PORT}`)
})
