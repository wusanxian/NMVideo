<template>
  <div class="page launch">
    <header class="launch-head">
      <h1>工作空间</h1>
      <div class="page-sub">{{ headline }}</div>
    </header>

    <!-- 主状态卡：整体就绪度 + 三步检查（三步都是真探测，不是假进度） -->
    <section class="panel status-card">
      <div class="sc-top">
        <div class="sc-logo" :class="{ ready: data.ready }">N</div>
        <div class="sc-text">
          <div class="sc-title">{{ title }}</div>
          <div class="sc-desc">{{ desc }}</div>
        </div>
      </div>
      <div class="sc-bar">
        <div class="sc-bar-inner" :class="{ ready: data.ready }" :style="{ width: pct + '%' }"></div>
      </div>
      <div class="sc-steps">
        <div v-for="s in data.steps" :key="s.key" class="step" :class="s.state">
          <span class="step-dot"></span>
          <span class="step-label">{{ s.label }}</span>
          <span class="step-detail" :title="s.detail">{{ s.detail }}</span>
        </div>
      </div>
    </section>

    <!-- 安装信息：路径都来自后端实况，点击可复制 -->
    <section class="panel">
      <div class="panel-head">安装信息</div>
      <div class="info-row">
        <span class="ir-label">ComfyUI</span>
        <span class="ir-value mono">{{ data.comfyui.url || '—' }}</span>
        <span class="ir-flag" :class="data.comfyui.online ? 'on' : 'off'">
          {{ data.comfyui.online ? '在线' : '未连接' }}
        </span>
      </div>
      <div class="info-row">
        <span class="ir-label">模型目录</span>
        <button class="ir-value mono link" :title="'点击复制'" @click="copy(data.paths.models.path)">
          {{ data.paths.models.path }}
        </button>
        <span class="ir-flag">{{ gb1(data.paths.models.size_gb) }} · {{ data.paths.models.files || 0 }} 文件</span>
      </div>
      <div class="info-row">
        <span class="ir-label">输出目录</span>
        <button class="ir-value mono link" :title="'点击复制'" @click="copy(data.paths.outputs.path)">
          {{ data.paths.outputs.path }}
        </button>
        <span class="ir-flag">{{ gb1(data.paths.outputs.size_gb) }}</span>
      </div>
      <div class="info-row">
        <span class="ir-label">数据文件</span>
        <span class="ir-value mono">{{ data.paths.db }}</span>
        <span class="ir-flag">{{ data.version ? 'v' + data.version : '' }}</span>
      </div>
    </section>

    <!-- 模型与硬件：出片能不能跑，看的是这张卡 -->
    <section class="panel">
      <div class="panel-head">模型与硬件</div>
      <div class="info-row">
        <span class="ir-label">显卡</span>
        <span class="ir-value">{{ data.hardware.gpu_name || '未检测到独立显卡' }}</span>
        <span class="ir-flag">
          {{ data.hardware.vram_gb ? data.hardware.vram_gb + ' GB' : '—' }}
        </span>
      </div>
      <div class="info-row">
        <span class="ir-label">内存</span>
        <span class="ir-value">{{ data.hardware.ram_gb ? data.hardware.ram_gb + ' GB' : '—' }}</span>
        <span class="ir-flag">
          可用 {{ fmtGb(data.hardware.available_ram_gb) }}
        </span>
      </div>
      <div class="info-row">
        <span class="ir-label">磁盘</span>
        <span class="ir-value">
          {{ data.disk.free_gb >= 0 ? data.disk.free_gb + ' GB 可用' : '未知' }}
        </span>
        <span class="ir-flag">
          {{ data.disk.total_gb > 0 ? '共 ' + data.disk.total_gb + ' GB' : '' }}
        </span>
      </div>
      <div class="info-row">
        <span class="ir-label">ComfyUI 队列</span>
        <span class="ir-value">
          运行 {{ data.comfyui.queue_running }} · 排队 {{ data.comfyui.queue_pending }}
        </span>
        <span class="ir-flag">
          {{ data.comfyui.online ? '显存 ' + data.comfyui.vram_free_gb + ' / ' + data.comfyui.vram_total_gb + ' GB' : '' }}
        </span>
      </div>
    </section>

    <footer class="launch-actions">
      <button class="btn" :disabled="checking" @click="refresh(true)">
        {{ checking ? '检测中…' : '重新检测' }}
      </button>
      <a
        class="btn"
        :class="{ disabled: !data.comfyui.online }"
        :href="data.comfyui.online ? data.comfyui.url : undefined"
        target="_blank"
        rel="noreferrer"
      >打开 ComfyUI</a>
      <router-link class="btn" to="/settings">模型与设置</router-link>
      <span class="launch-tip">{{ error || autoTip }}</span>
    </footer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from '../api'

const empty = {
  ok: false,
  ready: false,
  version: '',
  progress: 0,
  steps: [],
  comfyui: { url: '', online: false, vram_total_gb: 0, vram_free_gb: 0, queue_running: 0, queue_pending: 0 },
  paths: {
    project: '', backend: '', db: '',
    models: { path: '', size_gb: 0, files: 0 },
    outputs: { path: '', size_gb: 0, files: 0 },
  },
  disk: { free_gb: -1, total_gb: -1 },
  hardware: { gpu_name: '', vram_gb: 0, ram_gb: 0, available_ram_gb: null, device: '' },
}

const data = reactive(JSON.parse(JSON.stringify(empty)))
const checking = ref(false)
const error = ref('')
let timer = null

const pct = computed(() => Math.round((data.progress || 0) * 100))
const headline = computed(() => {
  if (error.value) return '后端已连接，但状态读取失败'
  if (data.ready) return '一切就绪，可以开始创作'
  return '后台正在启动，完成后可以操作'
})
const title = computed(() => {
  if (data.ready) return '环境已就绪'
  if (!data.comfyui.online) return '正在启动 ComfyUI'
  return '正在检查生成引擎'
})
const desc = computed(() => {
  if (data.ready) return '本地服务与 ComfyUI 均已连接。'
  if (!data.comfyui.online) return '等待 ComfyUI 服务响应；启动后本页会自动刷新。'
  return 'ComfyUI 已连接，正在确认引擎与模型。'
})
const autoTip = computed(() => (data.ready ? '本页每 10 秒自动复检' : '本页每 2 秒自动检测'))

const fmtGb = (v) => (v === null || v === undefined || v < 0 ? '—' : `${v} GB`)
// 目录体积可能是旧后端缺字段 / null，别让模板抛错把整页打白
const gb1 = (v) => `${Number(v || 0).toFixed(1)} GB`

async function refresh(manual = false) {
  if (manual) checking.value = true
  try {
    const got = await api.launchStatus()
    Object.assign(data, got)
    error.value = ''
    schedule(got.ready ? 10000 : 2000)
  } catch (e) {
    error.value = `状态读取失败：${e.message}`
    schedule(3000)
  } finally {
    checking.value = false
  }
}

function schedule(ms) {
  clearTimeout(timer)
  timer = setTimeout(() => refresh(false), ms)
}

async function copy(text) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    error.value = '路径已复制'
    setTimeout(() => (error.value = ''), 1500)
  } catch {
    /* 剪贴板不可用（非 https / 无权限）时静默 */
  }
}

onMounted(() => refresh(true))
onUnmounted(() => clearTimeout(timer))
</script>

<style scoped>
.launch { max-width: 860px; margin: 0 auto; }

.launch-head { text-align: center; margin: 12px 0 26px; }
.launch-head h1 { font-size: 26px; letter-spacing: 2px; margin: 0 0 8px; }
.launch-head .page-sub { color: var(--text-dim); }

.status-card { padding: 22px 22px 6px; }

.sc-top { display: flex; align-items: center; gap: 16px; }
.sc-logo {
  width: 46px; height: 46px; flex: none;
  border-radius: 10px;
  display: grid; place-items: center;
  font-weight: 800; font-size: 20px; color: #fff;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  box-shadow: 0 6px 18px rgba(var(--accent-rgb), .35);
}
.sc-logo.ready { box-shadow: 0 6px 18px rgba(52, 211, 153, .3); background: linear-gradient(135deg, #10b981, #34d399); }

.sc-title { font-size: 17px; font-weight: 600; margin-bottom: 4px; }
.sc-desc { font-size: 12.5px; color: var(--text-dim); }

.sc-bar { height: 6px; border-radius: 4px; background: var(--bg-card); margin: 16px 0 14px; overflow: hidden; }
.sc-bar-inner {
  height: 100%; border-radius: 4px;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  transition: width .5s ease;
}
.sc-bar-inner.ready { background: linear-gradient(90deg, #10b981, #34d399); }

.sc-steps { display: flex; flex-direction: column; gap: 2px; padding-bottom: 14px; }
.step { display: flex; align-items: center; gap: 10px; font-size: 12.5px; padding: 6px 0; }
.step-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text-dim); flex: none; }
.step.done .step-dot { background: var(--green); box-shadow: 0 0 6px rgba(52, 211, 153, .55); }
.step.blocked .step-dot { background: var(--yellow); }
.step-label { color: var(--text); min-width: 74px; }
.step-detail {
  color: var(--text-dim);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.panel-head { font-size: 13px; font-weight: 600; color: var(--text); margin-bottom: 12px; }

.info-row {
  display: flex; align-items: center; gap: 12px;
  padding: 9px 0; border-top: 1px solid var(--border);
  font-size: 12.5px;
}
.info-row:first-of-type { border-top: none; }
.ir-label { width: 96px; flex: none; color: var(--text-dim); }
.ir-value { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.link {
  background: none; border: none; padding: 0; text-align: left;
  color: var(--text); cursor: pointer; font-family: inherit;
}
.link:hover { color: var(--accent-2); }
.ir-flag { flex: none; color: var(--text-dim); font-size: 12px; }
.ir-flag.on { color: var(--green); }
.ir-flag.off { color: var(--yellow); }

.launch-actions { display: flex; align-items: center; gap: 10px; padding: 4px 0 20px; }
.launch-actions .btn { text-decoration: none; display: inline-flex; align-items: center; }
.launch-actions .btn.disabled { opacity: .45; pointer-events: none; }
.launch-tip { margin-left: auto; font-size: 12px; color: var(--text-dim); }
</style>
