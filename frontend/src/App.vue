<template>
  <aside class="sidebar">
    <div class="logo">🎬 AI 视频工坊</div>
    <nav>
      <router-link to="/generate">✨ 生成视频</router-link>
      <router-link to="/queue">
        📋 任务队列
        <span v-if="activeCount" class="nav-badge">{{ activeCount }}</span>
      </router-link>
      <router-link to="/gallery">🖼 作品画廊</router-link>
    </nav>
    <div class="sidebar-footer">
      <div class="engine-status" v-for="e in engines" :key="e.id">
        <span class="dot" :class="e.status"></span>
        <span>{{ e.name }}</span>
      </div>
      <div class="engine-status" v-if="!engines.length" :class="{ warn: !backendOnline }">
        <span class="dot" :style="{ background: backendOnline ? 'var(--text-dim)' : 'var(--red)' }"></span>
        <span>{{ backendOnline ? '后端启动中…' : '后端未连接' }}</span>
      </div>
    </div>
  </aside>
  <main class="main">
    <!-- 后端离线引导：exe 首次启动/窗口被关时的友好提示（替代白屏或报错） -->
    <transition name="fade">
      <div v-if="showOfflineCard" class="offline-overlay">
        <div class="offline-card">
          <div class="offline-icon">⏳</div>
          <h2>正在等待后端服务…</h2>
          <p v-if="waitingSec < 20">
            如果你是第一次运行：exe 窗口正在准备环境（安装依赖 / 下载模型），<br>
            大模型下载可能需要几分钟到几十分钟，请耐心等待窗口中的进度。
          </p>
          <p v-else>
            等待时间较长。请检查名为「AI视频工坊」的黑色命令行窗口是否还在运行：<br>
            · 窗口还在 → 它可能正在下载模型，继续等待即可<br>
            · 窗口已关闭 → 重新双击运行 <b>AI视频工坊.exe</b><br>
            · 想看详细进度 → 看那个窗口里滚动的输出
          </p>
          <div class="offline-meta">
            已等待 {{ waitingSec }} 秒 · 地址 http://127.0.0.1:8000
          </div>
          <button class="offline-retry" @click="checkNow">立即重试</button>
        </div>
      </div>
    </transition>
    <router-view />
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from './api'
import { useJobsStore } from './store/jobs'

const store = useJobsStore()
const engines = ref([])
const activeCount = computed(() => store.active.length)
const backendOnline = ref(true)
const waitingSec = ref(0)
let healthTimer = null
let waitTimer = null

const showOfflineCard = computed(() => !backendOnline.value)

async function checkNow() {
  try {
    await api.health()
    backendOnline.value = true
    waitingSec.value = 0
    engines.value = await api.engines().catch(() => engines.value)
  } catch {
    backendOnline.value = false
  }
}

onMounted(async () => {
  store.fetch()
  try {
    engines.value = await api.engines()
    backendOnline.value = true
  } catch {
    backendOnline.value = false
  }
  healthTimer = setInterval(checkNow, 5000)
  waitTimer = setInterval(() => {
    if (!backendOnline.value) waitingSec.value += 1
  }, 1000)
})

onUnmounted(() => {
  clearInterval(healthTimer)
  clearInterval(waitTimer)
})
</script>

<style scoped>
.engine-status.warn {
  color: var(--red);
}

/* 离线引导卡 */
.offline-overlay {
  position: fixed;
  inset: 0;
  background: rgba(10, 12, 18, 0.82);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.offline-card {
  max-width: 520px;
  padding: 36px 40px;
  background: var(--bg-card, #1a1f2b);
  border: 1px solid var(--border, #2a3345);
  border-radius: 14px;
  text-align: center;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}

.offline-icon {
  font-size: 42px;
  margin-bottom: 10px;
  animation: pulse 1.6s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.6; transform: scale(0.92); }
}

.offline-card h2 {
  margin: 0 0 14px;
  font-size: 20px;
  color: var(--text, #e8ecf3);
}

.offline-card p {
  margin: 0 0 12px;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--text-dim, #9aa5b8);
}

.offline-card b {
  color: var(--text, #e8ecf3);
}

.offline-meta {
  margin-top: 16px;
  font-size: 12px;
  color: var(--text-dim, #6b7688);
  font-variant-numeric: tabular-nums;
}

.offline-retry {
  margin-top: 14px;
  padding: 8px 22px;
  border: 1px solid var(--border, #2a3345);
  border-radius: 8px;
  background: transparent;
  color: var(--text, #e8ecf3);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}

.offline-retry:hover {
  border-color: var(--accent, #6c8cff);
  background: rgba(108, 140, 255, 0.08);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.35s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
