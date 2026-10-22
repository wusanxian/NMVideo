<template>
  <div class="home">
    <!-- 无当前项目：导演台引导 -->
    <div v-if="!project" class="empty-hero">
      <div class="empty-icon">🎬</div>
      <h1>NMVideo</h1>
      <p>从剧本到成片：剧本拆分、角色资产、分镜生成、多轨剪辑，一屏完成。</p>
      <div class="empty-actions">
        <button class="btn-primary" @click="$router.push('/projects')">🗂 打开我的作品</button>
        <button class="btn" @click="$router.push('/generate')">✨ 快速生成视频</button>
      </div>
    </div>

    <template v-else>
      <!-- 标题行：当前作品 / 当前幕 -->
      <header class="page-head">
        <div class="breadcrumb">
          <span class="bc-project">{{ project.name }}</span>
          <span class="bc-sep">/</span>
          <span class="bc-scene">{{ currentScene ? currentScene.title : '第一幕' }}</span>
        </div>
        <div class="head-actions">
          <span class="shot-counter">镜头 {{ pad(currentIndex + 1) }} / {{ pad(totalShots) }}</span>
          <button class="btn-mini" @click="goto('shots')">✎ 管理分镜</button>
        </div>
      </header>

      <!-- 主预览 -->
      <section class="preview-card">
        <div class="preview-stage">
          <video v-if="currentVideo" :key="currentVideo" :src="currentVideo" :poster="currentPoster"
                 controls preload="metadata" class="preview-video"></video>
          <div v-else class="preview-placeholder">
            <div class="play-btn">▶</div>
            <div class="ph-text">主预览画面</div>
            <div class="ph-sub">{{ currentShot ? '该镜头还没有生成视频' : '从下方时间线选一个镜头' }}</div>
          </div>
        </div>
        <footer class="preview-meta">
          <span class="meta-shot">镜头 {{ pad(currentIndex + 1) }} / {{ pad(totalShots) }}</span>
          <span v-if="currentShot" class="status-chip" :class="currentShot.status">
            <i class="dot"></i>{{ statusLabel(currentShot.status) }}
          </span>
        </footer>
      </section>

      <!-- 分镜时间线 -->
      <section class="tl-block">
        <div class="tl-head">
          <h2>分镜时间线</h2>
          <button class="btn-mini" @click="goto('timeline')">⇄ 剪辑台</button>
        </div>
        <div class="tl-strip">
          <button v-for="(shot, i) in allShots" :key="shot.id" class="tl-card"
                  :class="{ active: shot.id === currentShot?.id }" @click="currentIndex = i">
            <span class="tl-thumb">
              <img v-if="posterOf(shot)" :src="posterOf(shot)" alt="" loading="lazy" />
              <span v-else class="tl-thumb-ph">🖼</span>
              <span class="tl-dot" :class="shot.status" :title="statusLabel(shot.status)"></span>
            </span>
            <span class="tl-label">镜头 {{ pad(i + 1) }}</span>
          </button>
          <div v-if="!allShots.length" class="tl-empty">还没有分镜，去「剧本与分镜」拆分一场戏吧</div>
        </div>
      </section>

      <!-- 多轨条：配音 / 音乐 / 音效 / 字幕 -->
      <section class="tracks-bar">
        <button class="track-item" @click="goto('timeline')">
          <span class="t-ico">♪</span>
          <span class="t-name">配音</span>
          <span class="t-count" v-if="trackCounts.voice">{{ trackCounts.voice }}</span>
        </button>
        <button class="track-item" @click="goto('timeline')">
          <span class="t-ico">♫</span>
          <span class="t-name">音乐</span>
          <span class="t-count" v-if="trackCounts.music">{{ trackCounts.music }}</span>
        </button>
        <button class="track-item" @click="goto('timeline')">
          <span class="t-ico">🔊</span>
          <span class="t-name">音效</span>
          <span class="t-count" v-if="trackCounts.sfx">{{ trackCounts.sfx }}</span>
        </button>
        <button class="track-item" @click="goto('timeline')">
          <span class="t-ico">🔤</span>
          <span class="t-name">字幕</span>
          <span class="t-count" v-if="trackCounts.subtitle">{{ trackCounts.subtitle }}</span>
        </button>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useProjectStore } from '../store/project'

const router = useRouter()
const store = useProjectStore()

const project = computed(() => store.current)
const allShots = computed(() => {
  const out = []
  for (const sc of store.current?.scenes || []) {
    for (const sh of sc.shots || []) out.push({ ...sh, _scene: sc })
  }
  return out
})
const totalShots = computed(() => allShots.value.length)

const currentIndex = ref(0)
const currentShot = computed(() => allShots.value[currentIndex.value] || null)
const currentScene = computed(() => currentShot.value?._scene || store.current?.scenes?.[0] || null)

const currentVersion = computed(() => currentShot.value?.current_version || null)
const currentVideo = computed(() => currentVersion.value?.video_url || '')
const currentPoster = computed(() => currentVersion.value?.poster_url || '')

const trackCounts = ref({ voice: 0, music: 0, sfx: 0, subtitle: 0 })

const STATUS_LABELS = {
  draft: '草稿', queued: '排队中', generating: '生成中',
  review: '待审核', approved: '已通过', rework: '需重做',
}
function statusLabel(s) { return STATUS_LABELS[s] || s || '未知' }
function pad(n) { return String(n).padStart(2, '0') }
function posterOf(shot) { return shot?.current_version?.poster_url || '' }

function goto(tab) {
  if (store.current) router.push(`/projects/${store.current.id}?tab=${tab}`)
  else router.push('/projects')
}

async function loadTrackCounts() {
  if (!store.current) return
  const pid = store.current.id
  try {
    const [music, sfx, subs] = await Promise.all([
      api.audioTracks(pid, 'music').catch(() => []),
      api.audioTracks(pid, 'sfx').catch(() => []),
      api.subtitles(pid).catch(() => []),
    ])
    let voice = 0
    for (const sc of store.current.scenes || []) {
      for (const sh of sc.shots || []) voice += (sh.voices || []).length
    }
    trackCounts.value = { voice, music: music.length, sfx: sfx.length, subtitle: subs.length }
  } catch { /* 静默：统计失败不影响主页 */ }
}

let pollTimer = null

onMounted(async () => {
  await store.fetchProjects()
  const list = store.projects
  if (store.currentProjectId && list.some((p) => p.id === store.currentProjectId)) {
    await store.openProject(store.currentProjectId)
  } else if (list.length) {
    await store.openProject(list[0].id)
  }
  await loadTrackCounts()
  // 轮询：生成中镜头状态、配音/轨道统计实时更新
  pollTimer = setInterval(async () => {
    if (!store.current) return
    await store.refresh()
    await loadTrackCounts()
  }, 3000)
})

onUnmounted(() => clearInterval(pollTimer))
</script>

<style scoped>
.home { max-width: 1080px; margin: 0 auto; }

/* ---------- 空状态引导 ---------- */
.empty-hero {
  text-align: center;
  padding: 90px 24px;
  border: 1px dashed var(--border);
  border-radius: 16px;
  background: var(--bg-panel);
}
.empty-icon { font-size: 52px; margin-bottom: 14px; }
.empty-hero h1 { margin: 0 0 10px; font-size: 24px; }
.empty-hero p { margin: 0 0 24px; font-size: 14px; color: var(--text-dim); }
.empty-actions { display: flex; gap: 12px; justify-content: center; }

/* ---------- 标题行 ---------- */
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 18px;
}
.breadcrumb { font-size: 21px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.bc-project { color: var(--text); }
.bc-sep { color: var(--text-dim); font-weight: 400; }
.bc-scene { color: var(--text-dim); font-weight: 600; }
.head-actions { display: flex; align-items: center; gap: 10px; }
.shot-counter { font-size: 12.5px; color: var(--text-dim); font-variant-numeric: tabular-nums; }

.btn-mini {
  padding: 5px 12px;
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: transparent;
  color: var(--text-dim);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.btn-mini:hover { border-color: var(--accent); color: var(--text); }

/* ---------- 主预览 ---------- */
.preview-card {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--bg-panel);
  overflow: hidden;
  margin-bottom: 26px;
}
.preview-stage {
  position: relative;
  aspect-ratio: 16 / 9;
  background: var(--bg-card);
  display: flex;
  align-items: center;
  justify-content: center;
}
.preview-video { width: 100%; height: 100%; object-fit: contain; background: #000; }
.preview-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  color: var(--text-dim);
}
.play-btn {
  width: 62px;
  height: 62px;
  border-radius: 50%;
  background: rgba(139, 92, 246, 0.16);
  border: 1px solid rgba(139, 92, 246, 0.45);
  color: var(--accent);
  font-size: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding-left: 5px;
}
.ph-text { font-size: 15px; font-weight: 600; color: var(--text-dim); }
.ph-sub { font-size: 12px; }
.preview-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 11px 16px;
  border-top: 1px solid var(--border);
}
.meta-shot { font-size: 13px; color: var(--text-dim); font-variant-numeric: tabular-nums; }

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 3px 11px;
  border-radius: 999px;
  border: 1px solid var(--border);
  color: var(--text-dim);
}
.status-chip .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.status-chip.approved { color: var(--green, #34d399); border-color: rgba(52, 211, 153, 0.4); }
.status-chip.review { color: var(--yellow, #fbbf24); border-color: rgba(251, 191, 36, 0.4); }
.status-chip.rework { color: var(--red, #f87171); border-color: rgba(248, 113, 113, 0.4); }
.status-chip.generating, .status-chip.queued { color: var(--accent); border-color: rgba(139, 92, 246, 0.45); }

/* ---------- 分镜时间线 ---------- */
.tl-block { margin-bottom: 26px; }
.tl-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.tl-head h2 { margin: 0; font-size: 16.5px; }
.tl-strip {
  display: flex;
  gap: 12px;
  overflow-x: auto;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--bg-panel);
  scrollbar-width: thin;
}
.tl-card {
  flex: 0 0 auto;
  width: 132px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-card);
  padding: 0 0 8px;
  cursor: pointer;
  transition: border-color 0.15s, transform 0.15s;
  text-align: center;
}
.tl-card:hover { border-color: var(--accent); transform: translateY(-2px); }
.tl-card.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
.tl-thumb {
  position: relative;
  display: block;
  aspect-ratio: 16 / 10;
  border-radius: 10px 10px 0 0;
  overflow: hidden;
  background: var(--bg-hover);
}
.tl-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.tl-thumb-ph {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  color: var(--text-dim);
}
.tl-dot {
  position: absolute;
  top: 7px;
  right: 7px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-dim);
  box-shadow: 0 0 0 2px rgba(0, 0, 0, 0.35);
}
.tl-dot.approved { background: var(--green, #34d399); }
.tl-dot.review { background: var(--yellow, #fbbf24); }
.tl-dot.rework { background: var(--red, #f87171); }
.tl-dot.generating, .tl-dot.queued { background: var(--accent); }
.tl-label { display: block; margin-top: 7px; font-size: 12px; color: var(--text-dim); }
.tl-card.active .tl-label { color: var(--text); }
.tl-empty {
  padding: 26px;
  font-size: 13px;
  color: var(--text-dim);
  text-align: center;
  width: 100%;
}

/* ---------- 多轨条 ---------- */
.tracks-bar {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--bg-panel);
}
.track-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-card);
  color: var(--text-dim);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.track-item:hover { border-color: var(--accent); color: var(--text); }
.t-ico { font-size: 15px; color: var(--accent); }
.t-count {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--bg-hover);
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
</style>
