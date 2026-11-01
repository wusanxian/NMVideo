<template>
  <div>
    <div class="set-group">模型库 · 只在你点了才下</div>
    <div class="set-card">
      <div v-for="m in items" :key="m.id" class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">
            {{ m.label }}
            <span class="m-repo">{{ m.repo }}</span>
          </div>
          <div class="set-row-desc">{{ m.detail }}</div>

          <div v-if="isRunning(m)" class="progress">
            <div class="bar"><div class="bar-inner" :style="{ width: m.task.percent + '%' }"></div></div>
            <span class="progress-text">
              {{ m.task.percent }}% · 已下 {{ m.task.gained_gb }} / 约 {{ m.approx_gb }}GB
            </span>
          </div>
          <div v-if="isRunning(m) && lastLine(m)" class="m-log">{{ lastLine(m) }}</div>
          <div v-if="msg[m.id]" class="m-msg">{{ msg[m.id] }}</div>
        </div>

        <div class="set-row-ctl">
          <span class="set-badge" :class="badgeClass(m)">{{ badgeText(m) }}</span>
          <button v-if="isRunning(m)" class="btn btn-sm" :disabled="busy[m.id]" @click="cancel(m)">
            {{ busy[m.id] ? '停止中…' : '取消' }}
          </button>
          <button
            v-else-if="m.state !== 'ready'"
            class="btn btn-sm"
            :disabled="busy[m.id] || state.downloading"
            @click="download(m)"
          >
            {{ busy[m.id] ? '启动中…' : (m.state === 'partial' ? '继续下载' : `下载约 ${m.approx_gb}GB`) }}
          </button>
        </div>
      </div>

      <div class="set-hint">
        视频权重不会自动下载：几十 GB 占不占这块盘由你决定，本软件只负责把它下对、能续传。
        H3 的素材例外——那批权重由 ComfyUI 侧准备（谁跑推理谁放权重），这里不重复占一份。
      </div>
    </div>

    <div v-if="err" class="m-msg">{{ err }}</div>

    <div class="set-group">磁盘</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">模型目录</div>
          <div class="set-row-desc set-mono">{{ state.models_dir || '—' }}</div>
        </div>
        <div class="set-row-ctl">
          <span class="set-badge" :class="diskClass">{{ diskText }}</span>
          <button v-if="canOpen" class="set-link" @click="openDir">打开</button>
        </div>
      </div>
      <div class="set-hint">
        下载支持断点续传：中途取消或断网，再点一次会从中断处继续，已下分片不会重来。
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from '../../api'

const state = ref({ items: [], downloading: false, models_dir: '', disk_free_gb: -1 })
const busy = reactive({})
const msg = reactive({})
const err = ref('')
let timer = null

const items = computed(() => state.value.items || [])
const canOpen = computed(() => typeof window !== 'undefined' && !!window.desk && !!window.desk.openPath)

const diskText = computed(() => {
  const v = Number(state.value.disk_free_gb)
  if (!v || v < 0) return '未知'
  return `剩余 ${v.toFixed(0)} GB`
})
const diskClass = computed(() => {
  const v = Number(state.value.disk_free_gb)
  if (!v || v < 0) return ''
  return v < 10 ? 'warn' : 'on'
})

const isRunning = (m) => !!m.task && m.task.state === 'running'

const BADGE = {
  ready: ['已就绪', 'on'],
  partial: ['下载不全', 'warn'],
  absent: ['未下载', 'off'],
}

function badgeText(m) {
  if (isRunning(m)) return '下载中'
  return (BADGE[m.state] || ['未知', 'off'])[0]
}
function badgeClass(m) {
  if (isRunning(m)) return 'warn'
  return (BADGE[m.state] || ['', 'off'])[1]
}
function lastLine(m) {
  const tail = (m.task && m.task.tail) || []
  return tail.length ? tail[tail.length - 1] : ''
}

async function load() {
  try {
    state.value = await api.listModels()
    err.value = ''
  } catch (e) {
    err.value = `读取模型状态失败：${e.message}`
  }
  schedule()
}

function schedule() {
  clearTimeout(timer)
  // 只在真有任务在跑时轮询：闲时每 2.5 秒打一次接口纯属浪费
  if (state.value.downloading) timer = setTimeout(load, 2500)
}

async function download(m) {
  busy[m.id] = true
  msg[m.id] = ''
  try {
    const r = await api.downloadModel(m.id)
    msg[m.id] = r.message || '已开始下载'
  } catch (e) {
    msg[m.id] = e.message
  } finally {
    busy[m.id] = false
    await load()
  }
}

async function cancel(m) {
  busy[m.id] = true
  try {
    const r = await api.cancelModelDownload(m.id)
    msg[m.id] = r.message || '正在停止…'
  } catch (e) {
    msg[m.id] = e.message
  } finally {
    busy[m.id] = false
    await load()
  }
}

function openDir() {
  if (!state.value.models_dir) return
  window.desk.openPath(state.value.models_dir).catch(() => {})
}

onMounted(load)
onUnmounted(() => clearTimeout(timer))
</script>

<style scoped>
.m-repo {
  font-size: 11.5px;
  color: var(--text-dim);
  font-family: ui-monospace, monospace;
  margin-left: 6px;
}
.m-log {
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--text-dim);
  font-family: ui-monospace, monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.m-msg {
  margin-top: 6px;
  font-size: 12px;
  color: var(--accent);
}
</style>
