<template>
  <div class="shot-card" :class="'st-' + shot.status" :draggable="draggable"
       @dragstart="onDragStart" @dragover.prevent @drop="onDrop" @dragend="$emit('drag-end')">
    <!-- 画面区：有版本就显示封面，没有就留一块安静的占位。
         以前没有封面时这里直接铺两行提示词，卡片高度参差不齐。 -->
    <div class="shot-media" @click="currentVersion && currentVersion.video_url ? playPreview() : $emit('edit')">
      <template v-if="currentVersion && currentVersion.video_url">
        <img v-if="currentVersion.poster_url" :src="currentVersion.poster_url" alt="封面" />
        <video v-else :src="currentVersion.video_url" muted preload="metadata"></video>
        <div class="play-mask"><Icon name="play" :size="20" /></div>
        <span class="ver-tag">v{{ currentVersion.version_no }}</span>
      </template>
      <div v-else class="media-placeholder">
        <Icon name="film" :size="20" />
        <span>{{ shot.prompt ? '未生成画面' : '待写提示词' }}</span>
      </div>

      <div class="shot-order">{{ index + 1 }}</div>

      <!-- 排序/删除收进悬停区，避免和标题抢位置 -->
      <div class="media-actions">
        <button class="icon-btn" title="上移" :disabled="index === 0" @click.stop="$emit('move-up')">
          <Icon name="up" :size="14" />
        </button>
        <button class="icon-btn" title="下移" :disabled="isLast" @click.stop="$emit('move-down')">
          <Icon name="down" :size="14" />
        </button>
        <button class="icon-btn danger" title="删除分镜" @click.stop="$emit('delete')">
          <Icon name="trash" :size="14" />
        </button>
      </div>
    </div>

    <div class="shot-body">
      <div class="shot-head">
        <span class="shot-title" :title="shot.title">{{ shot.title || '未命名镜头' }}</span>
        <span class="status-dot" :class="badgeClass" :title="statusLabel"></span>
        <span class="status-text">{{ statusLabel }}</span>
      </div>

      <div v-if="shot.review_stage" class="stage-row">
        <span class="stage-chip" :class="shot.review_stage" :title="stageTip">
          {{ STAGE_LABELS[shot.review_stage] || shot.review_stage }}
        </span>
      </div>

      <div v-if="shot.characters && shot.characters.length" class="shot-chars">
        <span v-for="c in shot.characters" :key="c.id" class="char-chip" :title="c.appearance">
          <i class="char-dot" :style="{ background: charColor(c.name) }"></i>{{ c.name }}
        </span>
      </div>

      <p v-if="shot.prompt" class="shot-prompt" :title="shot.prompt">{{ shot.prompt }}</p>
      <p v-if="shot.dialogue" class="shot-dialogue">“{{ shot.dialogue }}”</p>

      <div class="shot-meta">
        <span class="meta-chip">{{ shot.shot_size }}</span>
        <span class="meta-chip">{{ shot.camera_movement }}</span>
        <span class="meta-chip">{{ shot.duration_s }}s</span>
        <span v-if="shot.mode && shot.mode !== 't2v'" class="meta-chip mode">
          {{ MODE_LABELS[shot.mode] || shot.mode }}
        </span>
        <span v-if="shot.voices && shot.voices.length" class="meta-chip voice">
          <Icon name="mic" :size="12" />{{ shot.voices.length }}
        </span>
        <span v-if="shot.versions && shot.versions.length" class="meta-chip">
          <Icon name="layers" :size="12" />{{ shot.versions.length }}
        </span>
        <span v-if="shot.note" class="meta-note" :title="shot.note">{{ shot.note }}</span>
      </div>

      <!-- 生成进度（排队中/生成中） -->
      <div v-if="job && (job.status === 'pending' || job.status === 'running')" class="shot-progress">
        <div class="bar"><div class="bar-inner" :style="{ width: Math.round(job.progress * 100) + '%' }"></div></div>
        <div class="progress-text">{{ job.stage || '排队中' }} {{ Math.round(job.progress * 100) }}%</div>
      </div>
    </div>

    <div class="shot-actions">
      <button class="btn btn-ghost btn-sm" @click="$emit('edit')">
        <Icon name="pencil" :size="14" />编辑
      </button>
      <button v-if="['draft', 'rework'].includes(shot.status)" class="btn btn-ghost btn-sm"
              title="生成前的设计审核：检查提示词 / 对白 / 时长 / 角色等创作参数"
              @click="$emit('review-design')">
        <Icon name="check" :size="14" />设计审核
      </button>
      <template v-if="shot.status === 'review'">
        <button class="btn btn-sm ok" @click="$emit('review', 'approve')">通过</button>
        <button class="btn btn-sm warn" @click="$emit('review', 'rework')">重做</button>
      </template>
      <button v-if="shot.status === 'approved'" class="btn btn-ghost btn-sm" title="从此镜头返工（可含后续镜头）"
              @click="$emit('rework')">返工</button>
      <button v-if="shot.versions && shot.versions.length" class="btn btn-ghost btn-sm" @click="$emit('versions')">
        版本 {{ shot.versions.length }}
      </button>
      <button class="btn btn-primary btn-sm shot-generate" :disabled="!canGenerate" @click="$emit('generate')">
        {{ shot.status === 'approved' || shot.status === 'rework' ? '重新生成' : '生成' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  shot: { type: Object, required: true },
  index: { type: Number, default: 0 },
  isLast: { type: Boolean, default: false },
  draggable: { type: Boolean, default: false },
  job: { type: Object, default: null },   // 关联的生成任务（用于进度条）
})

const emit = defineEmits(['edit', 'generate', 'review', 'review-design', 'rework', 'delete', 'versions', 'move-up', 'move-down', 'drag-start', 'drag-end', 'drop'])

const MODE_LABELS = {
  i2v: '图生视频', first_last: '首尾帧', continuation: '视频续接',
  whitemodel: '白模生成', v2v: '白模替换',
}

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
  border-radius: var(--radius-md);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: border-color .16s var(--ease), transform .16s var(--ease), box-shadow .16s var(--ease);
}
.shot-card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-2);
  transform: translateY(-1px);
}
.shot-card.st-generating { border-color: rgba(var(--accent-rgb), .55); }
.shot-card.st-approved { border-color: rgba(52, 211, 153, .35); }
.shot-card.st-rework { border-color: rgba(248, 113, 113, .35); }

/* ---- 画面区 ---- */
.shot-media {
  position: relative;
  aspect-ratio: 16 / 9;
  background: linear-gradient(160deg, #0b1524, #0e1a2c);
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  overflow: hidden;
}
.shot-media img, .shot-media video { width: 100%; height: 100%; object-fit: cover; display: block; }

.media-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: #4c5f7a;
  font-size: var(--fs-xs);
  letter-spacing: .02em;
}
.media-placeholder::before {
  /* 极淡的斜纹，暗示「这里以后是画面」，不是空白 */
  content: '';
  position: absolute;
  inset: 0;
  background-image: repeating-linear-gradient(135deg, rgba(255, 255, 255, .022) 0 1px, transparent 1px 10px);
}

.play-mask {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: #fff; background: rgba(4, 8, 14, .34);
  opacity: 0; transition: opacity .16s var(--ease);
}
.shot-media:hover .play-mask { opacity: 1; }

.ver-tag {
  position: absolute; left: 8px; bottom: 8px;
  font-size: var(--fs-xs); font-weight: 600;
  background: rgba(4, 8, 14, .72); color: #fff;
  border-radius: 5px; padding: 1px 7px;
  backdrop-filter: blur(2px);
}

.shot-order {
  position: absolute; top: 8px; left: 8px;
  min-width: 20px; height: 20px; padding: 0 5px;
  display: grid; place-items: center;
  font-size: var(--fs-xs); font-weight: 600;
  color: #cfe0f7; background: rgba(4, 8, 14, .6);
  border-radius: 6px;
  backdrop-filter: blur(2px);
  font-variant-numeric: tabular-nums;
}

.media-actions {
  position: absolute; top: 6px; right: 6px;
  display: flex; gap: 2px;
  opacity: 0; transform: translateY(-2px);
  transition: opacity .16s var(--ease), transform .16s var(--ease);
}
.shot-media:hover .media-actions { opacity: 1; transform: none; }
.media-actions .icon-btn {
  background: rgba(4, 8, 14, .62);
  color: #c6d4e8;
  backdrop-filter: blur(2px);
}
.media-actions .icon-btn:hover:not(:disabled) { background: rgba(4, 8, 14, .85); color: #fff; }
.media-actions .icon-btn.danger:hover { color: var(--red); }

/* ---- 内容区 ---- */
.shot-body {
  padding: 12px 14px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.shot-head { display: flex; align-items: center; gap: 7px; min-width: 0; }
.shot-title {
  font-size: var(--fs-base);
  font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}
.status-dot { width: 6px; height: 6px; border-radius: 50%; flex: none; background: var(--text-dim); }
.status-dot.pending { background: var(--yellow); }
.status-dot.running { background: var(--accent-2); }
.status-dot.done { background: var(--green); }
.status-dot.failed { background: var(--red); }
.status-text { font-size: var(--fs-xs); color: var(--text-dim); flex: none; }

.stage-row { display: flex; gap: 6px; }
.stage-chip {
  font-size: var(--fs-xs); padding: 1px 8px; border-radius: 999px; font-weight: 500;
  background: var(--bg-card); border: 1px solid var(--border); color: var(--text-dim);
}
.stage-chip.design { background: rgba(var(--accent-rgb), .16); color: var(--accent-soft); border-color: transparent; }
.stage-chip.design_ok { background: rgba(52, 211, 153, .14); color: var(--green); border-color: transparent; }
.stage-chip.generation { background: rgba(251, 191, 36, .14); color: var(--yellow); border-color: transparent; }
.stage-chip.video { background: rgba(var(--accent-2-rgb), .16); color: var(--accent-soft); border-color: transparent; }

.shot-chars { display: flex; flex-wrap: wrap; gap: 5px; }
.char-chip {
  display: flex; align-items: center; gap: 5px; font-size: var(--fs-xs);
  background: var(--bg-card); border-radius: 999px; padding: 1px 8px; color: var(--text-dim);
}
.char-dot { width: 5px; height: 5px; border-radius: 50%; }

.shot-prompt {
  margin: 0;
  font-size: var(--fs-sm);
  color: var(--text-dim);
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.shot-dialogue {
  margin: 0;
  font-size: var(--fs-sm);
  color: #a9bcd6;
  line-height: 1.5;
  padding-left: 9px;
  border-left: 2px solid rgba(var(--accent-rgb), .55);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.shot-meta { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; margin-top: auto; }
.meta-chip {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: var(--fs-xs);
  background: var(--bg-card);
  border-radius: 5px;
  padding: 1px 7px;
  color: var(--text-dim);
}
.meta-chip.mode { color: var(--accent-soft); background: rgba(var(--accent-rgb), .14); }
.meta-chip.voice { color: var(--green); background: rgba(52, 211, 153, .12); }
.meta-note { font-size: var(--fs-xs); color: var(--yellow); }

.shot-progress { display: flex; align-items: center; gap: 10px; }
.bar { flex: 1; height: 4px; background: var(--bg-card); border-radius: 2px; overflow: hidden; }
.bar-inner { height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-2)); border-radius: 2px; transition: width .3s; }
.progress-text { font-size: var(--fs-xs); color: var(--text-dim); white-space: nowrap; }

/* ---- 操作区 ---- */
.shot-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 8px 10px;
  border-top: 1px solid var(--border);
  background: rgba(255, 255, 255, .012);
}
.shot-actions .btn { border-color: transparent; }
.shot-actions .btn-ghost:hover { border-color: transparent; }
.shot-generate {
  margin-left: auto;
  min-width: 76px;
}
.btn.ok { border-color: rgba(52, 211, 153, .5); color: var(--green); }
.btn.ok:hover { background: rgba(52, 211, 153, .1); }
.btn.warn { border-color: rgba(251, 191, 36, .5); color: var(--yellow); }
.btn.warn:hover { background: rgba(251, 191, 36, .1); }
</style>
