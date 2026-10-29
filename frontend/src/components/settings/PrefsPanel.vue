<template>
  <div>
    <!-- 界面 -->
    <div class="set-group">界面</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">语言</div>
          <div class="set-row-desc">目前只有简体中文。英文没做，所以那个按钮是灰的——不糊弄你。</div>
        </div>
        <div class="set-row-ctl">
          <Segmented v-model="lang" :options="langs" />
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">配色</div>
          <div class="set-row-desc">蓝黑深色主题，跟系统主题无关（桌面壳标题栏同一套色）。</div>
        </div>
        <div class="set-row-ctl">
          <span class="set-badge">深色 · 固定</span>
        </div>
      </div>
    </div>

    <!-- 更新 -->
    <div class="set-group">更新</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">检查更新</div>
          <div class="set-row-desc">{{ updateDesc }}</div>
        </div>
        <div class="set-row-ctl">
          <button class="btn" :disabled="!hasDesk || updateState === 'checking'" @click="checkUpdate">
            {{ updateState === 'checking' ? '检查中…' : '检查更新' }}
          </button>
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">当前版本</div>
          <div class="set-row-desc">版本号来自打包信息（<code>AIVIDEO_VERSION</code> 或 desktop/package.json）。</div>
        </div>
        <div class="set-row-ctl">
          <span class="set-badge">{{ version ? 'v' + version : '未知' }}</span>
        </div>
      </div>
      <div class="set-foot">版本与更新说明 · {{ version ? 'v' + version : '未打包' }}</div>
    </div>

    <!-- 存储 -->
    <div class="set-group">存储</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">模型与输出占用</div>
          <div class="set-row-desc">
            模型 {{ gb(paths.models) }}<template v-if="paths.models && paths.models.files">（{{ paths.models.files }} 个文件）</template>
            · 输出 {{ gb(paths.outputs) }}
          </div>
        </div>
        <div class="set-row-ctl">
          <button class="btn btn-sm" @click="open(paths.models && paths.models.path)">打开模型目录</button>
          <button class="btn btn-sm" @click="open(paths.outputs && paths.outputs.path)">打开输出目录</button>
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">磁盘</div>
          <div class="set-row-desc">模型盘剩余 {{ diskText }}，低于 10GB 时新模型会放不下。</div>
        </div>
        <div class="set-row-ctl">
          <span class="set-badge" :class="diskBadge">{{ diskBadge === 'warn' ? '偏紧' : '充足' }}</span>
        </div>
      </div>
      <div class="set-hint">
        清理只删输出视频，不动模型；真要删模型请自己去目录操作——这里没有「一键清空」，
        那种按钮点错一次就是几十 GB 重新下载。
      </div>
    </div>

    <!-- 关于 -->
    <div class="set-group">关于</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">运行方式</div>
          <div class="set-row-desc">FastAPI 本地服务 + Vue 前端；视频生成走 ComfyUI 通道，本机进程内推理作为兜底。</div>
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">日志与排错</div>
          <div class="set-row-desc">后端日志在程序目录的 logs/ 下；桌面端「系统管理」能直接看终端输出。</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref } from 'vue'
import Segmented from '../Segmented.vue'

const props = defineProps({
  launch: { type: Object, default: () => ({}) },
})

const lang = ref('zh')
const langs = [
  { value: 'zh', label: '简体中文' },
  { value: 'en', label: 'English', disabled: true, hint: '还没做多语言，选了也不会切换' },
]

const hasDesk = computed(() => typeof window !== 'undefined' && !!window.desk && !!window.desk.checkUpdate)
const updateState = ref('idle')
const updateNote = ref('')

const version = computed(() => props.launch.version || '')
const paths = computed(() => props.launch.paths || {})

const updateDesc = computed(() => {
  if (!hasDesk.value) return '浏览器打开的页面没有更新通道，请重新下载安装包覆盖安装。'
  if (updateState.value === 'checking') return '正在向 GitHub Releases 查询…'
  if (updateNote.value) return updateNote.value
  return '从 GitHub Releases 拉取最新版本；下载完会在下次退出时安装。'
})

const gb = (o) => (o && o.size_gb ? `${Number(o.size_gb).toFixed(1)} GB` : '—')
const diskText = computed(() => {
  const v = Number((props.launch.disk || {}).free_gb)
  return !v || v < 0 ? '未知' : `${v.toFixed(0)} GB`
})
const diskBadge = computed(() => {
  const v = Number((props.launch.disk || {}).free_gb)
  return !v || v < 0 ? '' : v < 10 ? 'warn' : 'on'
})

// 桌面端更新事件：订阅一次，卸载时清掉（preload 的 on 返回清理函数）
const offs = []
function subscribe(channel, fn) {
  try {
    const off = window.desk.on(channel, fn)
    if (typeof off === 'function') offs.push(off)
  } catch {
    /* 通道不支持就算了，不因为订阅失败把页面搞坏 */
  }
}

async function checkUpdate() {
  if (!hasDesk.value) return
  updateState.value = 'checking'
  updateNote.value = ''
  offs.forEach((off) => off())
  offs.length = 0
  subscribe('update:available', (info) => {
    updateState.value = 'available'
    updateNote.value = `发现新版本 ${(info && info.version) || ''}，正在后台下载。`
  })
  subscribe('update:none', () => {
    updateState.value = 'none'
    updateNote.value = '已经是最新版本。'
  })
  subscribe('update:error', (e) => {
    updateState.value = 'error'
    updateNote.value = `检查失败：${(e && (e.message || e)) || '网络或签名问题'}`
  })
  try {
    await window.desk.checkUpdate()
  } catch (e) {
    updateState.value = 'error'
    updateNote.value = `检查失败：${e.message || e}`
  }
}

function open(p) {
  if (!p) return
  if (typeof window !== 'undefined' && window.desk && window.desk.openPath) {
    window.desk.openPath(p).catch(() => {})
  } else {
    navigator.clipboard && navigator.clipboard.writeText(p).catch(() => {})
  }
}

onUnmounted(() => {
  offs.forEach((off) => off())
  offs.length = 0
})
</script>

<style scoped>
code {
  background: var(--bg); border: 1px solid var(--border); border-radius: 5px;
  padding: 1px 5px; font-size: 12px; font-family: ui-monospace, monospace;
}
</style>
