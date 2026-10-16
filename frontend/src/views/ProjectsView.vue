<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>分镜工作台</h1>
        <div class="page-sub">剧本 → 场景分镜 → 镜头生成 → 时间线拼接，一条龙做完一支片</div>
      </div>
      <button class="btn btn-primary" @click="showCreate = true">＋ 新建项目</button>
    </header>

    <div v-if="store.loading" class="empty">加载中…</div>
    <div v-else-if="!store.projects.length" class="empty">
      还没有项目。点右上角「新建项目」，从一段剧本开始。
    </div>

    <div v-else class="proj-grid">
      <div v-for="p in store.projects" :key="p.id" class="proj-card" @click="open(p.id)">
        <div class="proj-card-head">
          <span class="proj-name">{{ p.name }}</span>
          <span class="badge" :class="p.status === 'active' ? 'done' : 'cancelled'">
            {{ p.status === 'active' ? '进行中' : '草稿' }}
          </span>
        </div>
        <div class="proj-desc">{{ p.description || '暂无简介' }}</div>
        <div class="proj-stats">
          <span>🎬 {{ p.scene_count }} 场景</span>
          <span>📹 {{ p.shot_count }} 分镜</span>
          <span>✔ {{ p.approved_count }} 已通过</span>
        </div>
        <div class="proj-foot">
          <span class="proj-time">更新于 {{ fmtTime(p.updated_at) }}</span>
          <button class="icon-btn danger" title="删除项目" @click.stop="remove(p)">🗑</button>
        </div>
      </div>
    </div>

    <!-- 新建项目弹窗 -->
    <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
      <div class="modal small">
        <header class="modal-head">
          <h2>新建项目</h2>
          <button class="icon-btn" @click="showCreate = false">✕</button>
        </header>
        <div class="modal-body">
          <div class="field">
            <label>项目名称</label>
            <input type="text" v-model="newName" placeholder="如：雨夜追逐 · 第一集" />
          </div>
          <div class="field">
            <label>简介（可选）</label>
            <textarea v-model="newDesc" rows="3" placeholder="这个项目讲什么…"></textarea>
          </div>
        </div>
        <footer class="modal-foot">
          <button class="btn" style="margin-left: auto" @click="showCreate = false">取消</button>
          <button class="btn btn-primary" :disabled="!newName.trim()" @click="create">创建</button>
        </footer>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '../store/project'

const store = useProjectStore()
const router = useRouter()

const showCreate = ref(false)
const newName = ref('')
const newDesc = ref('')

onMounted(() => store.fetchProjects())

function fmtTime(iso) {
  return (iso || '').slice(5, 16).replace('T', ' ')
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
  if (!confirm(`确定删除项目「${p.name}」？其中的场景、分镜与版本记录都会删除。`)) return
  await store.removeProject(p.id)
}
</script>

<style scoped>
.proj-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px;
}
.proj-card {
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 18px; cursor: pointer; display: flex; flex-direction: column; gap: 10px;
  transition: border-color 0.15s, transform 0.15s;
}
.proj-card:hover { border-color: var(--accent); transform: translateY(-2px); }
.proj-card-head { display: flex; align-items: center; gap: 10px; }
.proj-name { font-size: 16px; font-weight: 700; }
.proj-desc {
  font-size: 13px; color: var(--text-dim); line-height: 1.6;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  min-height: 42px;
}
.proj-stats { display: flex; gap: 14px; font-size: 12.5px; color: var(--text-dim); }
.proj-foot {
  display: flex; align-items: center; justify-content: space-between;
  border-top: 1px solid var(--border); padding-top: 10px;
}
.proj-time { font-size: 11.5px; color: var(--text-dim); }

.modal-mask {
  position: fixed; inset: 0; background: rgba(8, 10, 15, 0.75);
  display: flex; align-items: center; justify-content: center; z-index: 200; padding: 24px;
}
.modal {
  width: min(520px, 100%); background: var(--bg-panel);
  border: 1px solid var(--border); border-radius: 14px;
}
.modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 22px; border-bottom: 1px solid var(--border);
}
.modal-head h2 { margin: 0; font-size: 17px; }
.modal-body { padding: 20px 22px; }
.modal-foot { display: flex; gap: 10px; padding: 14px 22px; border-top: 1px solid var(--border); }
</style>
