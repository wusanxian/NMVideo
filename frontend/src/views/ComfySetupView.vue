<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>ComfyUI 设置</h1>
        <div class="page-sub">
          分镜生成可选择「ComfyUI 工作流」引擎：镜头参数按模板节点映射注入，
          提交后通过 WebSocket 实时推送进度。服务器地址可在「模型服务」页修改
          （settings.json 持久化，环境变量 <code>AIVIDEO_COMFYUI_URL</code> 可兜底）。
        </div>
      </div>
      <router-link to="/projects" class="btn">← 返回工作台</router-link>
    </header>

    <!-- 连接状态 -->
    <div class="panel status-panel">
      <div class="status-row">
        <span class="dot" :class="status.online ? 'ready' : 'unavailable'"></span>
        <b>{{ status.online ? '已连接' : '未连接' }}</b>
        <span class="status-url">{{ status.url }}</span>
        <button class="btn" style="margin-left: auto" @click="checkStatus">🔄 重新检测</button>
      </div>
      <p class="hint">
        未连接时：请先在本机启动 ComfyUI（默认 127.0.0.1:8188），
        或到「🧩 模型服务」页把地址改成你的 ComfyUI 服务器。
      </p>
    </div>

    <!-- 工作流模板 -->
    <div class="panel">
      <div class="wf-head">
        <h3 style="margin: 0">工作流模板</h3>
        <div style="display: flex; gap: 8px">
          <select v-model="selectedPreset" class="preset-select" @change="applyPreset">
            <option value="">📦 内置预设…</option>
            <option v-for="p in presets" :key="p.id" :value="p.id">
              {{ p.name }}<template v-if="p.requires"> · 需 {{ p.requires }}</template>
            </option>
          </select>
          <button class="btn" @click="resetTemplate">恢复默认</button>
          <button class="btn btn-primary" :disabled="saving" @click="saveWorkflow">💾 保存模板</button>
        </div>
      </div>
      <p v-if="presetDesc" class="hint">
        <b>{{ presetDesc.name }}</b>：{{ presetDesc.description }}
        <template v-if="presetDesc.requires"><br>依赖：{{ presetDesc.requires }}</template>
        <br>应用预设会覆盖当前模板，之后仍可手动编辑。
      </p>
      <p class="hint">
        在 ComfyUI 界面里搭好工作流 → 右上角「Save(API Format)」导出 JSON → 整段粘贴到下面 →
        按节点 ID 填好参数映射。生成时会把这些值写进对应节点的输入框：<br>
        <code>prompt</code> 画面提示词 · <code>negative_prompt</code> 负面提示词 ·
        <code>width / height / num_frames</code> 尺寸与帧数 ·
        <code>seed / steps / guidance</code> 种子 / 步数 / CFG。<br>
        产物节点兼容 SaveImage（PNG 序列）与 VHS_VideoCombine（直接出 MP4）等常见输出。
      </p>
      <textarea v-model="workflowText" class="wf-area" spellcheck="false" rows="18"></textarea>
      <p v-if="wfMsg" class="form-msg">{{ wfMsg }}</p>

      <h4>参数映射（节点 ID → 输入字段）</h4>
      <div class="map-grid">
        <div v-for="(v, k) in editableMap" :key="k" class="map-row">
          <code>{{ k }}</code>
          <span>→</span>
          <input type="text" v-model="v.node" placeholder="节点 ID" />
          <input type="text" v-model="v.input" placeholder="输入字段名" />
        </div>
      </div>
    </div>

    <!-- 队列 -->
    <div class="panel" v-if="status.online">
      <h3>ComfyUI 队列</h3>
      <p v-if="status.queue" class="hint">
        运行中 {{ status.queue.running }} 个 · 排队 {{ status.queue.pending }} 个
      </p>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const status = ref({ online: false, url: '', queue: null })
const workflowText = ref('')
const editableMap = reactive({})
const saving = ref(false)
const wfMsg = ref('')
const presets = ref([])
const selectedPreset = ref('')

const presetDesc = computed(() => presets.value.find((p) => p.id === selectedPreset.value) || null)

async function loadPresets() {
  try {
    const r = await api.comfyPresets()
    presets.value = r.presets || []
  } catch {
    presets.value = []
  }
}

async function applyPreset() {
  if (!selectedPreset.value) return
  if (!confirm('应用预设将覆盖当前工作流模板，确定？')) {
    selectedPreset.value = ''
    return
  }
  try {
    const wf = await api.applyComfyPreset(selectedPreset.value)
    renderWorkflow(wf)
    wfMsg.value = '已应用预设'
  } catch (e) {
    wfMsg.value = e.message
  }
  selectedPreset.value = ''
}

async function checkStatus() {
  try {
    status.value = await api.comfyStatus()
  } catch {
    status.value = { online: false, url: '', queue: null }
  }
}

async function loadWorkflow() {
  try {
    const wf = await api.comfyWorkflow()
    renderWorkflow(wf)
  } catch (e) {
    wfMsg.value = e.message
  }
}

function renderWorkflow(wf) {
  const map = wf._aivideo_map || {}
  workflowText.value = JSON.stringify(wf.prompt || {}, null, 2)
  Object.keys(editableMap).forEach((k) => delete editableMap[k])
  for (const [k, v] of Object.entries(map)) {
    editableMap[k] = { node: String(v.node || ''), input: String(v.input || '') }
  }
}

async function saveWorkflow() {
  saving.value = true
  wfMsg.value = ''
  try {
    const prompt = JSON.parse(workflowText.value)
    const map = {}
    for (const [k, v] of Object.entries(editableMap)) {
      if (v.node && v.input) map[k] = { node: v.node, input: v.input }
    }
    const saved = await api.saveComfyWorkflow({ prompt, _aivideo_map: map })
    renderWorkflow(saved)
    wfMsg.value = '已保存'
  } catch (e) {
    wfMsg.value = e.message.includes('JSON') ? '工作流 JSON 格式错误：' + e.message : e.message
  } finally {
    saving.value = false
  }
}

function resetTemplate() {
  if (!confirm('恢复默认模板？当前自定义工作流会被覆盖。')) return
  // 删掉自定义模板文件后重新加载（后端在文件缺失时返回内置默认模板）
  fetch('/api/comfy/workflow', { method: 'DELETE' }).then(loadWorkflow).catch(loadWorkflow)
}

onMounted(async () => {
  await checkStatus()
  await loadWorkflow()
  await loadPresets()
})
</script>

<style scoped>
.status-panel { padding: 16px 20px; }
.status-row { display: flex; align-items: center; gap: 10px; font-size: 14px; }
.status-url { font-size: 12.5px; color: var(--text-dim); font-family: ui-monospace, monospace; }
.hint {
  font-size: 12.5px; color: var(--text-dim); line-height: 1.9;
  background: var(--bg-card); border-radius: 8px; padding: 12px 14px; margin: 12px 0;
}
code {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 5px;
  padding: 1px 6px; font-size: 12px; font-family: ui-monospace, monospace;
}
.wf-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.wf-area {
  width: 100%; font-family: ui-monospace, monospace; font-size: 12.5px; line-height: 1.6;
  background: var(--bg-card);
}
h4 { margin: 18px 0 10px; font-size: 13px; }
.preset-select {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px;
  color: var(--text); padding: 6px 10px; font-size: 12.5px; max-width: 260px;
}
.map-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 8px; }
.map-row { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.map-row code { min-width: 110px; text-align: center; }
.map-row input {
  width: 70px; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 6px; color: var(--text); padding: 4px 8px; font-size: 12px;
}
</style>
