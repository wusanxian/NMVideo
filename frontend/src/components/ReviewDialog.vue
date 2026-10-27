<template>
  <div class="modal-mask" @click.self="$emit('close')">
    <div class="modal">
      <header class="modal-head">
        <h2>
          镜头审核 · {{ shot.title || '未命名镜头' }}
          <span v-if="stageBadge" class="stage-badge" :class="shot.review_stage">
            {{ stageBadge }}
          </span>
        </h2>
        <button class="icon-btn" @click="$emit('close')">✕</button>
      </header>

      <div class="modal-body">
        <!-- 审核门切换（3A 三态分离） -->
        <div class="stage-tabs">
          <button :class="{ active: stage === 'design' }" @click="switchStage('design')">
            ✏️ 设计审核
          </button>
          <button :class="{ active: stage === 'video' }" @click="switchStage('video')">
            🎬 视频审核
          </button>
        </div>
        <p class="stage-tip">{{ stageTip }}</p>

        <!-- 当前版本预览（视频审核） -->
        <template v-if="stage === 'video'">
          <div v-if="currentVersion && currentVersion.video_url" class="review-preview">
            <video :src="currentVersion.video_url" controls autoplay muted></video>
            <div class="rp-meta">
              <span class="badge done">v{{ currentVersion.version_no }}</span>
              <span>{{ currentVersion.engine }} · seed {{ currentVersion.seed }}</span>
            </div>
          </div>
          <p v-else class="empty-note">该镜头还没有生成版本，无法审核通过。</p>
        </template>

        <!-- 质量检查 -->
        <section class="form-section">
          <h3>
            {{ stage === 'design' ? '设计检查' : '质量检查' }}
            <span class="q-badge" :class="score.level">{{ Q_LEVELS[score.level] || score.level }}</span>
          </h3>
          <div v-if="issues.length" class="issue-list">
            <div v-for="(iss, i) in issues" :key="i" class="issue-item" :class="iss.level">
              <span class="iss-icon">{{ iss.level === 'error' ? '✕' : iss.level === 'warn' ? '!' : 'i' }}</span>
              <span>{{ iss.message }}</span>
            </div>
          </div>
          <p v-else class="empty-note">全部检查通过，没有发现问题。</p>
        </section>

        <!-- 审核意见 -->
        <section class="form-section">
          <h3>审核意见（记入历史）</h3>
          <textarea v-model="comment" rows="2"
                    placeholder="如：构图和配音 OK / 第二镜情绪不对，需要重做"></textarea>
        </section>
      </div>

      <footer class="modal-foot">
        <span v-if="errMsg" class="form-msg">{{ errMsg }}</span>
        <div style="margin-left: auto; display: flex; gap: 10px; flex-wrap: wrap">
          <button class="btn" @click="$emit('close')">取消</button>
          <button class="btn warn" :class="{ primary: preset === 'rework' }"
                  :disabled="busy" @click="act('rework')">
            ↺ {{ stage === 'design' ? '打回修改' : '打回重做' }}
          </button>
          <button class="btn ok" :class="{ primary: preset === 'approve' }"
                  :disabled="busy || (stage === 'video' && !currentVersion) || score.level === 'error'"
                  :title="score.level === 'error' ? '存在 error 级问题，不建议通过' : ''"
                  @click="act('approve')">
            ✔ {{ stage === 'design' ? '设计通过' : '审核通过' }}
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const props = defineProps({
  shot: { type: Object, required: true },
  preset: { type: String, default: '' },  // 'approve' | 'rework' | ''（卡片快捷入口带入）
  initialStage: { type: String, default: 'video' },  // 3A：卡片可按 review_stage 带入门
})
const emit = defineEmits(['close', 'done'])

const Q_LEVELS = { ok: '通过', warn: '注意', error: '严重' }
const STAGE_LABELS = { design: '设计审核', generation: '生成审核', video: '视频审核', '': '无待审' }
const issues = ref([])
const score = ref({ level: 'ok', error: 0, warn: 0, info: 0 })
const comment = ref('')
const busy = ref(false)
const errMsg = ref('')
const stage = ref(props.initialStage === 'design' ? 'design' : 'video')
const currentVersion = computed(() => props.shot.current_version || null)

const stageBadge = computed(() => STAGE_LABELS[props.shot.review_stage] || '')
const stageTip = computed(() => stage.value === 'design'
  ? '设计审核在生成前进行：只检查提示词 / 对白 / 时长 / 角色等创作参数，不要求已有视频。通过后分镜仍保持草稿态，可以继续发起生成。'
  : '视频审核对候选成片做质检：通过后该版本正式采用；被时间线引用的镜头不能静默换版本（会提示先移除引用）。')

async function loadQuality() {
  try {
    const q = await api.shotQuality(props.shot.id, stage.value)
    issues.value = q.issues
    score.value = q.score
  } catch { /* 忽略 */ }
}

function switchStage(next) {
  if (stage.value === next) return
  stage.value = next
  errMsg.value = ''
  loadQuality()
}

onMounted(loadQuality)

async function act(action) {
  busy.value = true
  errMsg.value = ''
  try {
    // expected_version_id：提交时复核采用版本，过期提交（409）由后端拦截
    const expected = stage.value === 'video' ? (props.shot.current_version_id || '') : ''
    await api.reviewShot(props.shot.id, action, '', comment.value, stage.value, expected)
    emit('done', action)
  } catch (e) {
    errMsg.value = e.message
    // 被后端质检阻断（422）时直接用响应里的检查结果，保证弹窗显示与后端一致
    const blocked = e.data?.detail
    if (e.status === 422 && blocked?.issues) {
      issues.value = blocked.issues
      if (blocked.score) score.value = blocked.score
    } else {
      try {
        const q = await api.shotQuality(props.shot.id, stage.value)
        issues.value = q.issues
        score.value = q.score
      } catch { /* ignore */ }
    }
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.modal-mask {
  position: fixed; inset: 0; background: rgba(8, 10, 15, 0.75);
  display: flex; align-items: center; justify-content: center;
  z-index: 200; padding: 24px; backdrop-filter: blur(3px);
}
.modal {
  width: min(640px, 100%); max-height: 92vh; display: flex; flex-direction: column;
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: 14px;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.55);
}
.modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 22px; border-bottom: 1px solid var(--border);
}
.modal-head h2 { margin: 0; font-size: 17px; display: flex; align-items: center; gap: 8px; }
.stage-badge {
  font-size: 11px; padding: 2px 9px; border-radius: 999px; font-weight: 600;
  background: var(--bg-card); border: 1px solid var(--border); color: var(--text-dim);
}
.stage-badge.design { background: rgba(96, 165, 250, 0.15); color: #60a5fa; border-color: transparent; }
.stage-badge.generation { background: rgba(251, 191, 36, 0.15); color: var(--yellow); border-color: transparent; }
.stage-badge.video { background: rgba(var(--accent-rgb), 0.15); color: var(--accent-soft); border-color: transparent; }
.modal-body { overflow-y: auto; padding: 20px 22px; }
.modal-foot {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 22px; border-top: 1px solid var(--border);
}
.stage-tabs { display: flex; gap: 8px; }
.stage-tabs button {
  background: var(--bg-card); border: 1px solid var(--border); color: var(--text-dim);
  border-radius: 8px; padding: 7px 14px; font-size: 12.5px; cursor: pointer; font-family: inherit;
}
.stage-tabs button.active {
  border-color: var(--accent); color: var(--text); background: var(--bg-hover);
}
.stage-tip {
  font-size: 12px; color: var(--text-dim); line-height: 1.6;
  margin: 10px 0 0; padding: 9px 11px; background: var(--bg-card);
  border-radius: 8px; border: 1px solid var(--border);
}
.review-preview video {
  width: 100%; max-height: 300px; border-radius: 10px; background: #000; display: block;
}
.rp-meta {
  display: flex; align-items: center; gap: 10px; margin-top: 8px;
  font-size: 12px; color: var(--text-dim);
}
.form-section { margin-top: 20px; }
.form-section h3 {
  font-size: 14px; margin: 0 0 12px; color: var(--text);
  padding-left: 10px; border-left: 3px solid var(--accent);
  display: flex; align-items: center; gap: 8px;
}
.q-badge { font-size: 11px; padding: 2px 9px; border-radius: 999px; font-weight: 600; }
.q-badge.ok { background: rgba(52, 211, 153, 0.15); color: var(--green); }
.q-badge.warn { background: rgba(251, 191, 36, 0.15); color: var(--yellow); }
.q-badge.error { background: rgba(248, 113, 113, 0.15); color: var(--red); }
.issue-list { display: flex; flex-direction: column; gap: 6px; }
.issue-item {
  display: flex; gap: 8px; align-items: flex-start; font-size: 12.5px;
  padding: 8px 10px; border-radius: 8px; line-height: 1.5;
}
.issue-item.error { background: rgba(248, 113, 113, 0.08); color: var(--red); }
.issue-item.warn { background: rgba(251, 191, 36, 0.08); color: var(--yellow); }
.issue-item.info { background: var(--bg-card); color: var(--text-dim); }
.iss-icon {
  width: 16px; height: 16px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; font-weight: 700; border: 1px solid currentColor;
}
textarea {
  width: 100%; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 8px; color: var(--text); padding: 9px 11px; font-size: 13px;
  font-family: inherit; resize: vertical;
}
textarea:focus { border-color: var(--accent); outline: none; }
.empty-note { font-size: 12.5px; color: var(--text-dim); }
.badge {
  font-size: 11px; padding: 2px 9px; border-radius: 999px;
  background: var(--bg-card); border: 1px solid var(--border); color: var(--text-dim);
}
.badge.done { background: rgba(52, 211, 153, 0.15); color: var(--green); border-color: transparent; }
.icon-btn {
  background: transparent; border: none; color: var(--text-dim);
  cursor: pointer; padding: 4px 7px; border-radius: 6px; font-size: 13px;
}
.icon-btn:hover { background: var(--bg-hover); color: var(--text); }
.btn {
  background: var(--bg-card); border: 1px solid var(--border); color: var(--text);
  border-radius: 8px; padding: 8px 16px; font-size: 13px; cursor: pointer; font-family: inherit;
}
.btn:hover:not(:disabled) { border-color: var(--accent); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.ok { border-color: var(--green); color: var(--green); }
.btn.ok:hover:not(:disabled) { background: rgba(52, 211, 153, 0.1); }
.btn.ok.primary { background: rgba(52, 211, 153, 0.18); }
.btn.warn { border-color: var(--yellow); color: var(--yellow); }
.btn.warn:hover:not(:disabled) { background: rgba(251, 191, 36, 0.1); }
.btn.warn.primary { background: rgba(251, 191, 36, 0.18); }
.form-msg { color: var(--red); font-size: 12.5px; }
</style>
