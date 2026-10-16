<template>
  <div class="modal-mask" @click.self="$emit('close')">
    <div class="modal">
      <header class="modal-head">
        <h2>{{ isNew ? '新建角色卡' : `角色卡 · ${form.name || '未命名'}` }}</h2>
        <button class="icon-btn" @click="$emit('close')">✕</button>
      </header>

      <div class="modal-body">
        <section class="form-section">
          <h3>基本信息</h3>
          <div class="param-grid">
            <div class="field">
              <label>角色名</label>
              <input type="text" v-model="form.name" placeholder="如：林越" />
            </div>
            <div class="field">
              <label>戏份</label>
              <select v-model="form.role">
                <option v-for="r in ROLES" :key="r" :value="r">{{ r }}</option>
              </select>
            </div>
          </div>
          <div class="field">
            <label>外貌描述（生成时自动拼入提示词，保证跨镜头一致）</label>
            <textarea v-model="form.appearance" rows="2"
                      placeholder="young man, black hair, sharp eyes, 180cm"></textarea>
          </div>
          <div class="field">
            <label>服装描述</label>
            <input type="text" v-model="form.outfit" placeholder="dark blue jacket, black boots" />
          </div>
          <div class="field">
            <label>关联 LoRA（可选，文件名或触发词）</label>
            <input type="text" v-model="form.lora" placeholder="如：linyue_v1.safetensors" />
          </div>
          <div class="field">
            <label>备注</label>
            <input type="text" v-model="form.notes" placeholder="角色小传/注意事项" />
          </div>
        </section>

        <section class="form-section">
          <h3>固定音色（TTS 配音绑定）</h3>
          <div class="field">
            <label>音色</label>
            <select v-model="form.voice_id" :disabled="!voices.length">
              <option value="">未绑定（用默认音色）</option>
              <option v-for="v in voices" :key="v.id" :value="v.id">
                {{ v.name }}{{ v.engine === 'pyttsx3' ? ' · 离线' : '' }}
              </option>
            </select>
          </div>
          <p v-if="!voices.length" class="hint-text">音色目录加载中或 TTS 不可用…</p>
        </section>

        <section class="form-section">
          <h3>参考图（正 / 侧 / 背 / 表情）</h3>
          <div class="ref-grid">
            <div v-for="(ref, i) in form.reference_images" :key="i" class="ref-item">
              <img :src="ref.url" alt="" />
              <div class="ref-meta">
                <select v-model="ref.type">
                  <option v-for="t in REF_TYPES" :key="t" :value="t">{{ REF_LABELS[t] }}</option>
                </select>
                <button class="icon-btn danger" @click="form.reference_images.splice(i, 1)">✕</button>
              </div>
            </div>
            <label class="ref-add">
              <input type="file" accept="image/*" hidden @change="onRefUpload" />
              <span>＋ 上传参考图</span>
            </label>
            <button class="ref-add" type="button" :disabled="genBusy" @click="generateRefImage">
              <span>{{ genBusy ? '生成中…' : '🖼 AI 生成参考图' }}</span>
            </button>
          </div>
          <p v-if="genMsg" class="form-msg" :class="{ error: genMsgIsError }">{{ genMsg }}</p>
          <p class="hint-text">
            参考图用于图生视频/首尾帧模式；同时提示词一致性检查会参考有无图。
            AI 生成需在「🧩 模型服务」页配置图像生成服务。
          </p>
        </section>

        <section class="form-section">
          <h3>表情预设</h3>
          <div class="expr-list">
            <div v-for="(ex, i) in form.expressions" :key="i" class="expr-row">
              <input type="text" v-model="ex.name" placeholder="表情名，如：冷笑" />
              <input type="text" v-model="ex.prompt" placeholder="提示词，如：cold smile" />
              <button class="icon-btn danger" @click="form.expressions.splice(i, 1)">✕</button>
            </div>
          </div>
          <button class="btn" @click="form.expressions.push({ name: '', prompt: '' })">＋ 添加表情</button>
        </section>
      </div>

      <footer class="modal-foot">
        <span v-if="errMsg" class="form-msg">{{ errMsg }}</span>
        <div style="margin-left: auto; display: flex; gap: 10px">
          <button class="btn" @click="$emit('close')">取消</button>
          <button class="btn btn-primary" :disabled="saving || !form.name.trim()" @click="save">
            {{ saving ? '保存中…' : '💾 保存角色卡' }}
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const props = defineProps({
  projectId: { type: String, required: true },
  character: { type: Object, default: null },  // null = 新建
})
const emit = defineEmits(['close', 'saved'])

const ROLES = ['主角', '配角', '群演', '旁白', '其他']
const REF_TYPES = ['front', 'side', 'back', 'expression', 'other']
const REF_LABELS = { front: '正面', side: '侧面', back: '背面', expression: '表情', other: '其他' }

const isNew = ref(!props.character)
const saving = ref(false)
const errMsg = ref('')
const voices = ref([])

const form = reactive({
  name: props.character?.name || '',
  role: props.character?.role || '配角',
  appearance: props.character?.appearance || '',
  outfit: props.character?.outfit || '',
  voice_id: props.character?.voice_id || '',
  lora: props.character?.lora || '',
  notes: props.character?.notes || '',
  reference_images: props.character?.reference_images
    ? JSON.parse(JSON.stringify(props.character.reference_images)) : [],
  expressions: props.character?.expressions
    ? JSON.parse(JSON.stringify(props.character.expressions)) : [],
})

onMounted(async () => {
  try {
    const r = await api.ttsVoices()
    voices.value = r.voices || []
  } catch { /* 音色加载失败不阻塞编辑 */ }
})

async function onRefUpload(e) {
  const file = e.target.files?.[0]
  if (!file) return
  try {
    const r = await api.uploadFile(file)
    form.reference_images.push({ url: r.url, type: 'front', label: file.name })
  } catch (err) {
    errMsg.value = err.message
  }
  e.target.value = ''
}

// ---------- AI 生成参考图 ----------
const genBusy = ref(false)
const genMsg = ref('')
const genMsgIsError = ref(false)

async function generateRefImage() {
  genBusy.value = true
  genMsg.value = ''
  genMsgIsError.value = false
  try {
    const prompt = [
      form.appearance || form.name,
      form.outfit ? `wearing ${form.outfit}` : '',
      'character reference sheet, front view, full body, neutral background, concept art',
    ].filter(Boolean).join(', ')
    const r = await api.imagegenGenerate({ prompt, size: '768x1024', n: 1 })
    for (const url of r.urls) {
      form.reference_images.push({ url, type: 'front', label: 'AI 生成' })
    }
    genMsg.value = '已生成并加入参考图列表'
  } catch (e) {
    genMsgIsError.value = true
    genMsg.value = e.message.includes('未配置')
      ? '图像生成未配置：请到「🧩 模型服务」页配置后使用'
      : e.message
  } finally {
    genBusy.value = false
  }
}

async function save() {
  saving.value = true
  errMsg.value = ''
  try {
    if (isNew.value) {
      await api.createCharacter(props.projectId, form)
    } else {
      await api.updateCharacter(props.character.id, form)
    }
    emit('saved')
  } catch (e) {
    errMsg.value = e.message
  } finally {
    saving.value = false
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
  width: min(720px, 100%); max-height: 92vh; display: flex; flex-direction: column;
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: 14px;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.55);
}
.modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 22px; border-bottom: 1px solid var(--border);
}
.modal-head h2 { margin: 0; font-size: 17px; }
.modal-body { overflow-y: auto; padding: 20px 22px; }
.modal-foot {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 22px; border-top: 1px solid var(--border);
}
.form-section { margin-bottom: 26px; }
.form-section h3 {
  font-size: 14px; margin: 0 0 14px; color: var(--text);
  padding-left: 10px; border-left: 3px solid var(--accent);
}
.param-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12.5px; color: var(--text-dim); margin-bottom: 5px; }
.field input, .field select, .field textarea {
  width: 100%; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 8px; color: var(--text); padding: 8px 10px; font-size: 13px;
  font-family: inherit;
}
.field input:focus, .field select:focus, .field textarea:focus {
  border-color: var(--accent); outline: none;
}
.icon-btn {
  background: transparent; border: none; color: var(--text-dim);
  cursor: pointer; padding: 4px 7px; border-radius: 6px; font-size: 13px;
}
.icon-btn:hover:not(:disabled) { background: var(--bg-hover); color: var(--text); }
.icon-btn.danger:hover { color: var(--red); }
.btn {
  background: var(--bg-card); border: 1px solid var(--border); color: var(--text);
  border-radius: 8px; padding: 8px 16px; font-size: 13px; cursor: pointer;
  font-family: inherit;
}
.btn:hover:not(:disabled) { border-color: var(--accent); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary {
  background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600;
}
.hint-text { font-size: 12px; color: var(--text-dim); margin: 6px 0 0; }
.ref-grid { display: flex; flex-wrap: wrap; gap: 10px; }
.ref-item {
  width: 120px; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 8px; overflow: hidden;
}
.ref-item img { width: 100%; height: 90px; object-fit: cover; display: block; }
.ref-meta { display: flex; align-items: center; gap: 4px; padding: 4px 6px; }
.ref-meta select {
  flex: 1; min-width: 0; background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: 5px; color: var(--text); font-size: 11px; padding: 2px 4px;
}
.ref-add {
  width: 120px; height: 118px; border: 1px dashed var(--border); border-radius: 8px;
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  color: var(--text-dim); font-size: 12.5px;
}
.ref-add:hover { border-color: var(--accent); color: var(--accent); }
.expr-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; }
.expr-row { display: flex; gap: 8px; }
.expr-row input {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px;
  color: var(--text); padding: 7px 10px; font-size: 13px; font-family: inherit;
}
.expr-row input:first-child { width: 120px; }
.expr-row input:last-of-type { flex: 1; }
.form-msg { color: var(--red); font-size: 12.5px; }
</style>
