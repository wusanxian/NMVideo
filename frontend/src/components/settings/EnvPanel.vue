<template>
  <div>
    <!-- ComfyUI 地址 -->
    <div class="set-group">ComfyUI 服务</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">服务地址</div>
          <div class="set-row-desc">本机或局域网内的 ComfyUI。改完点保存，配置写进后端 settings.json。</div>
        </div>
        <div class="set-row-ctl wide">
          <span class="set-badge" :class="comfy.online ? 'on' : 'off'">{{ comfy.online ? '已连接' : '未连接' }}</span>
          <input class="set-input mono" v-model="form.comfy_url" placeholder="http://127.0.0.1:8188" />
          <button class="btn" :disabled="busy.save" @click="save">{{ busy.save ? '保存中…' : '保存' }}</button>
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">当前状态</div>
          <div class="set-row-desc">
            <template v-if="comfy.online">
              队列：运行 {{ comfy.queue_running || 0 }} 个 · 排队 {{ comfy.queue_pending || 0 }} 个<template
                v-if="comfy.vram_total_gb"> · 显存 {{ comfy.vram_free_gb }} / {{ comfy.vram_total_gb }} GB</template>
            </template>
            <template v-else>未检测到服务——先启动 ComfyUI，再点「重新检测」。</template>
          </div>
        </div>
        <div class="set-row-ctl">
          <button class="btn" :disabled="busy.check" @click="refresh">{{ busy.check ? '检测中…' : '重新检测' }}</button>
        </div>
      </div>
      <div class="set-hint">
        ComfyUI 由你自行启动（<code>python main.py --listen</code> 才能被其他设备访问）。
        本产品只提交任务，不托管它的 Python 环境与启动进程。
      </div>
    </div>

    <!-- 目录 -->
    <div class="set-group">目录 · 只读</div>
    <div class="set-card">
      <div v-for="d in dirs" :key="d.key" class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">{{ d.label }}</div>
          <div class="set-row-desc set-mono">{{ d.path || '—' }}</div>
        </div>
        <div class="set-row-ctl">
          <span v-if="d.extra" class="set-badge">{{ d.extra }}</span>
          <button class="set-link" @click="copy(d.path, d.key)">{{ copied === d.key ? '已复制 ✓' : '复制' }}</button>
          <button v-if="canOpen" class="set-link" @click="open(d.path)">打开</button>
        </div>
      </div>
      <div class="set-hint">
        目录由后端配置决定（环境变量 <code>AIVIDEO_DATA_DIR</code> 等），这里只展示不修改，避免界面和进程各记一份。
      </div>
    </div>

    <!-- 工作流模板 -->
    <div class="set-group">工作流模板</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">模板与参数映射</div>
          <div class="set-row-desc">
            ComfyUI 里「Save (API Format)」导出的 JSON，按节点 ID 把提示词、尺寸、帧数、种子映射进去；
            缺节点/缺模型在编辑器里会逐条标红，不会等到出片时才失败。
          </div>
        </div>
        <div class="set-row-ctl">
          <router-link class="btn" to="/comfy">打开模板编辑器</router-link>
        </div>
      </div>
    </div>

    <!-- 兼容性 -->
    <div class="set-group">兼容性</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">显卡</div>
          <div class="set-row-desc">{{ hw.gpu_name || '未检测到 NVIDIA 显卡' }}</div>
        </div>
        <div class="set-row-ctl">
          <span class="set-badge">{{ vramText }}</span>
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">内存</div>
          <div class="set-row-desc">可用 {{ num(hw.available_ram_gb) }} / 总计 {{ num(hw.ram_gb) }} GB</div>
        </div>
        <div class="set-row-ctl">
          <span class="set-badge" :class="ramBadge">{{ ramBadgeText }}</span>
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">模型盘剩余空间</div>
          <div class="set-row-desc">低于 10GB 时新模型放不下，会先失败在下载/落盘。</div>
        </div>
        <div class="set-row-ctl">
          <span class="set-badge" :class="diskBadge">{{ diskText }}</span>
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">资源守卫档位</div>
          <div class="set-row-desc">{{ guardDesc }}</div>
        </div>
        <div class="set-row-ctl">
          <span class="set-badge" :class="guardBadge">{{ guardLabel }}</span>
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">运行中看门狗</div>
          <div class="set-row-desc">
            生成期间每几秒采一次可用内存，连续跌破 {{ num(floor) }}GB 硬底才中止——
            不是一抖就杀任务。
          </div>
        </div>
        <div class="set-row-ctl">
          <span class="set-badge" :class="watchdog ? 'on' : 'off'">{{ watchdog ? '已开启' : '已关闭' }}</span>
        </div>
      </div>
      <div class="set-hint">
        这两个开关读的是后端环境变量（<code>AIVIDEO_RESOURCE_GUARD</code> / <code>AIVIDEO_RESOURCE_WATCHDOG</code>），
        改完要重启后端才生效，所以这里只显示、不提供假开关。
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../../api'

const props = defineProps({
  launch: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['refresh'])

const form = reactive({ comfy_url: '' })
const busy = reactive({ save: false, check: false })
const copied = ref('')
const canOpen = computed(() => typeof window !== 'undefined' && !!window.desk && !!window.desk.openPath)

const comfy = computed(() => props.launch.comfyui || {})
const hw = computed(() => props.launch.hardware || {})
const resource = computed(() => props.launch.resource || {})
const floor = computed(() => resource.value.ram_floor_gb ?? 1)
const watchdog = computed(() => !!resource.value.watchdog)

const num = (v) => (v === null || v === undefined || v === '' || Number(v) < 0 ? '—' : Number(v).toFixed(1))

const dirs = computed(() => {
  const p = props.launch.paths || {}
  const gb = (o) => (o && o.size_gb ? `${Number(o.size_gb).toFixed(1)} GB` : '')
  return [
    { key: 'project', label: '程序目录', path: p.project || '' },
    { key: 'models', label: '模型目录', path: (p.models && p.models.path) || '', extra: gb(p.models) },
    { key: 'outputs', label: '输出目录', path: (p.outputs && p.outputs.path) || '', extra: gb(p.outputs) },
    { key: 'db', label: '数据文件', path: p.db || '' },
  ]
})

const vramText = computed(() => {
  if (!hw.value.vram_gb) return '显存未知'
  const free = hw.value.free_vram_gb
  return free === null || free === undefined
    ? `${hw.value.vram_gb} GB`
    : `空闲 ${Number(free).toFixed(1)} / ${hw.value.vram_gb} GB`
})

const ramBadge = computed(() => {
  const v = Number(hw.value.available_ram_gb)
  if (!v || v < 0) return ''
  return v < floor.value + 1.5 ? 'warn' : 'on'
})
const ramBadgeText = computed(() => (ramBadge.value === 'warn' ? '偏紧' : ramBadge.value === 'on' ? '充足' : '未知'))

const diskText = computed(() => {
  const v = Number((props.launch.disk || {}).free_gb)
  if (!v || v < 0) return '未知'
  return `剩余 ${v.toFixed(0)} GB`
})
const diskBadge = computed(() => {
  const v = Number((props.launch.disk || {}).free_gb)
  if (!v || v < 0) return ''
  return v < 10 ? 'warn' : 'on'
})

const GUARDS = {
  enforce: ['准入检查 + 自动降档', '开跑前估算需求，不够就降分辨率/帧数；低于下限直接拒绝，并写清原因。', 'on'],
  warn: ['只提示，不拦', '仍然估算并写提示，但不降档也不拒绝——排错时用。', 'warn'],
  off: ['已关闭', '完全按你填的参数执行；显存/内存不够时由 ComfyUI 或 torch 自己报错。', 'off'],
}
const guardLabel = computed(() => (GUARDS[resource.value.guard] || GUARDS.enforce)[0])
const guardDesc = computed(() => (GUARDS[resource.value.guard] || GUARDS.enforce)[1])
const guardBadge = computed(() => (GUARDS[resource.value.guard] || GUARDS.enforce)[2])

async function refresh() {
  busy.check = true
  try {
    emit('refresh')
    const c = await api.servicesConfig()
    form.comfy_url = c.comfy_url || ''
  } catch {
    /* ignore */
  } finally {
    busy.check = false
  }
}

async function save() {
  busy.save = true
  try {
    await api.saveComfyConfig({ comfy_url: form.comfy_url.trim() })
    emit('refresh')
    const c = await api.servicesConfig()
    form.comfy_url = c.comfy_url || form.comfy_url
  } catch (e) {
    alert(e.message)
  } finally {
    busy.save = false
  }
}

async function copy(text, key) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    copied.value = key
    setTimeout(() => { if (copied.value === key) copied.value = '' }, 1600)
  } catch {
    /* 剪贴板被拒就什么都不做 */
  }
}

function open(path) {
  if (!path || !canOpen.value) return
  window.desk.openPath(path).catch(() => copy(path, 'open'))
}

onMounted(async () => {
  try {
    const c = await api.servicesConfig()
    form.comfy_url = c.comfy_url || (props.launch.comfyui || {}).url || ''
  } catch {
    form.comfy_url = (props.launch.comfyui || {}).url || ''
  }
})
</script>

<style scoped>
code {
  background: var(--bg); border: 1px solid var(--border); border-radius: 5px;
  padding: 1px 5px; font-size: 12px; font-family: ui-monospace, monospace;
}
</style>
