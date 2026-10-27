<template>
  <div class="ep-panel">
    <!-- AI 分集大纲工具条 -->
    <div class="outline-bar">
      <button class="btn btn-primary" :disabled="generating" @click="generateOutline">
        {{ generating ? '规划中…' : '✨ AI 生成分集大纲' }}
      </button>
      <label class="hint-input">期望集数
        <input type="number" v-model.number="outlineCount" min="1" max="50" />
      </label>
      <label class="hint-input">单集时长
        <input type="number" v-model.number="outlineDuration" min="30" max="300" step="10" /> 秒
      </label>
      <span class="dim tip">以故事圣经 + 各章梗概为输入；重新生成会替换既有分集（其剧本随级联删除）</span>
    </div>
    <div v-if="error" class="form-msg">{{ error }}</div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="!episodes.length" class="empty">
      还没有分集。点「AI 生成分集大纲」（需先完成 PR-N02 章节分析与故事圣经），或手动新增。
    </div>

    <div v-else class="ep-layout">
      <!-- 分集列表 -->
      <aside class="ep-list">
        <div v-for="ep in episodes" :key="ep.id" class="ep-card"
             :class="{ active: ep.id === selectedId }" @click="select(ep.id)">
          <div class="ep-card-head">
            <span class="ep-seq">{{ ep.seq }}</span>
            <span class="ep-title">{{ ep.title }}</span>
            <span class="badge" :class="ep.status">{{ ep.status === 'approved' ? '已审核' : '草稿' }}</span>
            <button class="icon-btn danger" title="删除分集" @click.stop="remove(ep)">🗑</button>
          </div>
          <div class="ep-meta">{{ ep.target_duration_sec }} 秒 · 覆盖 {{ ep.chapter_refs.length }} 章</div>
          <div class="ep-sub">
            <span v-if="ep.script_status">
              剧本{{ ep.script_status === 'approved' ? '已审核' : '草稿' }}
              v{{ ep.script_version }} · {{ ep.script_scene_count }} 场 {{ ep.script_shot_count }} 镜
            </span>
            <span v-else class="dim">未生成剧本</span>
            <span v-if="ep.script_project_id" class="handoff-ok">→ 已交接项目</span>
          </div>
        </div>
        <button class="btn" @click="showCreate = true">＋ 手动新增分集</button>
      </aside>

      <!-- 分集详情 + 剧本 -->
      <section v-if="detail" class="ep-detail">
        <header class="detail-head">
          <h3>{{ detail.title }}</h3>
          <div class="ops">
            <button class="btn" @click="approveEpisode" :disabled="detail.status === 'approved'">
              ✔ 审核分集（v{{ (detail.version || 0) + 1 }}）
            </button>
            <button class="btn" @click="showEpVersions = true">分集版本</button>
          </div>
        </header>

        <div class="field">
          <label>集名</label>
          <input v-model="edit.title" maxlength="120" />
        </div>
        <div class="field">
          <label>一句话梗概</label>
          <textarea v-model="edit.logline" rows="2"></textarea>
        </div>
        <div class="field">
          <label>目标时长（秒，30-300）</label>
          <input type="number" v-model.number="edit.target_duration_sec" min="30" max="300" />
        </div>
        <div class="field">
          <label>覆盖章节（多选；决定剧本取材范围与原文定位）</label>
          <div class="chapter-picker">
            <label v-for="c in chapters" :key="c.id" class="pick">
              <input type="checkbox" :value="c.id" v-model="edit.chapter_ids" />
              <span>{{ c.seq }}. {{ c.title }}</span>
            </label>
          </div>
        </div>
        <div class="field">
          <label>节拍（每行一个，指导剧本结构）</label>
          <textarea v-model="beatsText" rows="4" placeholder="竹林对峙&#10;拔剑突进&#10;长老喝止"></textarea>
        </div>
        <div class="ops">
          <button class="btn btn-primary" :disabled="saving" @click="saveEpisode">
            {{ saving ? '保存中…' : '保存分集' }}
          </button>
          <span v-if="detail.status === 'approved'" class="dim">已审核（v{{ detail.version }}）——改动后自动回草稿态，版本历史保留</span>
        </div>

        <hr />

        <div class="script-head">
          <h3>动漫剧本</h3>
          <div class="ops">
            <button class="btn" :disabled="genScript" @click="generateScript">
              {{ genScript ? '生成中…' : '✨ 生成剧本' }}
            </button>
            <button class="btn" :disabled="!script || !scriptDirty || savingScript" @click="saveScript">
              保存修改
            </button>
            <button class="btn" :disabled="!script || script.status === 'approved'" @click="approveScript">
              ✔ 审核剧本（v{{ script ? script.version + 1 : 1 }}）
            </button>
            <button class="btn btn-primary" :disabled="!script" @click="handoff">
              🎬 交接为项目
            </button>
            <button class="btn" :disabled="!script" @click="showScVersions = true">剧本版本</button>
          </div>
        </div>
        <div v-if="script" class="script-meta">
          {{ script.scene_count }} 场 / {{ script.shot_count }} 镜 ·
          {{ script.status === 'approved' ? '已审核' : '草稿' }} v{{ script.version }}
          <span v-if="script.project_id"> · 已交接项目</span>
          <span class="dim">（格式与分镜工作台剧本解析器兼容，可继续手动编辑）</span>
        </div>
        <textarea v-if="script" class="script-editor" v-model="scriptContent"
                  @input="scriptDirty = true"></textarea>
        <div v-else class="empty dim">
          还没有剧本。点「生成剧本」——LLM 按故事圣经的人物/场景设定 + 本章节原文，
          改写为「场景一：xxx / 画面行（景别, 运动, 秒数）/ 人物：台词」格式。
        </div>
      </section>
    </div>

    <!-- 手动新增分集弹窗 -->
    <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
      <div class="modal small">
        <header class="modal-head">
          <h2>手动新增分集</h2>
          <button class="icon-btn" @click="showCreate = false">✕</button>
        </header>
        <div class="modal-body">
          <div class="field">
            <label>集名</label>
            <input v-model="createForm.title" placeholder="如：第3话 格挡" />
          </div>
          <div class="field">
            <label>目标时长（秒）</label>
            <input type="number" v-model.number="createForm.target_duration_sec" min="30" max="300" />
          </div>
        </div>
        <footer class="modal-foot">
          <button class="btn" style="margin-left: auto" @click="showCreate = false">取消</button>
          <button class="btn btn-primary" :disabled="!createForm.title" @click="createEpisode">创建</button>
        </footer>
      </div>
    </div>

    <!-- 分集版本历史弹窗 -->
    <div v-if="showEpVersions" class="modal-mask" @click.self="showEpVersions = false">
      <div class="modal">
        <header class="modal-head">
          <h2>分集版本历史（不可变快照）</h2>
          <button class="icon-btn" @click="showEpVersions = false">✕</button>
        </header>
        <div class="modal-body">
          <div v-if="!detail.episode_versions.length" class="empty dim">还没有审核过的版本</div>
          <div v-for="v in detail.episode_versions" :key="v.version" class="ver-row">
            <button class="btn btn-sm" @click="loadEpVersion(v.version)">v{{ v.version }}</button>
            <span class="dim">{{ fmtTime(v.created_at) }}</span>
            <span v-if="v.note">{{ v.note }}</span>
          </div>
          <pre v-if="epVersionSnapshot" class="ver-snap">{{ epVersionSnapshot }}</pre>
        </div>
      </div>
    </div>

    <!-- 剧本版本历史弹窗 -->
    <div v-if="showScVersions" class="modal-mask" @click.self="showScVersions = false">
      <div class="modal">
        <header class="modal-head">
          <h2>剧本版本历史（不可变快照）</h2>
          <button class="icon-btn" @click="showScVersions = false">✕</button>
        </header>
        <div class="modal-body">
          <div v-if="!script || !script.versions.length" class="empty dim">还没有审核过的版本</div>
          <template v-else>
            <div v-for="v in script.versions" :key="v.version" class="ver-row">
              <button class="btn btn-sm" @click="loadScVersion(v.version)">v{{ v.version }}</button>
              <span class="dim">{{ fmtTime(v.created_at) }}</span>
              <span v-if="v.note">{{ v.note }}</span>
            </div>
            <pre v-if="scVersionSnapshot" class="ver-snap">{{ scVersionSnapshot }}</pre>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const props = defineProps({
  novelId: { type: String, required: true },
  chapters: { type: Array, default: () => [] },
})
const emit = defineEmits(['locate'])
const router = useRouter()

const episodes = ref([])
const detail = ref(null)
const script = ref(null)
const scriptContent = ref('')
const scriptDirty = ref(false)
const selectedId = ref('')
const loading = ref(true)
const error = ref('')

const generating = ref(false)
const saving = ref(false)
const genScript = ref(false)
const savingScript = ref(false)
const outlineCount = ref(0)
const outlineDuration = ref(0)

const showCreate = ref(false)
const createForm = ref({ title: '', target_duration_sec: 90 })
const showEpVersions = ref(false)
const showScVersions = ref(false)
const epVersionSnapshot = ref('')
const scVersionSnapshot = ref('')

const edit = ref({ title: '', logline: '', target_duration_sec: 90, chapter_ids: [] })
const beatsText = ref('')

onMounted(load)

async function load() {
  loading.value = true
  error.value = ''
  try {
    episodes.value = await api.novelEpisodes(props.novelId)
    if (episodes.value.length && !selectedId.value) {
      await select(episodes.value[0].id)
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function select(id) {
  selectedId.value = id
  scriptDirty.value = false
  try {
    detail.value = await api.novelEpisode(props.novelId, id)
    syncEdit()
  } catch (e) {
    error.value = e.message
  }
}

function syncEdit() {
  const d = detail.value
  if (!d) return
  edit.value = {
    title: d.title,
    logline: d.logline || '',
    target_duration_sec: d.target_duration_sec || 90,
    chapter_ids: (d.chapter_refs || []).map((r) => r.chapter_id),
  }
  beatsText.value = (d.beats || []).map((b) => b.title).filter(Boolean).join('\n')
  script.value = d.script || null
  scriptContent.value = script.value ? script.value.content : ''
  scriptDirty.value = false
}

function parseBeats() {
  return beatsText.value.split('\n').map((t) => t.trim()).filter(Boolean)
    .map((t) => ({ title: t, summary: '' }))
}

async function generateOutline() {
  generating.value = true
  error.value = ''
  try {
    const body = {}
    if (outlineCount.value) body.episode_count = outlineCount.value
    if (outlineDuration.value) body.target_duration = outlineDuration.value
    episodes.value = await api.novelOutlineGenerate(props.novelId, body)
    selectedId.value = ''
    detail.value = null
    script.value = null
    if (episodes.value.length) await select(episodes.value[0].id)
  } catch (e) {
    error.value = e.message
  } finally {
    generating.value = false
  }
}

async function createEpisode() {
  error.value = ''
  try {
    await api.novelEpisodeCreate(props.novelId, { ...createForm.value })
    showCreate.value = false
    createForm.value = { title: '', target_duration_sec: 90 }
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function saveEpisode() {
  saving.value = true
  error.value = ''
  try {
    await api.novelEpisodeUpdate(props.novelId, selectedId.value, {
      title: edit.value.title,
      logline: edit.value.logline,
      target_duration_sec: edit.value.target_duration_sec,
      chapter_ids: edit.value.chapter_ids,
      beats: parseBeats(),
    })
    await load()
    await select(selectedId.value)
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function approveEpisode() {
  error.value = ''
  try {
    await api.novelEpisodeApprove(props.novelId, selectedId.value)
    await load()
    await select(selectedId.value)
  } catch (e) {
    error.value = e.message
  }
}

async function remove(ep) {
  if (!confirm(`删除分集「${ep.title}」？其剧本与版本一并删除。`)) return
  error.value = ''
  try {
    await api.novelEpisodeDelete(props.novelId, ep.id)
    if (selectedId.value === ep.id) {
      selectedId.value = ''
      detail.value = null
      script.value = null
    }
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function generateScript() {
  genScript.value = true
  error.value = ''
  try {
    await api.novelScriptGenerate(props.novelId, selectedId.value)
    await select(selectedId.value)
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    genScript.value = false
  }
}

async function saveScript() {
  savingScript.value = true
  error.value = ''
  try {
    await api.novelScriptUpdate(props.novelId, selectedId.value, {
      content: scriptContent.value,
    })
    scriptDirty.value = false
    await select(selectedId.value)
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    savingScript.value = false
  }
}

async function approveScript() {
  error.value = ''
  try {
    await api.novelScriptApprove(props.novelId, selectedId.value)
    await select(selectedId.value)
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function handoff() {
  error.value = ''
  try {
    const r = await api.novelScriptHandoff(props.novelId, selectedId.value)
    if (r.already) {
      alert(`该剧本已交接过了，直接打开既有项目「${r.project_name}」。`)
    }
    router.push(`/projects/${r.project_id}`)
  } catch (e) {
    error.value = e.message
  }
}

async function loadEpVersion(v) {
  try {
    const r = await api.novelEpisodeVersion(props.novelId, selectedId.value, v)
    epVersionSnapshot.value = JSON.stringify(r.snapshot, null, 2)
  } catch (e) {
    error.value = e.message
  }
}

async function loadScVersion(v) {
  try {
    const r = await api.novelScriptVersion(props.novelId, selectedId.value, v)
    scVersionSnapshot.value = r.snapshot.content || JSON.stringify(r.snapshot, null, 2)
  } catch (e) {
    error.value = e.message
  }
}

function fmtTime(iso) {
  return (iso || '').slice(5, 16).replace('T', ' ')
}
</script>

<style scoped>
.outline-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  font-size: 12px;
}
.hint-input {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-dim, #9aa5b8);
}
.hint-input input {
  width: 64px;
}
.tip {
  flex-basis: 100%;
}
.ep-layout {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 14px;
  align-items: start;
}
.ep-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ep-card {
  padding: 10px 12px;
  border: 1px solid var(--border, #2a3345);
  border-radius: 10px;
  cursor: pointer;
}
.ep-card.active {
  border-color: var(--accent);
}
.ep-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ep-seq {
  min-width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--text-dim, #9aa5b8);
  background: var(--bg-secondary, #222a3a);
  border-radius: 6px;
}
.ep-title {
  flex: 1;
  font-weight: 500;
  font-size: 13px;
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
.ep-meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-dim, #9aa5b8);
}
.ep-sub {
  display: flex;
  gap: 8px;
  margin-top: 2px;
  font-size: 11px;
  color: var(--text-dim, #6b7688);
}
.handoff-ok {
  color: #4ade80;
}
.ep-detail {
  padding: 14px 16px;
  border: 1px solid var(--border, #2a3345);
  border-radius: 12px;
}
.detail-head, .script-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.detail-head h3, .script-head h3 {
  margin: 0;
}
.ops {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.field {
  margin-top: 12px;
}
.field label {
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--text-dim, #9aa5b8);
}
.chapter-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  max-height: 132px;
  overflow: auto;
  padding: 8px 10px;
  background: var(--bg-secondary, #222a3a);
  border-radius: 8px;
  font-size: 12px;
}
.pick {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
}
.script-meta {
  margin: 8px 0 6px;
  font-size: 12px;
  color: var(--text-dim, #9aa5b8);
}
.script-editor {
  width: 100%;
  min-height: 320px;
  padding: 10px 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
  line-height: 1.8;
  white-space: pre;
  overflow: auto;
  color: var(--text, #e5e9f0);
  background: var(--bg-secondary, #222a3a);
  border: 1px solid var(--border, #2a3345);
  border-radius: 8px;
  resize: vertical;
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
</style>
