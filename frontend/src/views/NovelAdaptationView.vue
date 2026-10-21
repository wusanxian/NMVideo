<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>小说改编</h1>
        <div class="page-sub">导入小说 → 管理章节 →（后续批次）AI 故事分析 / 分集剧本 / 自动分镜</div>
      </div>
      <button class="btn btn-primary" @click="showImport = true">＋ 导入小说</button>
    </header>

    <div class="stage-hint">
      <b>第一期已交付：</b>PR-N01 小说导入（TXT / Markdown，UTF-8 / GBK / BIG5）、中文章节自动识别、
      章节查看 / 重命名 / 拆分 / 合并 / 重新识别、原文位置保留；
      <b>PR-N02 故事圣经</b>：AI 分章节理解（人物 / 场景 / 事件 / 关系）、结构化设定可编辑、审核版本、定位原小说；
      <b>PR-N03 分集与剧本</b>：AI 分集大纲（可编辑 / 审核 / 版本）、动漫剧本生成（工作台剧本格式）、
      审核后一键交接为项目进入分镜工作台。
      <span class="dim">结构化自动分镜（PR-N04）将在后续批次接入，所有 AI 草案均可编辑审核。</span>
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="!novels.length" class="empty">
      还没有导入小说。点右上角「导入小说」，支持 .txt / .md（≤5MB）。
    </div>

    <div v-else class="novel-layout">
      <!-- 小说列表 -->
      <aside class="novel-list">
        <div v-for="n in novels" :key="n.id" class="novel-card"
             :class="{ active: n.id === currentId }" @click="select(n.id)">
          <div class="novel-card-head">
            <span class="novel-name">{{ n.title }}</span>
            <button class="icon-btn danger" title="删除小说" @click.stop="remove(n)">🗑</button>
          </div>
          <div class="novel-meta">
            <span v-if="n.author">✍ {{ n.author }}</span>
            <span>📄 {{ n.chapter_count }} 章</span>
            <span>🔤 {{ fmtCount(n.char_count) }} 字</span>
          </div>
          <div class="novel-sub">{{ n.encoding }} · {{ fmtTime(n.created_at) }} · #{{ n.content_sha256.slice(0, 8) }}</div>
        </div>
      </aside>

      <!-- 主区：章节管理 / 故事圣经 -->
      <section v-if="current" class="chapter-panel">
        <div class="tabs">
          <button class="tab" :class="{ active: activeTab === 'chapters' }" @click="activeTab = 'chapters'">
            📄 章节管理
          </button>
          <button class="tab" :class="{ active: activeTab === 'bible' }" @click="activeTab = 'bible'">
            📖 故事圣经
          </button>
          <button class="tab" :class="{ active: activeTab === 'episodes' }" @click="activeTab = 'episodes'">
            🎬 分集与剧本
          </button>
        </div>

        <template v-if="activeTab === 'chapters'">
          <header class="chapter-head">
            <div>
              <h2 style="margin: 0">{{ current.title }}</h2>
              <div class="dim" style="font-size: 12px">
                共 {{ chapters.length }} 章 · 原文 {{ fmtCount(current.char_count) }} 字 ·
                点击章节查看原文；拆分/合并仅改章节边界，不回写正文
              </div>
            </div>
            <div style="display: flex; gap: 8px">
              <button class="btn" :disabled="reparsing" @click="reparse">
                {{ reparsing ? '识别中…' : '🔄 重新识别章节' }}
              </button>
            </div>
          </header>

          <div class="chapter-list">
            <div v-for="c in chapters" :key="c.id" class="chapter-row">
              <div class="chapter-main">
                <span class="seq">{{ c.seq }}</span>
                <template v-if="editingId === c.id">
                  <input class="rename-input" v-model="editingTitle"
                         @keyup.enter="saveRename(c)" @keyup.esc="editingId = ''" />
                  <button class="btn btn-primary btn-sm" @click="saveRename(c)">保存</button>
                  <button class="btn btn-sm" @click="editingId = ''">取消</button>
                </template>
                <template v-else>
                  <span class="chapter-title" @click="toggle(c.id)">{{ c.title }}</span>
                  <span class="dim ch-meta">{{ c.char_count }} 字 · 位置 [{{ c.start }}, {{ c.end }}]</span>
                </template>
              </div>
              <div class="chapter-ops">
                <button class="btn btn-sm" @click="startRename(c)">重命名</button>
                <button class="btn btn-sm" @click="toggleSplit(c)">拆分</button>
                <button class="btn btn-sm" :disabled="c.seq >= chapters.length"
                        title="与下一章合并" @click="mergeNext(c)">合并下一章</button>
              </div>
            </div>

            <!-- 章节内容 / 拆分输入 -->
            <div v-for="c in chapters" :key="'d' + c.id">
              <div v-if="openId === c.id" class="chapter-detail">
                <pre class="chapter-content">{{ contentOf(c.id) || '加载中…' }}</pre>
                <div v-if="splitId === c.id" class="split-bar">
                  <label>在本章第 <input type="number" class="num" v-model.number="splitOffset"
                                        :min="1" :max="Math.max(1, c.char_count - 2)" /> 字处拆分
                    （本章共 {{ c.char_count }} 字）</label>
                  <button class="btn btn-primary btn-sm" :disabled="!splitOffset" @click="doSplit(c)">拆分为（上）/（下）</button>
                </div>
              </div>
            </div>
          </div>
        </template>

        <NovelBiblePanel v-else-if="activeTab === 'bible'" :novel-id="currentId" :chapters="chapters" @locate="onLocate" />

        <NovelEpisodePanel v-else-if="activeTab === 'episodes'" :novel-id="currentId" :chapters="chapters" />
      </section>
    </div>

    <!-- 导入弹窗 -->
    <div v-if="showImport" class="modal-mask" @click.self="showImport = false">
      <div class="modal small">
        <header class="modal-head">
          <h2>导入小说</h2>
          <button class="icon-btn" @click="showImport = false">✕</button>
        </header>
        <div class="modal-body">
          <div class="field">
            <label>小说文件（.txt / .md，≤5MB）</label>
            <input type="file" accept=".txt,.md" @change="onFile" />
          </div>
          <div class="field">
            <label>标题（可选，默认取文件名）</label>
            <input type="text" v-model="importTitle" placeholder="如：少年剑行" />
          </div>
          <div class="field">
            <label>作者（可选）</label>
            <input type="text" v-model="importAuthor" placeholder="如：佚名" />
          </div>
          <div v-if="importError" class="form-msg">{{ importError }}</div>
          <div class="dim" style="font-size: 12px">
            编码自动识别（UTF-8 / GBK / BIG5）；内容重复导入会被阻止；原文与章节位置完整保留。
          </div>
        </div>
        <footer class="modal-foot">
          <button class="btn" style="margin-left: auto" @click="showImport = false">取消</button>
          <button class="btn btn-primary" :disabled="!file || importing" @click="doImport">
            {{ importing ? '导入中…' : '导入并识别章节' }}
          </button>
        </footer>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import NovelBiblePanel from './NovelBiblePanel.vue'
import NovelEpisodePanel from './NovelEpisodePanel.vue'

const novels = ref([])
const loading = ref(true)
const currentId = ref('')
const activeTab = ref('chapters')
const chapters = ref([])
const openId = ref('')
const splitId = ref('')
const splitOffset = ref(0)
const editingId = ref('')
const editingTitle = ref('')
const contents = ref({})
const reparsing = ref(false)

const showImport = ref(false)
const file = ref(null)
const importTitle = ref('')
const importAuthor = ref('')
const importing = ref(false)
const importError = ref('')

const current = computed(() => novels.value.find((n) => n.id === currentId.value) || null)

onMounted(load)

async function load() {
  loading.value = true
  try {
    novels.value = await api.novels()
    if (novels.value.length && !currentId.value) currentId.value = novels.value[0].id
    if (currentId.value) await loadChapters()
  } finally {
    loading.value = false
  }
}

async function select(id) {
  currentId.value = id
  activeTab.value = 'chapters'
  openId.value = ''
  await loadChapters()
}

async function loadChapters() {
  if (!currentId.value) return
  chapters.value = await api.novelChapters(currentId.value)
  contents.value = {}
}

function contentOf(cid) {
  return contents.value[cid] || ''
}

async function toggle(cid) {
  if (openId.value === cid) {
    openId.value = ''
    splitId.value = ''
    return
  }
  openId.value = cid
  splitId.value = ''
  if (!contents.value[cid]) {
    const ch = await api.novelChapter(currentId.value, cid)
    contents.value = { ...contents.value, [cid]: ch.content }
  }
}

function startRename(c) {
  editingId.value = c.id
  editingTitle.value = c.title
  openId.value = c.id
}

async function saveRename(c) {
  const title = editingTitle.value.trim()
  editingId.value = ''
  if (!title || title === c.title) return
  await api.updateNovelChapter(currentId.value, c.id, { title })
  await loadChapters()
}

function toggleSplit(c) {
  if (splitId.value === c.id) {
    splitId.value = ''
    return
  }
  splitId.value = c.id
  splitOffset.value = Math.max(1, Math.floor(c.char_count / 2))
  openId.value = c.id
}

async function doSplit(c) {
  try {
    await api.splitNovelChapter(currentId.value, c.id, splitOffset.value)
    splitId.value = ''
    await loadChapters()
  } catch (e) {
    alert(e.message)
  }
}

async function mergeNext(c) {
  const next = chapters.value.find((x) => x.seq === c.seq + 1)
  if (!next) return
  if (!confirm(`将「${c.title}」与下一章「${next.title}」合并（标题取前者）？`)) return
  try {
    await api.mergeNovelChapters(currentId.value, c.id, next.id)
    await loadChapters()
  } catch (e) {
    alert(e.message)
  }
}

async function reparse() {
  if (!confirm('按当前全文重新识别章节？既有章节划分（含手动修改）会被替换，原文不受影响。')) return
  reparsing.value = true
  try {
    chapters.value = await api.reparseNovel(currentId.value)
    contents.value = {}
    openId.value = ''
    await load()
  } finally {
    reparsing.value = false
  }
}

function onFile(e) {
  file.value = e.target.files[0] || null
  importError.value = ''
}

async function doImport() {
  importing.value = true
  importError.value = ''
  try {
    const n = await api.importNovel(file.value, {
      title: importTitle.value, author: importAuthor.value,
    })
    showImport.value = false
    file.value = null
    importTitle.value = ''
    importAuthor.value = ''
    await load()
    currentId.value = n.id
    await loadChapters()
  } catch (e) {
    importError.value = e.message
  } finally {
    importing.value = false
  }
}

async function remove(n) {
  if (!confirm(`删除《${n.title}》？章节数据一并删除，不影响任何项目与成片。`)) return
  await api.deleteNovel(n.id)
  if (currentId.value === n.id) {
    currentId.value = ''
    chapters.value = []
  }
  await load()
}

function fmtCount(n) {
  return (n || 0).toLocaleString('zh-CN')
}

// 故事圣经实体章节定位：切回章节页签并展开对应章节原文
async function onLocate(chapterId) {
  activeTab.value = 'chapters'
  await toggle(chapterId)
}

function fmtTime(iso) {
  return (iso || '').slice(5, 16).replace('T', ' ')
}
</script>

<style scoped>
.tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 14px;
  border-bottom: 1px solid var(--border, #2a3345);
}
.tab {
  padding: 8px 16px;
  font-size: 13px;
  color: var(--text-dim, #9aa5b8);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
}
.tab:hover { color: var(--text, #e5e9f0); }
.tab.active {
  color: var(--accent, #8b5cf6);
  border-bottom-color: var(--accent, #8b5cf6);
}
.stage-hint {
  padding: 12px 16px;
  margin-bottom: 16px;
  font-size: 13px;
  line-height: 1.7;
  background: var(--bg-card, #1a1f2b);
  border: 1px solid var(--border, #2a3345);
  border-radius: 10px;
}
.stage-hint .dim {
  display: block;
  margin-top: 4px;
}
.novel-layout {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 16px;
  align-items: start;
}
.novel-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.novel-card {
  padding: 12px 14px;
  background: var(--bg-card, #1a1f2b);
  border: 1px solid var(--border, #2a3345);
  border-radius: 10px;
  cursor: pointer;
}
.novel-card.active {
  border-color: var(--accent, #8b5cf6);
}
.novel-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.novel-name {
  font-weight: 500;
}
.novel-meta {
  display: flex;
  gap: 12px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-dim, #9aa5b8);
}
.novel-sub {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-dim, #6b7688);
}
.chapter-panel {
  padding: 16px;
  background: var(--bg-card, #1a1f2b);
  border: 1px solid var(--border, #2a3345);
  border-radius: 12px;
}
.chapter-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}
.chapter-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.chapter-row {
  border: 1px solid var(--border, #2a3345);
  border-radius: 8px;
}
.chapter-main,
.chapter-ops {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
}
.chapter-ops {
  padding-top: 0;
}
.seq {
  min-width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--text-dim, #9aa5b8);
  background: var(--bg-secondary, #222a3a);
  border-radius: 6px;
}
.chapter-title {
  cursor: pointer;
}
.chapter-title:hover {
  color: var(--accent, #8b5cf6);
}
.ch-meta {
  font-size: 11px;
}
.rename-input {
  flex: 1;
  max-width: 320px;
}
.chapter-detail {
  padding: 0 12px 12px;
}
.chapter-content {
  max-height: 220px;
  overflow: auto;
  margin: 0;
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.8;
  white-space: pre-wrap;
  background: var(--bg-secondary, #222a3a);
  border-radius: 8px;
}
.split-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  font-size: 12px;
}
.num {
  width: 72px;
}
.btn-sm {
  padding: 4px 10px;
  font-size: 12px;
}
</style>
