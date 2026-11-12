<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>作品画廊</h1>
        <div class="page-sub">共 {{ store.done.length }} 个已完成视频</div>
      </div>
      <button class="btn" @click="store.fetch()">刷新</button>
    </header>
    <p v-if="error" role="alert" class="form-msg">{{ error }}</p>

    <div v-if="!store.done.length" class="empty">
      还没有作品，去 <router-link to="/generate">生成页</router-link> 创作第一个视频
    </div>

    <div class="gallery-grid">
      <div v-for="job in store.done" :key="job.id" class="gallery-card">
        <div class="g-media">
          <video :src="job.video_url + '#t=0.1'" :poster="job.poster_url" controls loop preload="metadata"></video>
        </div>
        <div class="gallery-info">
          <div class="g-prompt" :title="job.prompt">{{ job.prompt }}</div>
          <div class="g-params">
            {{ job.preview_kind === 'native' ? '目标 ' : '' }}{{ job.width }}×{{ job.height }} · {{ job.num_frames }} 帧 · {{ job.fps }}fps · {{ job.steps }} 步 · seed {{ job.seed }}
          </div>
          <div v-if="job.preset || job.preview || job.elapsed_s || job.upgrade_clamped || job.parent_job_id" class="g-tags">
            <span v-if="job.preset" class="g-tag">{{ presetName(job.preset) }}</span>
            <span v-if="job.preview" class="g-tag g-tag-preview">预览</span>
            <span v-if="job.elapsed_s" class="g-tag">耗时 {{ formatElapsed(job.elapsed_s) }}</span>
            <span v-if="job.upgrade_clamped" class="g-tag g-tag-clamp" :title="job.upgrade_clamped">引擎护栏钳制</span>
            <span v-if="job.parent_job_id" class="g-tag">来源预览 {{ job.parent_job_id }}</span>
          </div>
          <div class="gallery-actions">
            <button class="btn btn-ghost btn-sm" @click="reuse(job)">复用参数</button>
            <a class="btn btn-ghost btn-sm" :href="job.video_url" download>下载</a>
            <template v-if="job.preview">
              <select v-model="upgradePreset" class="sel" :title="upgradeHint">
                <option value="">沿用原目标</option>
                <option v-for="p in qualityPresets" :key="p.id" :value="p.id">
                  {{ p.name }}（{{ p.width }}×{{ p.height }}）
                </option>
              </select>
              <button class="btn btn-primary btn-sm" :disabled="upgrading === job.id" @click="upgrade(job)">
                {{ upgrading === job.id ? '提交中…' : (job.preview_kind === 'native' ? '继续二采' : '生成高清候选') }}
              </button>
            </template>
            <button class="btn btn-ghost btn-sm btn-danger g-del" title="删除" @click="store.remove(job.id)">
              <Icon name="trash" :size="14" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '../components/Icon.vue'
import { api } from '../api'
import { useJobsStore } from '../store/jobs'

const router = useRouter()
const store = useJobsStore()
const qualityPresets = ref([])
const upgrading = ref('')
const error = ref('')
const upgradePreset = ref('')

// AnimeDiff 引擎有 32 帧 / 640×640 护栏：升级时自动钳到引擎上限
const upgradeHint = computed(() =>
  '选择目标档位；AnimeDiff 引擎上限 32 帧 / 640×640，超出会自动钳制')

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
    // H3 的画质档是独立维度，不带上就会静默掉回默认的「轻量」
    h3_quality: job.h3_quality || '',
    preview: false,
  }
  localStorage.setItem('aivideo:reuse', JSON.stringify(params))
  router.push('/generate')
}

// 由后端沿用来源任务的镜头、角色、素材及工作流，生成新的高清候选。
async function upgrade(job) {
  if (upgrading.value) return
  upgrading.value = job.id
  error.value = ''
  try {
    await api.upgradeJob(job.id, { preset: upgradePreset.value })
    await store.fetch()
    router.push('/queue')
  } catch (e) {
    error.value = e.message
  } finally {
    upgrading.value = ''
  }
}

onMounted(async () => {
  try {
    qualityPresets.value = await api.presets()
  } catch {
    qualityPresets.value = []
  }
})
</script>

<style scoped>
/* 卡片结构统一：媒体固定 16:9 → 信息区 → 操作区。
   以前 video 直接当卡片首元素、没有固定高度，加载前后行高参差不齐。 */
.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--sp-4);
}
.gallery-card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: border-color .16s var(--ease), box-shadow .16s var(--ease);
}
.gallery-card:hover { border-color: var(--border-strong); box-shadow: var(--shadow-2); }

.g-media {
  aspect-ratio: 16 / 9;
  overflow: hidden;
  /* 未加载出首帧时不是死黑：给一层极暗的底色，跟深色主题同族 */
  background: linear-gradient(160deg, #0a1220, #060a12);
}
.g-media video { width: 100%; height: 100%; object-fit: cover; display: block; }

.gallery-info {
  padding: 12px 14px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
}
.g-prompt {
  font-size: var(--fs-sm);
  color: var(--text-dim);
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 39px;
}
.g-params {
  font-size: var(--fs-xs);
  color: #5d6f8c;
  font-variant-numeric: tabular-nums;
  line-height: 1.5;
}
.g-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.g-tag {
  font-size: var(--fs-xs);
  color: var(--text-dim);
  background: var(--bg-card);
  border-radius: 5px;
  padding: 1px 7px;
}
.g-tag-preview { color: var(--accent-soft); background: rgba(var(--accent-rgb), .16); }

.gallery-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  flex-wrap: wrap;
}
.g-del { margin-left: auto; }

.sel {
  background: var(--bg-card);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 4px 8px;
  font-size: var(--fs-sm);
  font-family: inherit;
}
.g-tag-clamp {
  color: var(--yellow);
  background: rgba(251, 191, 36, .12);
  cursor: help;
}
</style>
