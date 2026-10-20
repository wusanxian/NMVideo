<template>
  <div class="bible-panel">
    <!-- 顶部：状态 + 操作 -->
    <header class="bible-head">
      <div class="bible-title">
        <span class="badge" :class="bible && bible.bible.status === 'approved' ? 'badge-ok' : 'badge-draft'">
          {{ bible ? (bible.bible.status === 'approved' ? `已审核 v${bible.bible.version}` : '草稿') : '未生成' }}
        </span>
        <span v-if="bible" class="dim">{{ bible.bible.summary }}</span>
      </div>
      <div class="bible-ops">
        <input v-model="approveNote" class="note-input" placeholder="审核备注（可选）" />
        <button class="btn btn-primary btn-sm" :disabled="!bible || approving" @click="approve">
          {{ approving ? '提交中…' : '✓ 审核通过' }}
        </button>
        <button class="btn btn-sm" :disabled="generating" @click="generate">
          {{ generating ? '生成中…' : (bible ? '🔄 重新生成草案' : '📖 生成故事圣经') }}
        </button>
        <button v-if="bible" class="btn btn-sm" @click="showVersions = true">🕘 版本历史</button>
      </div>
    </header>

    <div v-if="err" class="form-msg">{{ err }}</div>

    <!-- AI 章节分析 -->
    <section class="card">
      <header class="card-head">
        <div>
          <h3>AI 章节分析</h3>
          <div class="dim">逐章调用 LLM 提取梗概 / 人物 / 场景 / 事件 / 人物关系（AI 草案，全部可改）</div>
        </div>
        <div class="card-ops">
          <label class="chk"><input type="checkbox" v-model="force" /> 强制重新分析</label>
          <button class="btn btn-primary btn-sm" :disabled="analyzing || analysis.running" @click="startAnalyze">
            {{ analyzing ? '启动中…' : (analysis.running ? '分析中…' : '▶ 开始分析') }}
          </button>
        </div>
      </header>

      <div v-if="analysis.total" class="progress-row">
        <div class="progress-bar">
          <div class="progress-inner" :style="{ width: progressPct + '%' }" />
        </div>
        <span class="dim">{{ analysis.done }}/{{ analysis.total }} 章已完成
          <template v-if="analysis.error"> · {{ analysis.error }} 章失败</template>
          <template v-if="analysis.running"> · 进行中…</template>
        </span>
      </div>

      <div v-if="!analysis.total" class="empty-hint">
        还没有分析记录。点「开始分析」（需要先在「模型服务」页配置 LLM）。
      </div>
      <div v-else class="ana-list">
        <div v-for="it in analysis.items" :key="it.chapter_id" class="ana-row" :class="'st-' + it.status">
          <span class="badge" :class="statusClass(it.status)">{{ statusLabel(it.status) }}</span>
          <span class="ana-title">{{ it.chapter_title || '（无标题）' }}</span>
          <span v-if="it.status === 'done'" class="ana-summary">{{ it.summary }}</span>
          <span v-else-if="it.status === 'error'" class="ana-error" :title="it.error">⚠ {{ it.error }}</span>
          <span v-else class="dim">等待分析…</span>
        </div>
      </div>
    </section>

    <!-- 圣经实体 -->
    <template v-if="bible">
      <section v-for="sec in SECTIONS" :key="sec.kind" class="card">
        <header class="card-head">
          <h3>{{ sec.label }}（{{ (bible[sec.kind] || []).length }}）</h3>
          <button class="btn btn-sm" @click="startAdd(sec.kind)">＋ 新增{{ sec.label }}</button>
        </header>
        <div class="table-wrap">
          <table class="grid">
            <thead>
              <tr>
                <th v-for="col in sec.cols" :key="col.key" :style="col.width ? { width: col.width } : {}">{{ col.label }}</th>
                <th style="width: 150px">操作</th>
              </tr>
            </thead>
            <tbody>
              <!-- 新增行 -->
              <tr v-if="adding && adding.kind === sec.kind" class="row-adding">
                <td v-for="col in sec.cols" :key="col.key">
                  <input v-if="col.key !== 'seq'" v-model="adding.fields[col.key]"
                         :placeholder="col.label" @keyup.enter="saveAdd" />
                  <span v-else class="dim">—</span>
                </td>
                <td>
                  <button class="btn btn-primary btn-sm" @click="saveAdd">保存</button>
                  <button class="btn btn-sm" @click="adding = null">取消</button>
                </td>
              </tr>
              <tr v-for="row in (bible[sec.kind] || [])" :key="row.id">
                <template v-if="editing && editing.kind === sec.kind && editing.id === row.id">
                  <td v-for="col in sec.cols" :key="col.key">
                    <input v-if="col.key !== 'seq' && col.key !== 'chapters'" v-model="editing.fields[col.key]" />
                    <span v-else-if="col.key === 'seq'" class="dim">{{ row.seq }}</span>
                    <span v-else class="dim">随分析</span>
                  </td>
                  <td>
                    <button class="btn btn-primary btn-sm" @click="saveEdit">保存</button>
                    <button class="btn btn-sm" @click="editing = null">取消</button>
                  </td>
                </template>
                <template v-else>
                  <td v-for="col in sec.cols" :key="col.key">
                    <span v-if="col.key === 'chapters'" class="chips">
                      <span v-for="r in (row.chapters || [])" :key="r.chapter_id" class="chip"
                            :class="{ dead: !chapterById[r.chapter_id] }"
                            :title="chapterById[r.chapter_id] ? '定位到章节：' + r.title : '章节已变更，原文位置 [' + r.start + ', ' + r.end + ']'"
                            @click="chapterById[r.chapter_id] && locate(r.chapter_id)">
                        {{ chapterById[r.chapter_id] ? chapterById[r.chapter_id].title : (r.title || '已失效') }}
                      </span>
                      <span v-if="!(row.chapters || []).length" class="dim">—</span>
                    </span>
                    <span v-else-if="col.array" :class="{ dim: !(row[col.key] || []).length }">
                      {{ (row[col.key] || []).join('、') || '—' }}
                    </span>
                    <span v-else :class="{ dim: !row[col.key] }">{{ row[col.key] || '—' }}</span>
                  </td>
                  <td>
                    <button class="btn btn-sm" @click="startEdit(sec.kind, row)">编辑</button>
                    <button class="btn btn-sm danger" @click="remove(sec.kind, row)">删除</button>
                  </td>
                </template>
              </tr>
              <tr v-if="!(bible[sec.kind] || []).length && !(adding && adding.kind === sec.kind)">
                <td :colspan="sec.cols.length + 1" class="dim" style="text-align: center; padding: 14px">
                  暂无{{ sec.label }}——生成圣经后自动汇总，也可手动新增
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
    <div v-else class="empty-hint">
      还没有故事圣经。先完成「AI 章节分析」，再点右上角「生成故事圣经」。
    </div>

    <!-- 版本历史弹窗 -->
    <div v-if="showVersions" class="modal-mask" @click.self="showVersions = false">
      <div class="modal">
        <header class="modal-head">
          <h2>版本历史</h2>
          <button class="icon-btn" @click="showVersions = false">✕</button>
        </header>
        <div class="modal-body">
          <div v-if="!(bible.versions || []).length" class="dim">还没有审核通过的版本。</div>
          <div v-for="v in bible.versions" :key="v.version" class="ver-row">
            <span class="badge badge-ok">v{{ v.version }}</span>
            <span class="dim">{{ fmtTime(v.created_at) }}</span>
            <span v-if="v.note">{{ v.note }}</span>
            <button class="btn btn-sm" @click="viewVersion(v.version)">查看快照</button>
          </div>
          <div v-if="versionSnapshot" class="ver-snapshot">
            <h4>v{{ versionSnapshot.version }} 快照（不可变）</h4>
            <p class="dim">{{ versionSnapshot.snapshot.summary }}</p>
            <div class="dim">
              角色 {{ (versionSnapshot.snapshot.characters || []).length }} ·
              场景 {{ (versionSnapshot.snapshot.scenes || []).length }} ·
              事件 {{ (versionSnapshot.snapshot.timeline || []).length }} ·
              关系 {{ (versionSnapshot.snapshot.relations || []).length }}
            </div>
            <ul class="ver-names">
              <li v-for="c in (versionSnapshot.snapshot.characters || [])" :key="c.id">{{ c.name }}</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps({
  novelId: { type: String, required: true },
  chapters: { type: Array, default: () => [] },
})
const emits = defineEmits(['locate'])

const bible = ref(null)
const analysis = ref({ running: false, total: 0, pending: 0, done: 0, error: 0, items: [] })
const err = ref('')
const analyzing = ref(false)
const generating = ref(false)
const approving = ref(false)
const force = ref(false)
const approveNote = ref('')
const editing = ref(null)   // { kind, id, fields }
const adding = ref(null)    // { kind, fields }
const showVersions = ref(false)
const versionSnapshot = ref(null)

const chapterById = computed(() => {
  const m = {}
  for (const c of props.chapters) m[c.id] = c
  return m
})

const SECTIONS = [
  { kind: 'characters', label: '角色', cols: [
    { key: 'name', label: '名称', width: '100px' },
    { key: 'role', label: '定位', width: '70px' },
    { key: 'appearance', label: '外貌' },
    { key: 'clothing', label: '服装' },
    { key: 'personality', label: '性格' },
    { key: 'catchphrase', label: '口头禅' },
    { key: 'aliases', label: '别称', array: true, width: '110px' },
    { key: 'notes', label: '备注' },
  ] },
  { kind: 'scenes', label: '场景', cols: [
    { key: 'name', label: '名称', width: '120px' },
    { key: 'description', label: '环境要点' },
    { key: 'atmosphere', label: '氛围' },
  ] },
  { kind: 'timeline', label: '时间线', cols: [
    { key: 'seq', label: '#' },
    { key: 'title', label: '事件', width: '140px' },
    { key: 'summary', label: '说明' },
    { key: 'character_names', label: '涉及人物', array: true, width: '130px' },
  ] },
  { kind: 'relations', label: '人物关系', cols: [
    { key: 'from_character', label: '人物', width: '100px' },
    { key: 'relation_type', label: '关系', width: '80px' },
    { key: 'to_character', label: '对象', width: '100px' },
    { key: 'description', label: '说明' },
  ] },
]

const progressPct = computed(() =>
  analysis.value.total ? Math.round((analysis.value.done / analysis.value.total) * 100) : 0)

function statusClass(s) {
  return { pending: 'badge-draft', running: 'badge-run', done: 'badge-ok', error: 'badge-err' }[s] || 'badge-draft'
}
function statusLabel(s) {
  return { pending: '等待', running: '分析中', done: '完成', error: '失败' }[s] || s
}
function fmtTime(iso) {
  return (iso || '').slice(5, 16).replace('T', ' ')
}
function locate(chapterId) {
  emits('locate', chapterId)
}

async function loadBible() {
  try {
    bible.value = await api.novelBible(props.novelId)
  } catch (e) {
    if (e.status === 404) bible.value = null
    else err.value = e.message
  }
}

async function refreshAnalysis() {
  try {
    analysis.value = await api.novelAnalyzeStatus(props.novelId)
  } catch (e) {
    err.value = e.message
  }
}

let pollTimer = null
function ensurePolling() {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    await refreshAnalysis()
    if (!analysis.value.running && analysis.value.pending === 0) {
      stopPolling()
    }
  }, 1500)
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function startAnalyze() {
  analyzing.value = true
  err.value = ''
  try {
    await api.novelAnalyzeStart(props.novelId, { force: force.value })
    await refreshAnalysis()
    if (analysis.value.running || analysis.value.pending > 0) ensurePolling()
  } catch (e) {
    err.value = e.message
    await refreshAnalysis()
  } finally {
    analyzing.value = false
  }
}

async function generate() {
  if (bible.value && !confirm('重新生成会替换当前四类实体（版本历史保留）。确认？')) return
  generating.value = true
  err.value = ''
  try {
    bible.value = await api.novelBibleGenerate(props.novelId)
  } catch (e) {
    err.value = e.message
  } finally {
    generating.value = false
  }
}

async function approve() {
  approving.value = true
  err.value = ''
  try {
    await api.novelBibleApprove(props.novelId, approveNote.value)
    approveNote.value = ''
    await loadBible()
  } catch (e) {
    err.value = e.message
  } finally {
    approving.value = false
  }
}

function splitList(v) {
  return String(v || '').split(/[,，、;；]/).map((s) => s.trim()).filter(Boolean)
}

function startEdit(kind, row) {
  adding.value = null
  const fields = {}
  for (const sec of SECTIONS) {
    if (sec.kind !== kind) continue
    for (const col of sec.cols) {
      if (col.array) fields[col.key] = (row[col.key] || []).join('、')
      else fields[col.key] = row[col.key] || ''
    }
  }
  editing.value = { kind, id: row.id, fields }
}

function collectFields(draft, kind) {
  const out = {}
  for (const sec of SECTIONS) {
    if (sec.kind !== kind) continue
    for (const col of sec.cols) {
      if (col.key === 'seq' || col.key === 'chapters') continue
      const raw = draft[col.key]
      if (col.array) {
        const list = splitList(raw)
        if (list.length) out[col.key] = list
      } else if (String(raw || '').trim()) {
        out[col.key] = String(raw).trim()
      }
    }
  }
  return out
}

async function saveEdit() {
  err.value = ''
  try {
    await api.bibleEntityUpdate(props.novelId, editing.value.kind, editing.value.id,
      collectFields(editing.value.fields, editing.value.kind))
    editing.value = null
    await loadBible()
  } catch (e) {
    err.value = e.message
  }
}

function startAdd(kind) {
  editing.value = null
  adding.value = { kind, fields: {} }
}

async function saveAdd() {
  err.value = ''
  try {
    await api.bibleEntityCreate(props.novelId, adding.value.kind,
      collectFields(adding.value.fields, adding.value.kind))
    adding.value = null
    await loadBible()
  } catch (e) {
    err.value = e.message
  }
}

async function remove(kind, row) {
  const label = row.name || row.title || row.from_character || '该条目'
  if (!confirm(`删除「${label}」？`)) return
  err.value = ''
  try {
    await api.bibleEntityDelete(props.novelId, kind, row.id)
    await loadBible()
  } catch (e) {
    err.value = e.message
  }
}

async function viewVersion(version) {
  try {
    versionSnapshot.value = await api.novelBibleVersion(props.novelId, version)
  } catch (e) {
    err.value = e.message
  }
}

watch(() => props.novelId, async (id) => {
  if (!id) return
  stopPolling()
  editing.value = null
  adding.value = null
  await Promise.all([loadBible(), refreshAnalysis()])
  if (analysis.value.running || analysis.value.pending > 0) ensurePolling()
})

onUnmounted(stopPolling)

// 初始加载
refreshAnalysis().then(() => {
  if (analysis.value.running || analysis.value.pending > 0) ensurePolling()
})
loadBible()
</script>

<style scoped>
.bible-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.bible-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.bible-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  min-width: 0;
}
.bible-title .dim {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 480px;
}
.bible-ops {
  display: flex;
  gap: 8px;
  align-items: center;
}
.note-input {
  width: 160px;
}
.badge {
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 12px;
  white-space: nowrap;
}
.badge-ok { color: #4ade80; background: rgba(74, 222, 128, 0.12); }
.badge-draft { color: #fbbf24; background: rgba(251, 191, 36, 0.12); }
.badge-run { color: #60a5fa; background: rgba(96, 165, 250, 0.12); }
.badge-err { color: #f87171; background: rgba(248, 113, 113, 0.14); }
.card {
  padding: 14px 16px;
  background: var(--bg-card, #1a1f2b);
  border: 1px solid var(--border, #2a3345);
  border-radius: 12px;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.card-head h3 { margin: 0; font-size: 14px; }
.card-ops { display: flex; gap: 10px; align-items: center; }
.chk { font-size: 12px; color: var(--text-dim, #9aa5b8); display: flex; align-items: center; gap: 4px; }
.progress-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  font-size: 12px;
}
.progress-bar {
  flex: 1;
  height: 8px;
  border-radius: 4px;
  background: var(--bg-secondary, #222a3a);
  overflow: hidden;
}
.progress-inner {
  height: 100%;
  background: var(--accent, #8b5cf6);
  transition: width 0.3s;
}
.empty-hint {
  padding: 18px;
  text-align: center;
  font-size: 13px;
  color: var(--text-dim, #9aa5b8);
  background: var(--bg-card, #1a1f2b);
  border: 1px dashed var(--border, #2a3345);
  border-radius: 12px;
}
.ana-list { display: flex; flex-direction: column; gap: 4px; }
.ana-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  font-size: 12.5px;
  border-radius: 8px;
  background: var(--bg-secondary, #222a3a);
}
.ana-row.st-error { background: rgba(248, 113, 113, 0.08); }
.ana-title { font-weight: 500; white-space: nowrap; }
.ana-summary { color: var(--text-dim, #9aa5b8); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ana-error { color: #f87171; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.table-wrap { overflow-x: auto; }
.grid {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.grid th, .grid td {
  padding: 7px 9px;
  text-align: left;
  border-bottom: 1px solid var(--border, #2a3345);
  vertical-align: top;
}
.grid th {
  font-size: 11.5px;
  color: var(--text-dim, #9aa5b8);
  font-weight: 500;
  white-space: nowrap;
}
.grid input {
  width: 100%;
  min-width: 70px;
  box-sizing: border-box;
}
.row-adding td { background: rgba(139, 92, 246, 0.06); }
.chips { display: flex; flex-wrap: wrap; gap: 4px; }
.chip {
  padding: 1px 7px;
  font-size: 11px;
  border-radius: 6px;
  background: var(--bg-secondary, #222a3a);
  border: 1px solid var(--border, #2a3345);
  cursor: pointer;
  max-width: 130px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chip:hover { border-color: var(--accent, #8b5cf6); }
.chip.dead { color: #f87171; cursor: help; }
.ver-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border, #2a3345);
  font-size: 13px;
}
.ver-snapshot { margin-top: 14px; padding: 12px; background: var(--bg-secondary, #222a3a); border-radius: 8px; }
.ver-snapshot h4 { margin: 0 0 6px; font-size: 13px; }
.ver-snapshot p { margin: 0 0 6px; font-size: 12.5px; }
.ver-names { margin: 8px 0 0; padding-left: 18px; font-size: 12.5px; columns: 3; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.modal {
  width: 640px;
  max-width: 92vw;
  max-height: 84vh;
  overflow: auto;
  background: var(--bg-card, #1a1f2b);
  border: 1px solid var(--border, #2a3345);
  border-radius: 14px;
}
.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border, #2a3345);
}
.modal-head h2 { margin: 0; font-size: 16px; }
.modal-body { padding: 16px 18px; }
.form-msg {
  padding: 8px 12px;
  font-size: 12.5px;
  color: #f87171;
  background: rgba(248, 113, 113, 0.1);
  border-radius: 8px;
}
.dim { color: var(--text-dim, #9aa5b8); }
.btn-sm.danger { color: #f87171; }
</style>
