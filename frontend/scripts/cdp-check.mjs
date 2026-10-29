/**
 * 页面体检（Chrome DevTools Protocol）
 *
 * 逐页打开真实浏览器，检查三类"截图看不出来"的问题：
 *   1. 控制台报错 / 未捕获异常（Runtime.exceptionThrown）
 *   2. 整页横向溢出（出现横向滚动条）
 *   3. 元素超出视口右侧（被裁掉、需要横向拖动才看得到）
 *
 * 用法：node scripts/cdp-check.mjs   （先 npm run build）
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { PAGE_EXPECT } from './fixtures.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const PORT = 4173
const CDP_PORT = 9333

const CANDIDATES = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
].filter(Boolean)
const BROWSER = CANDIDATES.find((p) => fs.existsSync(p))
if (!BROWSER) {
  console.error('没找到 Chrome/Edge，可通过 CHROME_PATH 指定')
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
      /* retry */
    }
    await wait(150)
  }
  return false
}

const alreadyUp = await waitFor(`http://127.0.0.1:${PORT}/api/health`, 300)
const mock = alreadyUp
  ? null
  : spawn(process.execPath, [path.join(HERE, 'mock-api.mjs'), String(PORT)], { cwd: ROOT, stdio: 'ignore' })
if (!alreadyUp && !(await waitFor(`http://127.0.0.1:${PORT}/api/health`))) {
  console.error('mock 服务没起来')
  process.exit(1)
}

const chrome = spawn(BROWSER, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
  '--window-size=1440,900',
  `--remote-debugging-port=${CDP_PORT}`,
  `--user-data-dir=${path.join(ROOT, '.shots', '.cdp-profile')}`,
  'about:blank',
], { stdio: 'ignore' })

const cleanup = () => {
  try { chrome.kill() } catch { /* ignore */ }
  try { mock?.kill() } catch { /* ignore */ }
}
process.on('exit', cleanup)

if (!(await waitFor(`http://127.0.0.1:${CDP_PORT}/json/version`, 10000))) {
  console.error('Chrome 调试端口没起来')
  cleanup()
  process.exit(1)
}

class Session {
  constructor(ws) {
    this.ws = ws
    this.id = 0
    this.pending = new Map()
    this.events = []
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id)
        this.pending.delete(msg.id)
        msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result)
      } else if (msg.method) {
        this.events.push(msg)
      }
    })
  }

  send(method, params = {}) {
    const id = ++this.id
    this.ws.send(JSON.stringify({ id, method, params }))
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }))
  }
}

async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl)
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true })
    ws.addEventListener('error', reject, { once: true })
  })
  return new Session(ws)
}

const AUDIT = `(() => {
  const de = document.documentElement;
  const out = {
    overflowX: Math.max(0, de.scrollWidth - de.clientWidth),
    wide: [],
    clipped: [],
  };
  const describe = (el) => {
    const cls = typeof el.className === 'string' ? el.className.split(/\\s+/).slice(0, 2).join('.') : '';
    const text = (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 28);
    return (el.tagName.toLowerCase() + (cls ? '.' + cls : '') + ' 「' + text + '」');
  };
  for (const el of document.querySelectorAll('body *')) {
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    const cs = getComputedStyle(el);
    // 超出视口右侧（被裁掉）
    if (rect.right > window.innerWidth + 2 && cs.position !== 'fixed') {
      out.wide.push(describe(el));
    }
    // 内容被自身容器裁掉（text-overflow / overflow:hidden 的省略号属正常，跳过）
    if (cs.overflowX === 'visible' && el.scrollWidth > el.clientWidth + 4 && el.clientWidth > 0) {
      out.clipped.push(describe(el));
    }
  }
  out.wide = [...new Set(out.wide)].slice(0, 6);
  out.clipped = [...new Set(out.clipped)].slice(0, 6);
  return JSON.stringify(out);
})()`

let failed = 0
for (const [route] of PAGE_EXPECT) {
  const target = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' })).json()
  const s = await connect(target.webSocketDebuggerUrl)
  await s.send('Runtime.enable')
  await s.send('Log.enable')
  await s.send('Page.enable')

  await s.send('Page.navigate', { url: `http://127.0.0.1:${PORT}${route}` })
  await wait(2200)

  const res = await s.send('Runtime.evaluate', { expression: AUDIT, returnByValue: true })
  const audit = JSON.parse(res.result.value)

  const errors = s.events
    .filter((e) => e.method === 'Runtime.exceptionThrown')
    .map((e) => e.params.exceptionDetails.exception?.description || e.params.exceptionDetails.text)
    .concat(
      // Vue 的报错走 console.error（模板异常、属性未定义等），必须一起收
      s.events
        .filter((e) => e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error')
        .map((e) => e.params.args.map((a) => a.value ?? a.description ?? '').join(' ')),
    )
    .concat(
      s.events
        .filter((e) => e.method === 'Log.entryAdded' && e.params.entry.level === 'error')
        .map((e) => e.params.entry.text),
    )
    .filter((t) => t && !/favicon|net::ERR_|DevTools/.test(t))

  const problems = []
  if (errors.length) problems.push(`控制台报错 ${errors.length} 条：${errors[0].split('\n')[0].slice(0, 120)}`)
  if (audit.overflowX > 4) problems.push(`整页横向溢出 ${audit.overflowX}px`)
  if (audit.wide.length) problems.push(`超出视口：${audit.wide.join(' / ')}`)
  if (audit.clipped.length) problems.push(`内容被裁：${audit.clipped.join(' / ')}`)

  if (problems.length) {
    failed++
    console.log(`ISSUE ${route}`)
    problems.forEach((p) => console.log('      - ' + p))
  } else {
    console.log(`OK    ${route}`)
  }

  await fetch(`http://127.0.0.1:${CDP_PORT}/json/close/${target.id}`)
  s.ws.close()
}

// ---------------- 交互场景 ----------------
// 引擎自选：点卡片 → 高亮切换 + 偏好写入 localStorage（用户的选择要留得住）
{
  const target = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' })).json()
  const s = await connect(target.webSocketDebuggerUrl)
  await s.send('Runtime.enable')
  await s.send('Page.enable')
  await s.send('Page.navigate', { url: `http://127.0.0.1:${PORT}/generate` })
  await wait(2000)

  const clicked = JSON.parse(
    (
      await s.send('Runtime.evaluate', {
        expression: `(() => {
          const card = [...document.querySelectorAll('.engine-card')].find((el) => el.textContent.includes('AnimeDiff'));
          if (!card) return JSON.stringify({ ok: false, why: '没找到 AnimeDiff 卡片' });
          card.click();
          return JSON.stringify({ ok: true });
        })()`,
        returnByValue: true,
      })
    ).result.value,
  )
  await wait(300)

  const state = JSON.parse(
    (
      await s.send('Runtime.evaluate', {
        expression: `JSON.stringify({
          active: (document.querySelector('.engine-card.active')?.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 30),
          saved: localStorage.getItem('aivideo:engine'),
          badge: document.querySelector('.engine-card.active .ec-badge')?.textContent || '',
        })`,
        returnByValue: true,
      })
    ).result.value,
  )

  const ok = clicked.ok && state.active.includes('AnimeDiff') && state.saved === 'anime'
  if (ok) {
    console.log(`OK    交互：选 AnimeDiff → 高亮「${state.active}」· 偏好 ${state.saved}`)
  } else {
    failed++
    console.log(`ISSUE 交互：生成页选引擎失败 ${JSON.stringify({ clicked, ...state })}`)
  }
  await fetch(`http://127.0.0.1:${CDP_PORT}/json/close/${target.id}`)
  s.ws.close()
}

// 设置页：点 tab → URL 跟着变 + 面板真的换掉（别只是样式动了）
{
  const target = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' })).json()
  const s = await connect(target.webSocketDebuggerUrl)
  await s.send('Runtime.enable')
  await s.send('Page.enable')
  await s.send('Page.navigate', { url: `http://127.0.0.1:${PORT}/settings` })
  await wait(1800)

  const result = JSON.parse(
    (
      await s.send('Runtime.evaluate', {
        expression: `(() => {
          const tabs = [...document.querySelectorAll('.set-tabs button')];
          const env = tabs.find((b) => b.textContent.trim() === '环境');
          if (!env) return JSON.stringify({ ok: false, why: '没找到环境 tab', tabs: tabs.length });
          env.click();
          return JSON.stringify({ ok: true, tabs: tabs.map((b) => b.textContent.trim()) });
        })()`,
        returnByValue: true,
      })
    ).result.value,
  )
  await wait(500)

  const state = JSON.parse(
    (
      await s.send('Runtime.evaluate', {
        expression: `JSON.stringify({
          url: location.pathname + location.search,
          active: (document.querySelector('.set-tabs button.active')?.textContent || '').trim(),
          hasGuard: document.body.innerText.includes('资源守卫档位'),
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        })`,
        returnByValue: true,
      })
    ).result.value,
  )

  const ok = result.ok && state.active === '环境' && state.url.includes('tab=env')
    && state.hasGuard && state.overflow <= 1
  if (ok) {
    console.log(`OK    交互：设置页切「环境」→ URL ${state.url} · 渲染出资源守卫档位`)
  } else {
    failed++
    console.log(`ISSUE 交互：设置页 tab 切换失败 ${JSON.stringify({ result, ...state })}`)
  }
  await fetch(`http://127.0.0.1:${CDP_PORT}/json/close/${target.id}`)
  s.ws.close()
}

cleanup()
console.log(`\n${PAGE_EXPECT.length - failed}/${PAGE_EXPECT.length} 页面无异常`)
process.exit(failed ? 1 : 0)