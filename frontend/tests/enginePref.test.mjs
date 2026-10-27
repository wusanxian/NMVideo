/**
 * 引擎偏好行为测试（零依赖，node --test）
 *
 * 覆盖"用户自选"这条约定的关键语义：
 * - 第一次打开回落到默认引擎；
 * - 选过就记住；
 * - 偏好可用时不被动摇；
 * - 偏好不可用时只做临时回退（fallback），不改写偏好。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
}

const { DEFAULT_ENGINE_ID, getEnginePref, setEnginePref, resolveEngine, engineName } =
  await import('../src/enginePref.js')

const ready = (id, channel) => ({ id, status: 'ready', execution_channel: channel })
const down = (id, channel) => ({ id, status: 'unavailable', execution_channel: channel, name: `${id}-name` })

test('第一次打开回落到默认引擎', () => {
  store.clear()
  assert.equal(getEnginePref(), DEFAULT_ENGINE_ID)
})

test('选过之后记住用户的选择', () => {
  setEnginePref('anime')
  assert.equal(getEnginePref(), 'anime')
})

test('偏好引擎可用时不被替换', () => {
  const list = [ready('comfyui', 'comfyui'), ready('anime', 'native')]
  assert.deepEqual(resolveEngine(list, 'anime'), { engine: 'anime', fallback: false })
})

test('偏好引擎不可用时只做临时回退', () => {
  const list = [ready('comfyui', 'comfyui'), down('anime', 'native')]
  assert.deepEqual(resolveEngine(list, 'anime'), { engine: 'comfyui', fallback: true })
})

test('引擎列表未加载时保留用户选择', () => {
  assert.deepEqual(resolveEngine([], 'wan22'), { engine: 'wan22', fallback: false })
})

test('全部不可用时不擅自换引擎', () => {
  const list = [down('anime', 'native'), down('comfyui', 'comfyui')]
  assert.deepEqual(resolveEngine(list, 'anime'), { engine: 'anime', fallback: false })
})

test('引擎名缺失时回落 id', () => {
  assert.equal(engineName([ready('comfyui', 'comfyui')], 'nope'), 'nope')
})
