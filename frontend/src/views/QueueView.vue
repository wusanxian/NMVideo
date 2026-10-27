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
        <div
          v-if="job.resource_note && job.resource_note !== job.error"
          class="job-resource"
          :title="job.resource_note"
        >
          <span class="res-tag">{{ noteTag(job.resource_note) }}</span>
          <span class="res-text">{{ job.resource_note }}</span>
        </div>
        <div v-if="job.error" class="job-error">⚠ {{ job.error }}</div>
        <video v-if="job.status === 'done'" class="job-video" :src="job.video_url" :poster="job.poster_url" controls preload="metadata"></video>
      </div>
      <div class="job-actions">
        <button v-if="job.status === 'pending' || job.status === 'running'" class="btn" @click="store.cancel(job.id)">取消</button>
        <button v-if="job.status === 'needs_attention'" class="btn" @click="store.retry(job.id)">重试</button>
        <button v-if="job.status === 'needs_attention'" class="btn" @click="store.cancel(job.id)">取消</button>
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
  needs_attention: '待核查',
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

// resource_note 现在是「一句话资源说明」，来源有五种（入队降档 / 入队提示 /
// 运行中内存紧张 / 看门狗中止 / 装不下被拒），标签得跟着变——写死「已自动
// 降档」会把中止也说成降档。关键字就是后端 resource_guard / resource_watch
// 里那几个固定措辞，改后端文案时记得同步这里。
function noteTag(note) {
  const text = note || ''
  if (text.includes('降档')) return '已自动降档'
  if (text.includes('中止')) return '资源中止'
  if (text.includes('接近') || text.includes('紧张')) return '内存紧张'
  return '资源提示'
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
