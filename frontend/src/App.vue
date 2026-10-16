<template>
  <aside class="sidebar">
    <div class="logo">🎬 AI 动画导演工作台</div>
    <nav>
      <a v-for="item in navItems" :key="item.label" class="nav-item"
         :class="{ active: isActive(item) }"
         :href="itemHref(item)" @click.prevent="onNav(item)">
        <span class="nav-ico">{{ item.icon }}</span>
        <span class="nav-text">{{ item.label }}</span>
        <span v-if="item.label === '视频生成' && activeCount" class="nav-badge">{{ activeCount }}</span>
      </a>
    </nav>
    <div class="nav-extra">
      <router-link to="/queue">
        📋 任务队列
        <span v-if="activeCount" class="nav-badge">{{ activeCount }}</span>
      </router-link>
      <router-link to="/gallery">🖼 作品画廊</router-link>
    </div>
    <transition name="fade">
      <div v-if="navToast" class="nav-toast">{{ navToast }}</div>
    </transition>
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
            等待时间较长。桌面端请打开标题栏「系统管理」查看启动状态与终端日志，或重试连接。<br>
            浏览器用户请检查本地后端是否仍在运行。
          </p>
          <div class="offline-meta">
            已等待 {{ waitingSec }} 秒 · 地址 {{ backendOrigin }}
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
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import { useJobsStore } from './store/jobs'
import { useProjectStore } from './store/project'

const store = useJobsStore()
const projectStore = useProjectStore()
const route = useRoute()
const router = useRouter()
const engines = ref([])
const activeCount = computed(() => store.active.length)
const backendOnline = ref(true)
const waitingSec = ref(0)
const backendOrigin = window.location.origin
const navToast = ref('')
let toastTimer = null
let healthTimer = null
let waitTimer = null

const showOfflineCard = computed(() => !backendOnline.value)

// ---------- 侧栏导航（按创作流：作品 → 剧本 → 资产 → 生成 → 剪辑 → 声音 → 设置） ----------
const pid = computed(() => projectStore.currentProjectId)
const navItems = computed(() => [
  { icon: '🗂', label: '我的作品', to: '/projects', route: '/projects' },
  { icon: '📖', label: '剧本与分镜', tab: 'shots', needsProject: true },
  { icon: '🎭', label: '角色与场景', tab: 'assets', needsProject: true },
  { icon: '🎬', label: '视频生成', to: '/generate', route: '/generate' },
  { icon: '✂️', label: '剪辑工作台', tab: 'timeline', needsProject: true },
  { icon: '🎧', label: '声音制作', tab: 'timeline', needsProject: true },
  { icon: '⚙️', label: '模型与设置', to: '/services', route: '/services' },
])

function itemHref(item) {
  if (item.needsProject && !pid.value) return '/projects'
  if (item.needsProject) return `/projects/${pid.value}?tab=${item.tab}`
  return item.to
}

function isActive(item) {
  if (item.needsProject) {
    return route.path.startsWith('/projects/') && route.query.tab === item.tab
  }
  return route.path === item.route
}

function toast(msg) {
  navToast.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (navToast.value = ''), 2500)
}

function onNav(item) {
  const href = itemHref(item)
  if (item.needsProject && !pid.value) {
    toast('请先进入一个作品，再使用「' + item.label + '」')
  }
  router.push(href)
}

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
  clearTimeout(toastTimer)
})
</script>

<style scoped>
.engine-status.warn {
  color: var(--red);
}

/* 导航提示 toast（无当前作品时点击项目内菜单） */
.nav-toast {
  position: fixed;
  top: 18px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 200;
  padding: 9px 18px;
  font-size: 13px;
  color: var(--text, #e8ecf3);
  background: var(--bg-card, #1a1f2b);
  border: 1px solid var(--accent, #8b5cf6);
  border-radius: 10px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
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
