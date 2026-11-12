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
      <!-- 左：产物缩略（没有产物就给状态占位，保持行高一致） -->
      <div class="job-media" :class="{ flat: !(job.status === 'done' && job.video_url) }">
        <video v-if="job.status === 'done' && job.video_url"
               :src="job.video_url + '#t=0.1'" :poster="job.poster_url" controls preload="metadata"></video>
        <div v-else class="job-media-empty">
          <Icon :name="job.status === 'running' || job.status === 'pending' ? 'clock' : 'film'" :size="18" />
          <span>{{ statusText[job.status] || job.status }}</span>
        </div>
      </div>

      <div class="job-main">
        <div class="job-title">
          <span class="badge" :class="job.status">{{ statusText[job.status] || job.status }}</span>
          <span class="job-engine">{{ engineName(job.engine) }}</span>
          <span class="job-id">#{{ job.id }}</span>
          <span class="job-time">{{ formatTime(job.created_at) }}</span>
          <div class="job-actions">
            <button v-if="job.status === 'pending' || job.status === 'running'" class="btn btn-ghost btn-sm"
                    @click="store.cancel(job.id)">取消</button>
            <button v-if="job.status === 'needs_attention'" class="btn btn-ghost btn-sm"
                    @click="store.retry(job.id)">重试</button>
            <button v-if="job.status === 'needs_attention'" class="btn btn-ghost btn-sm"
                    @click="store.cancel(job.id)">取消</button>
            <button class="icon-btn danger" title="删除任务" @click="store.remove(job.id)">
              <Icon name="trash" :size="15" />
            </button>
          </div>
        </div>
        <div class="job-prompt">{{ job.prompt }}</div>
        <div class="job-params">
          {{ job.width }}×{{ job.height }} · {{ job.num_frames }} 帧 · {{ job.fps }}fps ·
          {{ job.steps }} 步<template v-if="job.h3_quality">（{{ H3_TIERS[job.h3_quality] || job.h3_quality }}档{{
            job.two_pass ? ' · 两遍' : ' · 单遍' }}）</template>
          · CFG {{ job.guidance }} · seed {{ job.seed }}
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
        <div v-if="job.error" class="job-error">{{ job.error }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.job-card { align-items: flex-start; gap: 14px; }

.job-media {
  width: 224px; flex: none;
  aspect-ratio: 16 / 9;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: linear-gradient(160deg, #0a1220, #060a12);
  border: 1px solid var(--border);
}
.job-media video { width: 100%; height: 100%; object-fit: cover; display: block; }
.job-media.flat { display: grid; place-items: center; border-style: dashed; background: transparent; }
.job-media-empty {
  display: flex; flex-direction: column; align-items: center; gap: 5px;
  color: #4c5f7a; font-size: var(--fs-xs);
}

.job-title .job-engine {
  font-size: var(--fs-sm); color: var(--text-dim);
  padding: 1px 7px; border-radius: 5px; background: var(--bg-card);
}
.job-title .job-time { margin-left: auto; }
.job-actions { display: flex; align-items: center; gap: 2px; }

@media (max-width: 900px) {
  .job-media { width: 150px; }
}
</style>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import Icon from '../components/Icon.vue'
import { api } from '../api'
import { useJobsStore } from '../store/jobs'

const store = useJobsStore()
const engines = ref([])

// H3 的画质档名：只看 steps 会分不清「8 步两遍」和「8 步单遍」
const H3_TIERS = { light: '轻量', medium: '标准', high: '精细', max: '极致' }

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
