<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>我的作品</h1>
        <div class="page-sub">
          每个作品是一支片：剧本 → 场景分镜 → 镜头生成 → 时间线拼接
        </div>
      </div>
      <div class="head-actions">
        <button class="btn btn-primary" @click="showCreate = true">
          <Icon name="plus" :size="15" />新建作品
        </button>
      </div>
    </header>

    <div v-if="store.loading" class="empty">加载中…</div>
    <div v-else-if="!store.projects.length" class="empty">
      还没有作品。点右上角「新建作品」，从一段剧本开始。
    </div>

    <div v-else class="proj-grid">
      <article v-for="p in store.projects" :key="p.id" class="proj-card" @click="open(p.id)">
        <header class="proj-head">
          <span class="proj-avatar" :style="avatarStyle(p.name)">{{ initial(p.name) }}</span>
          <div class="proj-id">
            <h2 class="proj-name" :title="p.name">{{ p.name }}</h2>
            <span class="proj-state" :class="p.status === 'active' ? 'on' : ''">
              {{ p.status === 'active' ? '进行中' : '草稿' }}
            </span>
          </div>
          <button class="icon-btn danger proj-del" title="删除作品" @click.stop="remove(p)">
            <Icon name="trash" :size="14" />
          </button>
        </header>

        <p class="proj-desc">{{ p.description || '暂无简介' }}</p>

        <div class="proj-stats">
          <span><b>{{ p.scene_count }}</b> 场景</span>
          <i></i>
          <span><b>{{ p.shot_count }}</b> 分镜</span>
          <i></i>
          <span><b>{{ p.approved_count }}</b> 已通过</span>
        </div>

        <footer class="proj-foot">
          <div class="proj-progress" :title="progressTip(p)">
            <div class="proj-progress-bar">
              <div class="proj-progress-inner" :style="{ width: progressPct(p) + '%' }"></div>
            </div>
            <span>{{ progressPct(p) }}%</span>
          </div>
          <time class="proj-time">{{ fmtTime(p.updated_at) }}</time>
        </footer>
      </article>

      <button class="proj-new" @click="showCreate = true">
        <Icon name="plus" :size="18" />
        <span>新建作品</span>
      </button>
    </div>

    <!-- 新建作品弹窗 -->
    <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
      <div class="modal small">
        <header class="modal-head">
          <h2>新建作品</h2>
          <button class="icon-btn" @click="showCreate = false"><Icon name="close" :size="15" /></button>
        </header>
        <div class="modal-body">
          <div class="field">
            <label>作品名称</label>
            <input type="text" v-model="newName" placeholder="如：雨夜追缉 · 第一集" />
          </div>
          <div class="field">
            <label>简介（可选）</label>
            <textarea v-model="newDesc" rows="3" placeholder="这个作品讲什么…"></textarea>
          </div>
        </div>
        <footer class="modal-foot">
          <button class="btn" @click="showCreate = false">取消</button>
          <button class="btn btn-primary" :disabled="!newName.trim()" @click="create">创建</button>
        </footer>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Icon from '../components/Icon.vue'
import { useProjectStore } from '../store/project'

const store = useProjectStore()
const router = useRouter()

const showCreate = ref(false)
const newName = ref('')
const newDesc = ref('')

onMounted(() => store.fetchProjects())

function fmtTime(iso) {
  if (!iso) return ''
  const s = iso.slice(5, 16).replace('T', ' ')
  return s
}

function initial(name) {
  return (name || '?').trim().slice(0, 1).toUpperCase()
}

function avatarStyle(name) {
  let h = 0
  for (const ch of name || '') h = (h * 31 + ch.codePointAt(0)) % 360
  // 低饱和染底 + 同色描边，跟深色底融得进去又不糊
  return {
    color: `hsl(${h}, 62%, 76%)`,
    background: `hsla(${h}, 62%, 52%, .14)`,
    boxShadow: `inset 0 0 0 1px hsla(${h}, 62%, 60%, .28)`,
  }
}

function progressPct(p) {
  const total = Number(p.shot_count) || 0
  const done = Number(p.approved_count) || 0
  if (!total) return 0
  return Math.min(100, Math.round((done / total) * 100))
}

function progressTip(p) {
  return `${p.approved_count} / ${p.shot_count} 个分镜已通过`
}

async function create() {
  const p = await store.createProject(newName.value.trim(), newDesc.value.trim())
  showCreate.value = false
  newName.value = ''
  newDesc.value = ''
  router.push(`/projects/${p.id}`)
}

function open(id) {
  router.push(`/projects/${id}`)
}

async function remove(p) {
  if (!confirm(`确定删除作品「${p.name}」？其中的场景、分镜与版本记录都会删除。`)) return
  await store.removeProject(p.id)
}
</script>

<style scoped>
.proj-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--sp-4);
}

.proj-card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 16px 16px 12px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: border-color .16s var(--ease), transform .16s var(--ease), box-shadow .16s var(--ease);
}
.proj-card:hover {
  border-color: rgba(var(--accent-rgb), .5);
  transform: translateY(-2px);
  box-shadow: var(--shadow-2);
}

.proj-head { display: flex; align-items: center; gap: 10px; }
.proj-avatar {
  width: 34px; height: 34px; border-radius: 9px; flex: none;
  display: grid; place-items: center;
  font-size: 15px; font-weight: 700;
}
.proj-id { min-width: 0; flex: 1; display: flex; align-items: center; gap: 8px; }
.proj-name {
  margin: 0;
  font-size: var(--fs-md);
  font-weight: 650;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  min-width: 0;
}
.proj-state {
  flex: none;
  font-size: var(--fs-xs);
  color: var(--text-dim);
  background: var(--bg-card);
  border-radius: 999px;
  padding: 1px 8px;
}
.proj-state.on { color: var(--accent-soft); background: rgba(var(--accent-rgb), .16); }
.proj-del { opacity: 0; transition: opacity .16s var(--ease); }
.proj-card:hover .proj-del { opacity: 1; }

.proj-desc {
  margin: 0;
  font-size: var(--fs-sm);
  color: var(--text-dim);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 40px;
}

.proj-stats {
  display: flex; align-items: center; gap: 10px;
  font-size: var(--fs-sm); color: var(--text-dim);
}
.proj-stats b { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; }
.proj-stats i { width: 1px; height: 10px; background: var(--border); }

.proj-foot {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  margin-top: 2px; padding-top: 10px;
  border-top: 1px solid var(--border);
}
.proj-progress { display: flex; align-items: center; gap: 8px; flex: 1; }
.proj-progress-bar {
  flex: 1; height: 4px; border-radius: 2px;
  background: var(--bg-card); overflow: hidden; max-width: 120px;
}
.proj-progress-inner {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  border-radius: 2px;
  transition: width .3s var(--ease);
}
.proj-progress span { font-size: var(--fs-xs); color: var(--text-dim); font-variant-numeric: tabular-nums; }
.proj-time { font-size: var(--fs-xs); color: #5d6f8c; white-space: nowrap; }

.proj-new {
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text-dim);
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; min-height: 150px; cursor: pointer;
  font-size: var(--fs-base); font-family: inherit;
  transition: border-color .16s var(--ease), color .16s var(--ease), background .16s var(--ease);
}
.proj-new:hover {
  border-color: var(--accent); color: var(--text);
  background: rgba(var(--accent-rgb), .06);
}

/* 弹窗 */
.modal-mask {
  position: fixed; inset: 0; background: rgba(6, 10, 18, .74);
  backdrop-filter: blur(3px);
  display: flex; align-items: center; justify-content: center; z-index: 200; padding: 24px;
}
.modal {
  width: min(480px, 100%);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}
.modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 18px; border-bottom: 1px solid var(--border);
}
.modal-head h2 { margin: 0; font-size: var(--fs-lg); font-weight: 650; }
.modal-body { padding: 18px; }
.modal-foot {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: 12px 18px; border-top: 1px solid var(--border);
}
</style>
