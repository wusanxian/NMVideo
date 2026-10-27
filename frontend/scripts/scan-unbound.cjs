// 扫描所有 .vue：模板里引用了 setup 里不存在的标识符 → 运行时会渲染失败/报错
// 原理：compileTemplate 带上 bindingMetadata 后，已知绑定会编译成 $setup.x，
// 未知绑定则落到 _ctx.x。只把后者当成可疑项。
const fs = require('fs')
const path = require('path')
const { parse, compileScript, compileTemplate } = require('@vue/compiler-sfc')

const root = path.resolve(__dirname, '../src')
const files = []
;(function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) walk(p)
    else if (e.name.endsWith('.vue')) files.push(p)
  }
})(root)

// 这些是 _ctx 上合法存在的（组件实例自带 / 全局注册）
const ALLOW = new Set(['$props', '$emit', '$attrs', '$slots', '$el', '$router', '$route', '$store', '$refs', '$parent', '$root'])

let problems = 0
for (const file of files) {
  const source = fs.readFileSync(file, 'utf-8')
  const id = Buffer.from(file).toString('hex').slice(0, 8)
  const { descriptor, errors } = parse(source, { filename: file })
  if (errors.length) {
    console.log(`PARSE  ${path.relative(root, file)}: ${errors.map((e) => e.message).join('; ')}`)
    problems++
    continue
  }
  if (!descriptor.template) continue
  let bindings = {}
  try {
    bindings = compileScript(descriptor, { id }).bindings
  } catch (e) {
    console.log(`SCRIPT ${path.relative(root, file)}: ${e.message.split('\n')[0]}`)
    problems++
    continue
  }
  const res = compileTemplate({
    source: descriptor.template.content,
    filename: file,
    id,
    compilerOptions: { bindingMetadata: bindings, prefixIdentifiers: true },
  })
  if (res.errors.length) {
    console.log(`TPL    ${path.relative(root, file)}: ${res.errors.map((e) => e.message || e).join('; ')}`)
    problems++
    continue
  }
  const missing = new Set()
  for (const m of res.code.matchAll(/_ctx\.([A-Za-z_$][\w$]*)/g)) {
    if (!ALLOW.has(m[1])) missing.add(m[1])
  }
  if (missing.size) {
    console.log(`UNDEF  ${path.relative(root, file)}: ${[...missing].join(', ')}`)
    problems++
  }
}
console.log(`\n扫描 ${files.length} 个组件，可疑 ${problems} 处`)
