/**
 * 页面 SSR 冒烟：把每个路由页面真实渲染一遍（setup + 模板都执行），
 * 用于抓「一打开就白屏 / 模板访问 undefined」这类构建期查不出的 bug。
 *
 * 不依赖浏览器：vite 以 middleware 模式提供 SFC 转换，@vue/server-renderer 渲染。
 * fetch 全部打桩，避免真的连后端。
 */
import { createServer } from 'vite'
import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

const PAGES = [
  ['/', 'HomeView'],
  ['/launch', 'LaunchView'],
  ['/generate', 'GenerateView'],
  ['/queue', 'QueueView'],
  ['/gallery', 'GalleryView'],
  ['/projects', 'ProjectsView'],
  ['/novel', 'NovelAdaptationView'],
  ['/projects/demo-project', 'ProjectView'],
  ['/comfy', 'ComfySetupView'],
  // 设置页三段各渲染一遍：服务面板默认挂载，环境/偏好靠 query 切
  ['/settings', 'SettingsView'],
  ['/settings?tab=env', 'SettingsView'],
  ['/settings?tab=prefs', 'SettingsView'],
]

// ---- 浏览器 API 打桩 ----
const mem = new Map()
globalThis.localStorage = {
  getItem: (k) => (mem.has(k) ? mem.get(k) : null),
  setItem: (k, v) => mem.set(k, String(v)),
  removeItem: (k) => mem.delete(k),
}
globalThis.window = globalThis.window || { location: { href: 'http://127.0.0.1:8000/' } }

// ---- fetch 打桩：返回结构合理的空数据，让页面走「空状态」分支 ----
const EMPTY = {
  '/api/health': { status: 'ok', comfyui: false },
  '/api/engines': [],
  '/api/jobs': [],
  '/api/presets': [],
  '/api/hardware': null,
  '/api/projects': [],
  '/api/services/status': {},
  '/api/services/config': {},
  '/api/services/mcp': {},
  '/api/comfy/status': { available: false },
  '/api/comfy/workflow': {},
  '/api/comfy/presets': [],
  '/api/comfy/capabilities': {},
  '/api/novels': [],
  '/api/gallery': [],
  '/api/assets': [],
}
globalThis.fetch = async (url) => {
  const path = String(url).split('?')[0]
  const body = path in EMPTY ? EMPTY[path] : (path.startsWith('/api/') ? {} : [])
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => body,
    text: async () => JSON.stringify(body),
  }
}

const server = await createServer({
  server: { middlewareMode: true },
  appType: 'custom',
  logLevel: 'error',
})

let failed = 0
for (const [path, name] of PAGES) {
  try {
    const mod = await server.ssrLoadModule(`/src/views/${name}.vue`)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/:pathMatch(.*)*', component: mod.default }],
    })
    await router.push(path)
    await router.isReady()

    const app = createSSRApp(mod.default)
    app.use(createPinia()).use(router)
    // 记录渲染期的 Vue 警告（未定义属性等）
    const warnings = []
    app.config.warnHandler = (msg) => warnings.push(msg)

    const html = await renderToString(app)
    const empty = html.replace(/<!--.*?-->/g, '').trim().length < 40
    const bad = warnings.filter((w) => /is not defined|was accessed during render|Invalid/.test(w))
    if (empty) {
      failed++
      console.log(`EMPTY  ${path.padEnd(22)} ${name} 渲染结果几乎为空`)
    } else if (bad.length) {
      failed++
      console.log(`WARN   ${path.padEnd(22)} ${name}`)
      bad.slice(0, 3).forEach((w) => console.log('        ' + w))
    } else {
      console.log(`OK     ${path.padEnd(22)} ${name}  ${html.length} 字节`)
    }
  } catch (e) {
    failed++
    console.log(`FAIL   ${path.padEnd(22)} ${name}: ${e.message.split('\n')[0]}`)
  }
}

await server.close()
console.log(`\n${PAGES.length - failed}/${PAGES.length} 页面渲染通过`)
process.exit(failed ? 1 : 0)
