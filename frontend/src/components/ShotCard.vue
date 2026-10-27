<template>
  <div class="shot-card" :class="'st-' + shot.status" :draggable="draggable"
       @dragstart="onDragStart" @dragover.prevent @drop="onDrop" @dragend="$emit('drag-end')">
    <div class="shot-head">
      <span class="shot-order">{{ index + 1 }}</span>
      <span class="shot-title" :title="shot.title">{{ shot.title || '未命名镜头' }}</span>
      <span class="badge" :class="badgeClass">{{ statusLabel }}</span>
      <span v-if="shot.review_stage" class="stage-chip" :class="shot.review_stage"
            :title="stageTip">{{ STAGE_LABELS[shot.review_stage] || shot.review_stage }}</span>
      <div class="shot-head-actions">
        <button class="icon-btn" title="上移" :disabled="index === 0" @click.stop="$emit('move-up')">↑</button>
        <button class="icon-btn" title="下移" :disabled="isLast" @click.stop="$emit('move-down')">↓</button>
        <button class="icon-btn danger" title="删除分镜" @click.stop="$emit('delete')">✕</button>
      </div>
    </div>

    <!-- 关联角色 -->
    <div v-if="shot.characters && shot.characters.length" class="shot-chars">
      <span v-for="c in shot.characters" :key="c.id" class="char-chip" :title="c.appearance">
        <i class="char-dot" :style="{ background: charColor(c.name) }"></i>{{ c.name }}
      </span>
    </div>

    <div v-if="shot.dialogue" class="shot-dialogue">💬 {{ shot.dialogue }}</div>

    <div class="shot-meta">
      <span class="meta-chip">{{ shot.shot_size }}</span>
      <span class="meta-chip">{{ shot.camera_movement }}</span>
      <span class="meta-chip">{{ shot.duration_s }}s</span>
      <span v-if="shot.mode && shot.mode !== 't2v'" class="meta-chip mode">{{ MODE_LABELS[shot.mode] || shot.mode }}</span>
      <span v-if="shot.voices && shot.voices.length" class="meta-chip voice">🎙 {{ shot.voices.length }}</span>
      <span v-if="shot.versions && shot.versions.length" class="meta-chip">
        {{ shot.versions.length }} 个版本
      </span>
      <span v-if="shot.note" class="meta-note" :title="shot.note">⚠ {{ shot.note }}</span>
    </div>

    <!-- 生成进度（排队中/生成中） -->
    <div v-if="job && (job.status === 'pending' || job.status === 'running')" class="shot-progress">
      <div class="bar"><div class="bar-inner" :style="{ width: Math.round(job.progress * 100) + '%' }"></div></div>
      <div class="progress-text">{{ job.stage || '排队中' }} {{ Math.round(job.progress * 100) }}%</div>
    </div>

    <!-- 当前版本预览 -->
    <div v-if="currentVersion && currentVersion.video_url" class="shot-preview" @click="playPreview">
      <img v-if="currentVersion.poster_url" :src="currentVersion.poster_url" alt="封面" />
      <video v-else :src="currentVersion.video_url" muted preload="metadata"></video>
      <div class="play-mask">▶</div>
      <span class="ver-tag">v{{ currentVersion.version_no }}</span>
    </div>
    <div v-else-if="shot.prompt" class="shot-prompt" :title="shot.prompt">{{ shot.prompt }}</div>

    <div class="shot-actions">
      <button class="btn" @click="$emit('edit')">✏️ 编辑</button>
      <button class="btn btn-primary" :disabled="!canGenerate" @click="$emit('generate')">
        🎬 {{ shot.status === 'approved' || shot.status === 'rework' ? '重新制作' : '生成' }}
      </button>
      <button v-if="['draft', 'rework'].includes(shot.status)" class="btn"
              title="生成前的设计审核：检查提示词 / 对白 / 时长 / 角色等创作参数"
              @click="$emit('review-design')">📐 设计审核</button>
      <template v-if="shot.status === 'review'">
        <button class="btn ok" @click="$emit('review', 'approve')">✔ 通过</button>
        <button class="btn warn" @click="$emit('review', 'rework')">↺ 重做</button>
      </template>
      <button v-if="shot.status === 'approved'" class="btn" title="从此镜头返工（可含后续镜头）"
              @click="$emit('rework')">↺ 返工</button>
      <button v-if="shot.versions && shot.versions.length" class="btn" @click="$emit('versions')">
        🗂 版本 {{ shot.versions.length }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  shot: { type: Object, required: true },
  index: { type: Number, default: 0 },
  isLast: { type: Boolean, default: false },
  draggable: { type: Boolean, default: false },
  job: { type: Object, default: null },   // 关联的生成任务（用于进度条）
})

const emit = defineEmits(['edit', 'generate', 'review', 'review-design', 'rework', 'delete', 'versions', 'move-up', 'move-down', 'drag-start', 'drag-end', 'drop'])

const MODE_LABELS = { i2v: '图生视频', first_last: '首尾帧', continuation: '视频续接' }

const STATUS_LABELS = {
  draft: '草稿', queued: '排队中', generating: '生成中',
  review: '待审核', approved: '已通过', rework: '需重做',
}
// 3A 审核三态（与后端 REVIEW_STAGE_LABELS 对应）
const STAGE_LABELS = {
  design: '设计审核', generation: '生成审核', video: '视频审核', design_ok: '设计已通过', '': '',
}
const stageTip = computed(() => ({
  design: '设计审核中：生成前的创作参数检查门',
  design_ok: '设计审核已通过，可以发起生成',
  generation: '生成审核中：任务已排队/正在生成',
  video: '视频审核中：成片质检与采用版本确认',
})[props.shot.review_stage] || '')
const statusLabel = computed(() => STATUS_LABELS[props.shot.status] || props.shot.status)
const badgeClass = computed(() => {
  const m = {
    draft: 'cancelled', queued: 'pending', generating: 'running',
    review: 'running', approved: 'done', rework: 'failed',
  }
  return m[props.shot.status] || 'cancelled'
})
const canGenerate = computed(() =>
  !['queued', 'generating'].includes(props.shot.status) && props.shot.prompt && props.shot.prompt.trim()
)
const currentVersion = computed(() => props.shot.current_version || null)

function charColor(name) {
  let h = 0
  for (const ch of name || '') h = (h * 31 + ch.codePointAt(0)) % 360
  return `hsl(${h}, 65%, 60%)`
}

function onDragStart(e) {
  if (!props.draggable) return
  e.dataTransfer.setData('text/plain', props.shot.id)
  emit('drag-start')
}
function onDrop(e) {
  emit('drop', e.dataTransfer.getData('text/plain'))
}
function playPreview() {
  if (currentVersion.value) window.open(currentVersion.value.video_url, '_blank')
}
</script>

<style scoped>
.shot-card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: border-color 0.15s;
}
.shot-card:hover { border-color: #3a4358; }
.shot-card.st-generating { border-color: var(--accent); }
.shot-card.st-approved { border-left: 3px solid var(--green); }
.shot-card.st-rework { border-left: 3px solid var(--red); }
.shot-card.st-review { border-left: 3px solid var(--yellow); }

.shot-head { display: flex; align-items: center; gap: 8px; }
.shot-order {
  width: 22px; height: 22px; border-radius: 6px; flex-shrink: 0;
  background: var(--bg-card); border: 1px solid var(--border);
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; color: var(--text-dim); font-variant-numeric: tabular-nums;
}
.shot-title {
  font-weight: 600; font-size: 14px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; max-width: 220px;
}
.shot-head-actions { margin-left: auto; display: flex; gap: 4px; }

/* 3A 审核三态徽标 */
.stage-chip {
  font-size: 11px; padding: 2px 9px; border-radius: 999px; font-weight: 600;
  flex-shrink: 0; background: var(--bg-card); border: 1px solid var(--border);
  color: var(--text-dim); cursor: default;
}
.stage-chip.design { background: rgba(96, 165, 250, 0.15); color: #60a5fa; border-color: transparent; }
.stage-chip.design_ok { background: rgba(52, 211, 153, 0.15); color: var(--green); border-color: transparent; }
.stage-chip.generation { background: rgba(251, 191, 36, 0.15); color: var(--yellow); border-color: transparent; }
.stage-chip.video { background: rgba(var(--accent-rgb), 0.15); color: var(--accent-soft); border-color: transparent; }

.icon-btn {
  background: transparent; border: none; color: var(--text-dim);
  cursor: pointer; padding: 4px 7px; border-radius: 6px; font-size: 13px;
}
.icon-btn:hover:not(:disabled) { background: var(--bg-hover); color: var(--text); }
.icon-btn:disabled { opacity: 0.35; cursor: not-allowed; }
.icon-btn.danger:hover { color: var(--red); }

.shot-chars { display: flex; flex-wrap: wrap; gap: 5px; }
.char-chip {
  display: flex; align-items: center; gap: 4px; font-size: 11px;
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 999px; padding: 1px 9px; color: var(--text-dim);
}
.char-dot { width: 6px; height: 6px; border-radius: 50%; }

.shot-dialogue {
  font-size: 13px; color: var(--text-dim); font-style: italic;
  border-left: 2px solid var(--accent); padding-left: 10px; line-height: 1.6;
}

.shot-meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.meta-chip {
  font-size: 11.5px; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 999px; padding: 2px 10px; color: var(--text-dim);
}
.meta-chip.mode { border-color: var(--accent); color: var(--accent); }
.meta-chip.voice { border-color: var(--green); color: var(--green); }
.meta-note { font-size: 11.5px; color: var(--yellow); }

.shot-progress { display: flex; align-items: center; gap: 10px; }

.shot-preview {
  position: relative; border-radius: 8px; overflow: hidden; cursor: pointer;
  background: #000; aspect-ratio: 16 / 9;
}
.shot-preview img, .shot-preview video { width: 100%; height: 100%; object-fit: cover; display: block; }
.play-mask {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  font-size: 30px; color: rgba(255, 255, 255, 0.85); background: rgba(0, 0, 0, 0.25);
  opacity: 0; transition: opacity 0.15s;
}
.shot-preview:hover .play-mask { opacity: 1; }
.ver-tag {
  position: absolute; top: 6px; left: 6px; font-size: 11px; font-weight: 700;
  background: rgba(0, 0, 0, 0.65); color: #fff; border-radius: 5px; padding: 1px 7px;
}

.shot-prompt {
  font-size: 12px; color: var(--text-dim); line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

.shot-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: auto; }
.shot-actions .btn { padding: 6px 12px; font-size: 12.5px; }
.btn.ok { border-color: var(--green); color: var(--green); }
.btn.ok:hover { background: rgba(52, 211, 153, 0.1); }
.btn.warn { border-color: var(--yellow); color: var(--yellow); }
.btn.warn:hover { background: rgba(251, 191, 36, 0.1); }
.badge {
  font-size: 11px; padding: 2px 9px; border-radius: 999px; flex-shrink: 0;
  background: var(--bg-card); border: 1px solid var(--border); color: var(--text-dim);
}
.badge.pending { color: var(--yellow); border-color: rgba(251, 191, 36, 0.4); }
.badge.running { color: var(--accent); border-color: rgba(var(--accent-2-rgb), 0.4); }
.badge.done { color: var(--green); border-color: rgba(52, 211, 153, 0.4); }
.badge.failed { color: var(--red); border-color: rgba(248, 113, 113, 0.4); }
.badge.cancelled { color: var(--text-dim); }
.bar { flex: 1; height: 6px; background: var(--bg-card); border-radius: 3px; overflow: hidden; }
.bar-inner { height: 100%; background: var(--accent); border-radius: 3px; transition: width 0.3s; }
.progress-text { font-size: 11.5px; color: var(--text-dim); white-space: nowrap; }
</style>
