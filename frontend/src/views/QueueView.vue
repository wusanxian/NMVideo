<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>任务队列</h1>
        <div class="page-sub">每 1.5 秒自动刷新 · 生成任务在后台顺序执行</div>
      </div>
      <button class="btn" @click="store.fetch()">刷新</button>
    </header>

    <div v-if="!store.jobs.length" class="empty">
      暂无任务，去 <router-link to="/generate">生成页</router-link> 创建第一个视频
    </div>

    <div v-for="job in store.jobs" :key="job.id" class="job-card">
      <div class="job-main">
        <div class="job-title">
          <span class="badge" :class="job.status">{{ statusText[job.status] || job.status }}</span>
          <span class="job-id">#{{ job.id }}</span>
          <span class="job-engine">{{ engineName(job.engine) }}</span>
          <span class="job-time">{{ formatTime(job.created_at) }}</span>
        </div>
        <div class="job-prompt">{{ job.prompt }}</div>
        <div class="job-params">
          {{ job.width }}×{{ job.height }} · {{ job.num_frames }} 帧 · {{ job.fps }}fps ·
          {{ job.steps }} 步 · CFG {{ job.guidance }} · seed {{ job.seed }}
        </div>
        <div v-if="job.status === 'pending' || job.status === 'running'" class="progress">
          <div class="bar"><div class="bar-inner" :style="{ width: Math.round(job.progress * 100) + '%' }"></div></div>
          <span class="progress-text">{{ job.stage }} {{ Math.round(job.progress * 100) }}%</span>
        </div>
        <div v-if="job.error" class="job-error">⚠ {{ job.error }}</div>
        <video v-if="job.status === 'done'" class="job-video" :src="job.video_url" :poster="job.poster_url" controls preload="metadata"></video>
      </div>
      <div class="job-actions">
        <button v-if="job.status === 'pending' || job.status === 'running'" class="btn" @click="store.cancel(job.id)">取消</button>
        <button class="btn btn-danger" @click="store.remove(job.id)">删除</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import { useJobsStore } from '../store/jobs'

const store = useJobsStore()
const engines = ref([])

const statusText = {
  pending: '排队中',
  running: '生成中',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

function engineName(id) {
  const e = engines.value.find((x) => x.id === id)
  return e ? e.name : id
}

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

let timer = null

onMounted(async () => {
  await store.fetch()
  try {
    engines.value = await api.engines()
  } catch {
    /* ignore */
  }
  timer = setInterval(() => store.fetch(), 1500)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>
