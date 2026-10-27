// 检查前端调用契约：
// 1) api.xxx() 是否在 api.js 中定义
// 2) store 上使用的方法/状态是否在 store 中定义
// 3) 子组件 $emit('x') 与父组件 @x 监听是否对得上（含 emits 声明）
const fs = require('fs')
const path = require('path')

const src = path.resolve(__dirname, '../src')
const files = []
;(function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) walk(p)
    else if (/\.(vue|js)$/.test(e.name)) files.push(p)
  }
})(src)

const read = (f) => fs.readFileSync(f, 'utf-8')

// ---- 1) api ----
const apiSrc = read(path.join(src, 'api.js'))
const apiKeys = new Set()
for (const m of apiSrc.matchAll(/^\s{2}([A-Za-z_$][\w$]*)\s*:/gm)) apiKeys.add(m[1])

const apiProblems = []
for (const f of files) {
  if (f.endsWith('api.js')) continue
  const s = read(f)
  for (const m of s.matchAll(/\bapi\.([A-Za-z_$][\w$]*)\s*\(/g)) {
    if (!apiKeys.has(m[1])) apiProblems.push(`${path.relative(src, f)} → api.${m[1]}()`)
  }
}

// ---- 2) store ----
function collectStore(dir) {
  const names = new Set()
  const dirPath = path.join(src, dir)
  if (!fs.existsSync(dirPath)) return names
  for (const f of fs.readdirSync(dirPath)) {
    const s = read(path.join(dirPath, f))
    for (const m of s.matchAll(/^\s{4}(?:async\s+)?([A-Za-z_$][\w$]*)\s*\(/gm)) names.add(m[1])
    for (const m of s.matchAll(/^\s{4}([A-Za-z_$][\w$]*)\s*:/gm)) names.add(m[1])
  }
  return names
}
const storeNames = collectStore('store')
const storeProblems = []
for (const f of files) {
  if (f.includes(`${path.sep}store${path.sep}`)) continue
  const s = read(f)
  for (const m of s.matchAll(/\b(?:jobStore|store|projectStore|useJobsStore\(\)|useProjectStore\(\))\s*\.\s*([A-Za-z_$][\w$]*)\s*\(/g)) {
    if (!storeNames.has(m[1])) storeProblems.push(`${path.relative(src, f)} → store.${m[1]}()`)
  }
}

// ---- 3) emits ----
// 组件声明的 emits + $emit 调用
const emitDecl = new Map() // 组件名 -> Set
const emitCalls = new Map()
for (const f of files.filter((x) => x.endsWith('.vue'))) {
  const name = path.basename(f, '.vue')
  const s = read(f)
  const decl = new Set()
  const block = s.match(/defineEmits\(\[([\s\S]*?)\]\)/)
  if (block) for (const m of block[1].matchAll(/['"]([\w:-]+)['"]/g)) decl.add(m[1])
  if (decl.size) emitDecl.set(name, decl)
  const calls = new Set()
  for (const m of s.matchAll(/\$emit\(\s*['"]([\w:-]+)['"]/g)) calls.add(m[1])
  for (const m of s.matchAll(/\bemit\(\s*['"]([\w:-]+)['"]/g)) calls.add(m[1])
  if (calls.size) emitCalls.set(name, calls)
}
const emitProblems = []
for (const [comp, calls] of emitCalls) {
  const decl = emitDecl.get(comp)
  if (!decl) continue
  for (const c of calls) if (!decl.has(c) && c !== 'update:modelValue') emitProblems.push(`${comp} → $emit('${c}') 未在 emits 声明`)
}

const dump = (title, list) => {
  console.log(`\n== ${title} ==`)
  if (!list.length) console.log('  无问题')
  else list.forEach((x) => console.log('  ' + x))
}
dump('api 调用', [...new Set(apiProblems)])
dump('store 调用', [...new Set(storeProblems)])
dump('emits 契约', [...new Set(emitProblems)])
