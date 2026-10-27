/**
 * 页面渲染验收（真实浏览器）
 *
 * 起一个 mock 服务（静态托管 dist + 假数据响应 /api/*），再用系统 Chrome/Edge 的
 * 无头模式逐页渲染，检查页面上真的出现了关键内容。相比 SSR，它能跑 onMounted、
 * 路由懒加载和真实 CSS 布局，是"页面能不能用"最接近真机的检查。
 *
 *   node scripts/page-check.mjs            # 跑全部页面
 *   node scripts/page-check.mjs --shots    # 同时存截图到 dist/../.shots/
 *
 * 注意：先 npm run build。
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { FIXTURES, PAGE_EXPECT } from './fixtures.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const DIST = path.join(ROOT, 'dist')
const PORT = 4173
const SHOTS = process.argv.includes('--shots')
const SHOT_DIR = path.join(ROOT, '.shots')

const CANDIDATES = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].filter(Boolean)
const BROWSER = CANDIDATES.find((p) => fs.existsSync(p))
if (!BROWSER) {
  console.error('没找到 Chrome/Edge，可通过 CHROME_PATH 指定')
  process.exit(1)
}
if (!fs.existsSync(path.join(DIST, 'index.html'))) {
  console.error('没找到 dist 产物，请先 npm run build')
  process.exit(1)
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitFor(url, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const r = await fetch(url)
      if (r.ok) return true
    } catch {
      /* 还没起来 */
    }
    await wait(150)
  }
  return false
}

const strip = (html) => html.replace(/<script[\s\S]*?<\/script>/g, ' ').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')

// 端口上已经有 mock 服务就直接复用，避免重复绑定报错
const alreadyUp = await waitFor(`http://127.0.0.1:${PORT}/api/health`, 300)
const server = alreadyUp
  ? null
  : spawn(process.execPath, [path.join(HERE, 'mock-api.mjs'), String(PORT)], {
      cwd: ROOT,
      stdio: ['ignore', 'pipe', 'inherit'],
    })
if (server) server.stdout.on('data', () => {})

const cleanup = () => {
  try { server?.kill() } catch { /* ignore */ }
}
process.on('exit', cleanup)

if (!alreadyUp && !(await waitFor(`http://127.0.0.1:${PORT}/api/health`))) {
  console.error('mock 服务没起来')
  cleanup()
  process.exit(1)
}
if (SHOTS) fs.mkdirSync(SHOT_DIR, { recursive: true })

function render(route, name) {
  return new Promise((resolve) => {
    const args = [
      '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
      '--window-size=1440,900', '--virtual-time-budget=4500',
      `--user-data-dir=${path.join(ROOT, '.shots', `.profile-${name}`)}`,
    ]
    if (SHOTS) args.push(`--screenshot=${path.join(SHOT_DIR, `${name}.png`)}`)
    args.push('--dump-dom', `http://127.0.0.1:${PORT}${route}`)

    const child = spawn(BROWSER, args, { stdio: ['ignore', 'pipe', 'ignore'] })
    let out = ''
    child.stdout.on('data', (d) => (out += d))
    child.on('close', () => resolve(out))
  })
}

let failed = 0
for (const [route, expects] of PAGE_EXPECT) {
  const name = route === '/' ? 'home' : route.replace(/[^\w]+/g, '-').replace(/^-|-$/g, '')
  const html = await render(route, name)
  const text = strip(html)
  const values = [...html.matchAll(/value="([^"]*)"/g)].map((m) => m[1]).join(' ')
  const haystack = `${text} ${values}`
  const missing = expects.filter((k) => !haystack.includes(k))
  const blank = text.length < 60
  if (missing.length || blank) {
    failed++
    console.log(`FAIL  ${route.padEnd(24)} ${blank ? '页面几乎空白 ' : ''}${missing.length ? '缺少: ' + missing.join(' / ') : ''}`)
    if (blank) console.log(`      正文: ${text.slice(0, 160)}`)
  } else {
    console.log(`OK    ${route.padEnd(24)} ${text.length} 字`)
  }
}

cleanup()
console.log(`\n${PAGE_EXPECT.length - failed}/${PAGE_EXPECT.length} 页面通过${SHOTS ? ` · 截图 ${SHOT_DIR}` : ''}`)
process.exit(failed ? 1 : 0)
