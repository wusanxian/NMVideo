<template>
  <div class="sp-panel">
    <!-- 工具条：选分集 + 生成分镜 -->
    <div class="sp-bar">
      <label class="pick-ep">
        分集
        <select v-model="selectedId" @change="onSelectEpisode">
          <option value="">选择分集…</option>
          <option v-for="ep in episodes" :key="ep.id" :value="ep.id">
            {{ ep.seq }}. {{ ep.title }}（{{ ep.script_status === 'approved' ? '剧本已审核' : '剧本未审核' }}）
          </option>
        </select>
      </label>
      <button class="btn btn-primary" :disabled="!selectedId || generating" @click="generatePlan">
        {{ generating ? '生成中…' : '✨ 生成结构化分镜' }}
      </button>
      <span class="dim tip">以「审核通过的动漫剧本 + 故事圣经」为输入；重新生成会替换既有分镜计划（旧计划镜头随级联删除）</span>
    </div>
    <div v-if="error" class="form-msg">{{ error }}</div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="!episodes.length" class="empty">
      还没有分集。请先在「🎬 分集与剧本」页签生成分集与剧本（并审核通过）。
    </div>
    <div v-else-if="!selectedId" class="empty dim">选择上方分集开始。</div>
    <div v-else-if="!plan" class="empty dim">
      该分集还没有结构化分镜。点「生成结构化分镜」——LLM 按剧本逐镜头展开
      （景别 / 机位运动 / 画面动作 / 对白 / 出场人物 / 走位 / 情绪 / 时长 / 生成模式）。
    </div>

    <template v-else>
      <!-- 计划概要 + 操作 -->
      <header class="plan-head">
        <div class="plan-meta">
          <span class="badge" :class="plan.status">{{ plan.status === 'approved' ? '已审核' : '草稿' }}</span>
          <span>共 {{ plan.shot_count }} 镜 · 总时长 {{ plan.total_duration_sec }} 秒 · v{{ plan.version }}</span>
          <span v-if="plan.project" class="handoff-ok">→ 已交接项目「{{ plan.project.name }}」</span>
        </div>
        <div class="ops">
          <button class="btn" :disabled="plan.status === 'approved'" @click="approvePlan">
            ✔ 审核分镜（v{{ plan.version + 1 }}）
          </button>
          <button class="btn" @click="showVersions = true">版本历史</button>
          <button class="btn" :disabled="validating" @click="runValidate">
            {{ validating ? '校验中…' : '🔍 一致性校验' }}
          </button>
          <button class="btn btn-primary" :disabled="plan.status !== 'approved'" @click="handoff">
            🎬 交接为项目分镜
          </button>
        </div>
      </header>
      <div v-if="plan.status === 'approved'" class="dim plan-note">
        已审核（v{{ plan.version }}）——改动任意镜头后自动回草稿态，版本历史保留；交接后项目分镜按当前审核态重建。
      </div>

      <!-- 一致性校验（PR-N06：分镜 ↔ 圣经双向，只提示不阻断） -->
      <section v-if="validation" class="validate-sec" :class="{ ok: validation.ok }">
        <div class="val-head">
          <b>🔍 一致性校验</b>
          <span class="badge" :class="validation.ok ? 'ok' : 'warn'">
            {{ validation.ok ? '通过' : `${validation.warnings.length} 项提示` }}
          </span>
          <span class="dim">
            圣经 v{{ validation.bible_version }}（{{ validation.bible_status === 'approved' ? '已审核' : '草稿' }}）
            · 分镜冻结于 v{{ validation.plan_bible_version }} · 已核对 {{ validation.checked_items }} 镜
          </span>
          <button class="btn btn-sm" @click="validation = null">收起</button>
        </div>
        <span v-if="validation.stale" class="badge stale">圣经已更新</span>
        <ul class="val-warnings">
          <li v-for="(w, i) in validation.warnings" :key="i">{{ w }}</li>
        </ul>
        <div v-if="validation.item_issues.length" class="val-items">
          <div v-for="iss in validation.item_issues" :key="iss.item_id" class="val-item">
            <span class="dim">#{{ iss.seq }}</span>
            <span>{{ iss.title || '镜头' }}</span>
            <span v-if="iss.unknown_characters.length" class="st-chip failed">
              未知人物：{{ iss.unknown_characters.join('、') }}
            </span>
            <span v-for="a in iss.alias_resolved" :key="a.name" class="st-chip queued" :title="'将按圣经标准名锚定'">
              {{ a.name }} → {{ a.canonical }}
            </span>
            <span v-if="iss.unknown_scenes.length" class="st-chip partial">
              未知场景：{{ iss.unknown_scenes.join('、') }}
            </span>
          </div>
        </div>
      </section>

      <!-- 提交批次（PR-N05：预览转正与安全提交） -->
      <section v-if="plan.project" class="submit-sec">
        <div class="submit-bar">
          <b>🎬 生成批次</b>
          <select v-model="submitForm.kind" class="sel">
            <option value="preview">预览（低质量快速）</option>
            <option value="final">正式（需逐镜已采用版本）</option>
          </select>
          <select v-if="submitForm.kind === 'final'" v-model="submitForm.quality" class="sel">
            <option value="draft">草稿档</option>
            <option value="standard">标准档</option>
            <option value="high">精细档</option>
          </select>
          <select v-model="submitForm.engine" class="sel" title="AnimeDiff 仅支持文生视频；FreeVideo H3 需在 ComfyUI 安装 FreeVideo 插件（8GB 显存起步）">
            <option value="anime">AnimeDiff（本机）</option>
            <option value="comfyui">ComfyUI 工作流</option>
            <option value="h3">FreeVideo H3（MiniMax，需插件）</option>
          </select>
          <button class="btn btn-primary" :disabled="submitting" @click="submitBatch">
            {{ submitting ? '提交中…' : (submitForm.kind === 'preview' ? '▶ 提交预览批次' : '▶ 提交正式批次') }}
          </button>
          <span class="dim">按镜头串行生成（续接镜头等前序完成）；正式批次前请先跑预览并在项目里逐镜审核采用</span>
        </div>
        <div v-if="error" class="form-msg">{{ error }}</div>

        <div v-for="sub in submissions" :key="sub.id" class="sub-card">
          <div class="sub-head">
            <span class="badge" :class="sub.status">{{ subStatus(sub.status) }}</span>
            <span>{{ sub.kind === 'preview' ? '预览' : '正式' }} · {{ sub.engine }} · {{ sub.quality }}</span>
            <span class="dim">{{ sub.done_count }}/{{ sub.shot_count }} 完成</span>
            <span v-if="sub.failed_count" class="fail-tip">{{ sub.failed_count }} 失败</span>
            <span class="dim">预算估算 {{ sub.budget_estimate_sec }}s / 上限 {{ sub.budget_limit_sec }}s</span>
            <span class="dim">{{ fmtTime(sub.created_at) }}</span>
            <span class="ops">
              <button v-if="['partial', 'failed'].includes(sub.status)" class="btn btn-sm" @click="retryBatch(sub)">🔁 重试失败镜</button>
              <button v-if="['pending', 'running'].includes(sub.status)" class="btn btn-sm" @click="cancelBatch(sub)">✕ 取消</button>
            </span>
          </div>
          <div v-if="sub.warnings && sub.warnings.length" class="sub-warn">
            ⚠ {{ sub.warnings.join('；') }}
          </div>
          <div class="sub-items">
            <div v-for="it in sub.items" :key="it.id" class="sub-item" :title="it.error || it.stage || ''">
              <span class="dim">#{{ it.seq }}</span>
              <span class="it-title">{{ it.shot_title || it.title || '镜头' }}</span>
              <span class="st-chip" :class="it.status">{{ itStatus(it.status) }}</span>
              <span v-if="it.status === 'running' || it.status === 'queued'" class="dim">{{ Math.round((it.progress || 0) * 100) }}% {{ it.stage }}</span>
              <span v-if="it.error" class="it-err" :title="it.error">{{ it.error }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- 镜头表 -->
      <div class="shot-table">
        <div class="shot-row head">
          <span class="c-seq">#</span>
          <span class="c-title">镜头</span>
          <span class="c-size">景别</span>
          <span class="c-move">机位</span>
          <span class="c-dur">时长</span>
          <span class="c-mode">模式</span>
          <span class="c-ops">操作</span>
        </div>
        <template v-for="it in plan.items" :key="it.id">
          <div class="shot-row" :class="{ editing: editingId === it.id }">
            <span class="c-seq">{{ it.seq }}</span>
            <span class="c-title">
              <b>{{ it.title }}</b>
              <span class="dim scene-tag">{{ it.scene_title }}</span>
              <span v-if="it.dialogue" class="dlg">💬 {{ it.dialogue }}</span>
              <span class="action">{{ it.action }}</span>
              <span class="tags">
                <span v-for="n in it.character_names" :key="n" class="chip char">{{ n }}</span>
                <span v-if="it.position" class="chip pos" :title="it.position">📍 {{ it.position }}</span>
                <span v-if="it.emotion" class="chip emo">🎭 {{ it.emotion }}</span>
                <span v-if="it.chapter_refs && it.chapter_refs.length" class="chip ch"
                      :title="it.chapter_refs.map((r) => r.title).join('、')">
                  📖 {{ it.chapter_refs.length }} 章可定位
                </span>
              </span>
            </span>
            <span class="c-size">{{ it.shot_size }}</span>
            <span class="c-move">{{ it.camera_movement }}</span>
            <span class="c-dur">{{ it.duration_sec }}s</span>
            <span class="c-mode"><span class="mode-chip" :class="it.mode">{{ modeLabel(it.mode) }}</span></span>
            <span class="c-ops">
              <button class="btn btn-sm" title="上移" :disabled="it.seq <= 1" @click="reorder(it, 'up')">↑</button>
              <button class="btn btn-sm" title="下移" :disabled="it.seq >= plan.items.length" @click="reorder(it, 'down')">↓</button>
              <button class="btn btn-sm" @click="startEdit(it)">编辑</button>
            </span>
          </div>
          <!-- 行内编辑 -->
          <div v-if="editingId === it.id" class="shot-editor">
            <div class="egrid">
              <label>镜头名<input v-model="editForm.title" maxlength="40" /></label>
              <label>场景名<input v-model="editForm.scene_title" maxlength="120" /></label>
              <label>景别
                <select v-model="editForm.shot_size">
                  <option v-for="s in SHOT_SIZES" :key="s" :value="s">{{ s }}</option>
                </select>
              </label>
              <label>机位运动
                <select v-model="editForm.camera_movement">
                  <option v-for="m in CAMERA_MOVES" :key="m" :value="m">{{ m }}</option>
                </select>
              </label>
              <label>时长（秒）
                <input type="number" v-model.number="editForm.duration_sec" min="1" max="15" step="0.5" />
              </label>
              <label>生成模式
                <select v-model="editForm.mode">
                  <option v-for="m in MODES" :key="m" :value="m">{{ modeLabel(m) }}</option>
                </select>
              </label>
              <label class="wide">画面动作<input v-model="editForm.action" maxlength="300" /></label>
              <label class="wide">对白<input v-model="editForm.dialogue" maxlength="300" /></label>
              <label class="wide">出场人物（逗号分隔，故事圣经标准名）<input v-model="charsText" /></label>
              <label>走位 / 出入画<input v-model="editForm.position" maxlength="120" /></label>
              <label>情绪<input v-model="editForm.emotion" maxlength="40" /></label>
            </div>
            <div class="ops">
              <button class="btn btn-primary btn-sm" :disabled="saving" @click="saveEdit(it)">
                {{ saving ? '保存中…' : '保存镜头' }}
              </button>
              <button class="btn btn-sm" @click="editingId = ''">取消</button>
            </div>
          </div>
        </template>
      </div>
    </template>

    <!-- 版本历史弹窗 -->
    <div v-if="showVersions" class="modal-mask" @click.self="showVersions = false">
      <div class="modal">
        <header class="modal-head">
          <h2>分镜版本历史（不可变快照）</h2>
          <button class="icon-btn" @click="showVersions = false">✕</button>
        </header>
        <div class="modal-body">
          <div v-if="!plan || !plan.versions.length" class="empty dim">还没有审核过的版本</div>
          <template v-else>
            <div v-for="v in plan.versions" :key="v.version" class="ver-row">
              <button class="btn btn-sm" @click="loadVersion(v.version)">v{{ v.version }}</button>
              <span class="dim">{{ fmtTime(v.created_at) }}</span>
              <span v-if="v.note">{{ v.note }}</span>
            </div>
            <pre v-if="versionSnapshot" class="ver-snap">{{ versionSnapshot }}</pre>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const props = defineProps({
  novelId: { type: String, required: true },
})
const router = useRouter()

const SHOT_SIZES = ['远景', '全景', '中景', '近景', '特写']
const CAMERA_MOVES = ['固定', '推镜', '拉镜', '摇镜', '移镜', '跟镜', '环绕', '手持']
const MODES = ['t2v', 'i2v', 'first_last', 'continuation']
const MODE_LABELS = {
  t2v: '文生视频',
  i2v: '图生视频',
  first_last: '首尾帧',
  continuation: '续接',
}

const episodes = ref([])
const selectedId = ref('')
const plan = ref(null)
const loading = ref(true)
const generating = ref(false)
const saving = ref(false)
const error = ref('')
const showVersions = ref(false)
const versionSnapshot = ref('')
const editingId = ref('')
const charsText = ref('')
const editForm = ref({})

// PR-N05：提交批次
const submissions = ref([])
const submitting = ref(false)
const submitForm = ref({ kind: 'preview', quality: 'draft', engine: 'h3' })
let pollTimer = null

// PR-N06：一致性校验
const validation = ref(null)
const validating = ref(false)

onMounted(load)
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })

async function load() {
  loading.value = true
  error.value = ''
  try {
    episodes.value = await api.novelEpisodes(props.novelId)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function onSelectEpisode() {
  plan.value = null
  editingId.value = ''
  submissions.value = []
  validation.value = null
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (selectedId.value) await refreshPlan()
}

async function refreshPlan() {
  try {
    plan.value = await api.novelShotPlan(props.novelId, selectedId.value)
    validation.value = null // 计划重载（生成/修改/审核）后旧校验结果作废
    await refreshSubmissions()
  } catch (e) {
    if (e.status === 404 || (e.message || '').includes('尚未生成')) {
      plan.value = null
    } else {
      error.value = e.message
    }
  }
}

// ---------- PR-N05：提交批次 ----------
const SUB_ST = {
  pending: '等待', running: '生成中', cancelling: '取消中', done: '已完成',
  partial: '部分完成', failed: '失败', cancelled: '已取消',
}
const ITEM_ST = {
  pending: '待放行', queued: '排队', running: '生成中', done: '完成',
  failed: '失败', cancelled: '已取消',
}
function subStatus(s) { return SUB_ST[s] || s }
function itStatus(s) { return ITEM_ST[s] || s }

async function refreshSubmissions() {
  if (!props.novelId || !selectedId.value) return
  try {
    submissions.value = await api.novelShotSubmissions(props.novelId, selectedId.value)
    // 活跃批次存在 → 5s 轮询进度（GET 本身会顺带推进批次）
    const active = submissions.value.some((s) => ['pending', 'running', 'cancelling'].includes(s.status))
    if (active && !pollTimer) {
      pollTimer = setInterval(refreshSubmissions, 5000)
    } else if (!active && pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  } catch (e) {
    error.value = e.message
  }
}

async function submitBatch() {
  submitting.value = true
  error.value = ''
  try {
    await api.novelShotSubmit(props.novelId, selectedId.value, { ...submitForm.value })
    await refreshSubmissions()
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}

async function retryBatch(sub) {
  error.value = ''
  try {
    await api.novelShotSubmitRetry(props.novelId, selectedId.value, sub.id)
    await refreshSubmissions()
  } catch (e) {
    error.value = e.message
  }
}

async function cancelBatch(sub) {
  error.value = ''
  try {
    await api.novelShotSubmitCancel(props.novelId, selectedId.value, sub.id)
    await refreshSubmissions()
  } catch (e) {
    error.value = e.message
  }
}

async function generatePlan() {
  generating.value = true
  error.value = ''
  try {
    plan.value = await api.novelShotPlanGenerate(props.novelId, selectedId.value)
    await load() // 刷新分集列表的剧本状态徽章
  } catch (e) {
    error.value = e.message
  } finally {
    generating.value = false
  }
}

function modeLabel(m) {
  return MODE_LABELS[m] || m
}

function startEdit(it) {
  editingId.value = it.id
  charsText.value = (it.character_names || []).join('，')
  editForm.value = {
    title: it.title,
    scene_title: it.scene_title,
    shot_size: it.shot_size,
    camera_movement: it.camera_movement,
    duration_sec: it.duration_sec,
    mode: it.mode,
    action: it.action,
    dialogue: it.dialogue,
    position: it.position,
    emotion: it.emotion,
  }
}

async function saveEdit(it) {
  saving.value = true
  error.value = ''
  try {
    const body = { ...editForm.value }
    body.character_names = charsText.value
      .split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    await api.novelShotItemUpdate(props.novelId, selectedId.value, it.id, body)
    editingId.value = ''
    await refreshPlan()
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function reorder(it, direction) {
  error.value = ''
  try {
    plan.value = await api.novelShotItemReorder(props.novelId, selectedId.value, it.id, direction)
  } catch (e) {
    error.value = e.message
  }
}

async function approvePlan() {
  error.value = ''
  try {
    await api.novelShotPlanApprove(props.novelId, selectedId.value)
    validation.value = null
    await refreshPlan()
  } catch (e) {
    error.value = e.message
  }
}

// ---------- PR-N06：一致性校验 ----------
async function runValidate() {
  validating.value = true
  error.value = ''
  try {
    validation.value = await api.novelShotValidate(props.novelId, selectedId.value)
  } catch (e) {
    error.value = e.message
  } finally {
    validating.value = false
  }
}

async function handoff() {
  error.value = ''
  try {
    const r = await api.novelShotPlanHandoff(props.novelId, selectedId.value)
    router.push(`/projects/${r.project_id}`)
  } catch (e) {
    error.value = e.message
  }
}

async function loadVersion(v) {
  try {
    const r = await api.novelShotPlanVersion(props.novelId, selectedId.value, v)
    const items = r.snapshot.items || []
    versionSnapshot.value = items.map((i) =>
      `${i.seq}. [${i.scene_title}] ${i.title}｜${i.shot_size}/${i.camera_movement}｜` +
      `${i.duration_sec}s｜${modeLabel(i.mode)}｜${i.action}` +
      (i.dialogue ? `｜💬${i.dialogue}` : '') +
      (i.character_names && i.character_names.length ? `｜${i.character_names.join('、')}` : '')
    ).join('\n')
  } catch (e) {
    error.value = e.message
  }
}

function fmtTime(iso) {
  return (iso || '').slice(5, 16).replace('T', ' ')
}
</script>

<style scoped>
.sp-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  font-size: 12px;
}
.pick-ep {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-dim, #9aa5b8);
}
.pick-ep select {
  min-width: 220px;
}
.tip {
  flex-basis: 100%;
}
.plan-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.plan-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.plan-note {
  font-size: 12px;
  margin-bottom: 10px;
}
.handoff-ok {
  color: #4ade80;
}
.badge {
  padding: 1px 8px;
  font-size: 11px;
  border-radius: 999px;
}
.badge.draft {
  color: var(--text-dim, #9aa5b8);
  background: var(--bg-secondary, #222a3a);
}
.badge.approved {
  color: #4ade80;
  background: rgba(74, 222, 128, 0.12);
}
/* ---------- PR-N06：一致性校验 ---------- */
.validate-sec {
  border: 1px solid rgba(250, 204, 21, 0.35);
  background: rgba(250, 204, 21, 0.06);
  border-radius: 8px;
  padding: 10px 14px;
  margin-top: 10px;
}
.validate-sec.ok {
  border-color: rgba(74, 222, 128, 0.35);
  background: rgba(74, 222, 128, 0.06);
}
.val-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.val-head .btn-sm {
  margin-left: auto;
}
.badge.warn {
  color: #facc15;
  background: rgba(250, 204, 21, 0.12);
}
.badge.ok {
  color: #4ade80;
  background: rgba(74, 222, 128, 0.12);
}
.badge.stale {
  color: #fb923c;
  background: rgba(251, 146, 60, 0.12);
  margin-top: 8px;
}
.val-warnings {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.7;
}
.val-items {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.val-item {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
}
.sub-warn {
  font-size: 12px;
  color: #facc15;
  padding: 6px 2px 0;
}

/* ---------- PR-N05：提交批次 ---------- */
.submit-sec {
  margin-bottom: 12px;
}
.submit-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  font-size: 12.5px;
  margin-bottom: 8px;
}
.submit-bar .sel {
  min-width: 130px;
}
.sub-card {
  border: 1px solid var(--border, #2a3345);
  border-radius: 10px;
  padding: 8px 10px;
  margin-bottom: 8px;
  font-size: 12.5px;
}
.sub-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.sub-head .ops {
  margin-left: auto;
  display: flex;
  gap: 6px;
}
.fail-tip {
  color: #f87171;
}
.sub-items {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.sub-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 4px;
  border-radius: 6px;
  background: var(--bg-secondary, #222a3a);
}
.it-title {
  min-width: 100px;
}
.st-chip {
  padding: 0 7px;
  font-size: 10.5px;
  border-radius: 999px;
  background: var(--bg-primary, #161c28);
}
.st-chip.done { color: #4ade80; }
.st-chip.running, .st-chip.queued { color: #38bdf8; }
.st-chip.pending { color: var(--text-dim, #9aa5b8); }
.st-chip.failed { color: #f87171; }
.st-chip.cancelled { color: var(--text-dim, #9aa5b8); }
.it-err {
  color: #f87171;
  font-size: 11.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 420px;
}
.badge.done, .badge.running {
  color: #4ade80;
  background: rgba(74, 222, 128, 0.12);
}
.badge.partial {
  color: #f0b429;
  background: rgba(240, 180, 41, 0.12);
}
.badge.failed {
  color: #f87171;
  background: rgba(248, 113, 113, 0.12);
}
.badge.cancelled, .badge.cancelling, .badge.pending {
  color: var(--text-dim, #9aa5b8);
  background: var(--bg-secondary, #222a3a);
}
.shot-table {
  border: 1px solid var(--border, #2a3345);
  border-radius: 10px;
  overflow: hidden;
}
.shot-row {
  display: grid;
  grid-template-columns: 36px 1fr 64px 64px 56px 92px 132px;
  gap: 8px;
  align-items: start;
  padding: 8px 10px;
  font-size: 12.5px;
  border-top: 1px solid var(--border, #2a3345);
}
.shot-row.head {
  border-top: none;
  font-size: 11px;
  color: var(--text-dim, #9aa5b8);
  background: var(--bg-secondary, #222a3a);
}
.shot-row.editing {
  background: rgba(139, 92, 246, 0.06);
}
.c-seq {
  color: var(--text-dim, #9aa5b8);
  text-align: center;
}
.c-title {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.scene-tag {
  font-size: 11px;
}
.dlg {
  font-size: 12px;
  color: #f0b429;
}
.action {
  color: var(--text-dim, #9aa5b8);
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 2px;
}
.chip {
  padding: 0 6px;
  font-size: 10.5px;
  border-radius: 999px;
  background: var(--bg-secondary, #222a3a);
  color: var(--text-dim, #9aa5b8);
}
.chip.char {
  color: #8b5cf6;
}
.chip.pos, .chip.ch {
  color: #38bdf8;
}
.chip.emo {
  color: #f0b429;
}
.mode-chip {
  padding: 1px 7px;
  font-size: 11px;
  border-radius: 999px;
  background: var(--bg-secondary, #222a3a);
}
.mode-chip.t2v { color: #8b5cf6; }
.mode-chip.i2v { color: #38bdf8; }
.mode-chip.first_last { color: #f0b429; }
.mode-chip.continuation { color: #4ade80; }
.c-ops {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.shot-editor {
  padding: 10px 12px;
  border-top: 1px solid var(--border, #2a3345);
  background: var(--bg-secondary, #222a3a);
}
.egrid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px 12px;
  font-size: 12px;
}
.egrid label {
  display: flex;
  flex-direction: column;
  gap: 3px;
  color: var(--text-dim, #9aa5b8);
}
.egrid .wide {
  grid-column: span 3;
}
.egrid input, .egrid select {
  width: 100%;
}
.ver-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  font-size: 13px;
}
.ver-snap {
  max-height: 300px;
  overflow: auto;
  margin-top: 10px;
  padding: 10px 12px;
  font-size: 12px;
  white-space: pre-wrap;
  background: var(--bg-secondary, #222a3a);
  border-radius: 8px;
}
@media (max-width: 1100px) {
  .shot-row {
    grid-template-columns: 28px 1fr 56px 100px;
  }
  .shot-row .c-size, .shot-row .c-move, .shot-row .c-dur { display: none; }
  .egrid { grid-template-columns: 1fr; }
  .egrid .wide { grid-column: span 1; }
}
</style>
