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
          <button class="btn btn-primary" :disabled="plan.status !== 'approved'" @click="handoff">
            🎬 交接为项目分镜
          </button>
        </div>
      </header>
      <div v-if="plan.status === 'approved'" class="dim plan-note">
        已审核（v{{ plan.version }}）——改动任意镜头后自动回草稿态，版本历史保留；交接后项目分镜按当前审核态重建。
      </div>

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
import { onMounted, ref } from 'vue'
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

onMounted(load)

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
  if (selectedId.value) await refreshPlan()
}

async function refreshPlan() {
  try {
    plan.value = await api.novelShotPlan(props.novelId, selectedId.value)
  } catch (e) {
    if (e.status === 404 || (e.message || '').includes('尚未生成')) {
      plan.value = null
    } else {
      error.value = e.message
    }
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
    await refreshPlan()
  } catch (e) {
    error.value = e.message
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
