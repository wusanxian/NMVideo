<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>作品画廊</h1>
        <div class="page-sub">共 {{ store.done.length }} 个已完成视频</div>
      </div>
      <button class="btn" @click="store.fetch()">刷新</button>
    </header>

    <div v-if="!store.done.length" class="empty">
      还没有作品，去 <router-link to="/generate">生成页</router-link> 创作第一个视频
    </div>

    <div class="gallery-grid">
      <div v-for="job in store.done" :key="job.id" class="gallery-card">
        <video :src="job.video_url" :poster="job.poster_url" controls loop preload="metadata"></video>
        <div class="gallery-info">
          <div class="g-prompt">{{ job.prompt }}</div>
          <div class="g-params">
            {{ job.width }}×{{ job.height }} · {{ job.num_frames }} 帧 · {{ job.fps }}fps · {{ job.steps }} 步 · seed {{ job.seed }}
            <span v-if="job.preset" class="g-tag">{{ presetName(job.preset) }}</span>
            <span v-if="job.preview" class="g-tag g-tag-preview">预览</span>
            <span v-if="job.elapsed_s" class="g-tag">耗时 {{ formatElapsed(job.elapsed_s) }}</span>
          </div>
          <div class="gallery-actions">
            <button class="btn" @click="reuse(job)">复用参数</button>
            <button v-if="job.preview" class="btn btn-primary" @click="upgrade(job)">⬆ 升级渲染</button>
            <a class="btn" :href="job.video_url" download>下载</a>
            <button class="btn btn-danger" @click="store.remove(job.id)">删除</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useJobsStore } from '../store/jobs'

const router = useRouter()
const store = useJobsStore()
const qualityPresets = ref([])

function presetName(id) {
  const p = qualityPresets.value.find((q) => q.id === id)
  return p ? p.name : id
}

function formatElapsed(s) {
  if (s < 60) return `${Math.round(s)} 秒`
  return `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒`
}

function reuse(job) {
  const params = {
    engine: job.engine,
    prompt: job.prompt,
    negative_prompt: job.negative_prompt,
    width: job.width,
    height: job.height,
    num_frames: job.num_frames,
    fps: job.fps,
    steps: job.steps,
    guidance: job.guidance,
    seed: job.seed,
    preset: job.preset || '',
    preview: false,
  }
  localStorage.setItem('aivideo:reuse', JSON.stringify(params))
  router.push('/generate')
}

// 升级渲染：预览 → 全质量（保留 seed，关预览，用原档位）
function upgrade(job) {
  const params = {
    engine: job.engine,
    prompt: job.prompt,
    negative_prompt: job.negative_prompt,
    preset: job.preset || '',
    seed: job.seed,
    preview: false,
  }
  localStorage.setItem('aivideo:reuse', JSON.stringify(params))
  router.push('/generate')
}

onMounted(async () => {
  try {
    qualityPresets.value = await api.presets()
  } catch {
    qualityPresets.value = []
  }
})
</script>
