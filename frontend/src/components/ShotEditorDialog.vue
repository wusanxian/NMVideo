<template>
  <div class="modal-mask" @click.self="$emit('close')">
    <div class="modal">
      <header class="modal-head">
        <h2>分镜编辑</h2>
        <button class="icon-btn" @click="$emit('close')">✕</button>
      </header>

      <div class="modal-body">
        <!-- 基本信息 -->
        <section class="form-section">
          <h3>镜头信息</h3>
          <div class="field">
            <label>镜头标题</label>
            <input type="text" v-model="form.title" placeholder="如：主角推门进入密室" />
          </div>
          <div class="field">
            <label>人物对白（TTS 配音的文本来源）</label>
            <input type="text" v-model="form.dialogue" placeholder="如：主角：谁在那里？" />
          </div>
          <div class="param-grid">
            <div class="field">
              <label>景别</label>
              <select v-model="form.shot_size">
                <option v-for="s in SHOT_SIZES" :key="s" :value="s">{{ s }}</option>
              </select>
            </div>
            <div class="field">
              <label>镜头运动</label>
              <select v-model="form.camera_movement">
                <option v-for="m in CAMERA_MOVES" :key="m" :value="m">{{ m }}</option>
              </select>
            </div>
            <div class="field">
              <label>时长（秒）</label>
              <input type="number" v-model.number="form.duration_s" min="0.5" max="60" step="0.5" />
            </div>
          </div>
        </section>

        <!-- 角色绑定 -->
        <section class="form-section">
          <h3>关联角色（生成时自动拼入外貌/服装描述）</h3>
          <div v-if="characters.length" class="char-chips">
            <span v-for="c in characters" :key="c.id" class="char-chip"
                  :class="{ on: form.character_ids.includes(c.id) }"
                  @click="toggleCharacter(c.id)">
              <i class="char-dot" :style="{ background: charColor(c.name) }"></i>
              {{ c.name }}
              <em v-if="c.voice_name">🎙 {{ c.voice_name }}</em>
            </span>
          </div>
          <p v-else class="hint-text">还没有角色卡。去项目里「🎭 资产库」标签页创建，跨镜头人物外观才能保持一致。</p>
        </section>

        <!-- 提示词 -->
        <section class="form-section">
          <h3>提示词</h3>
          <div class="field">
            <label>
              画面提示词 Prompt
              <button class="btn mini" :disabled="polishing || !form.prompt.trim()"
                      style="margin-left: 8px" @click="polishPrompt">
                {{ polishing ? '优化中…' : '✨ AI 优化' }}
              </button>
            </label>
            <textarea v-model="form.prompt" rows="3"
                      placeholder="描述这个镜头要出现的画面，如：young swordsman pushing open a wooden door, candlelight flickering, secret chamber"></textarea>
            <span v-if="polishMsg" class="form-msg" :class="{ error: polishMsgIsError }">{{ polishMsg }}</span>
          </div>
          <div class="field">
            <label>负面提示词</label>
            <input type="text" v-model="form.negative_prompt" placeholder="worst quality, blurry, watermark" />
          </div>
        </section>

        <!-- 生成模式 -->
        <section class="form-section">
          <h3>生成模式</h3>
          <div class="mode-cards">
            <div v-for="m in MODES" :key="m.id" class="mode-card"
                 :class="{ active: form.mode === m.id }" @click="form.mode = m.id">
              <div class="mc-name">{{ m.name }}</div>
              <div class="mc-desc">{{ m.desc }}</div>
            </div>
          </div>

          <!-- 图生视频 / 首尾帧：参考图 -->
          <div v-if="form.mode === 'i2v' || form.mode === 'first_last'" class="field" style="margin-top: 12px">
            <label>参考图 / 首帧（本地引擎将仅记录，实际图生视频走 ComfyUI 工作流）</label>
            <div class="ref-input-row">
              <input type="text" v-model="form.reference_image" placeholder="/outputs/xxx.png 或 http://..." />
              <label class="btn upload-btn">
                上传
                <input type="file" accept="image/*" hidden @change="onRefUpload" />
              </label>
            </div>
          </div>

          <!-- 视频续接：来源镜头 -->
          <div v-if="form.mode === 'continuation'" class="field" style="margin-top: 12px">
            <label>续接来源镜头（默认上一镜头；自动抽取其末帧作为首帧）</label>
            <select v-model="form.continue_from">
              <option value="">上一镜头（自动）</option>
              <option v-for="s in siblingShots" :key="s.id" :value="s.id">
                #{{ s.order_index + 1 }} {{ s.title || '未命名' }}
              </option>
            </select>
            <p class="hint-text">
              续接会把来源镜头的<b>末帧</b>作为本镜头首帧参考注入工作流；
              本地引擎（AnimeDiff）会退化为「同场景衔接提示词」，完整续接请用 ComfyUI。
            </p>
          </div>
        </section>

        <!-- TTS 配音 -->
        <section class="form-section">
          <h3>
            🎙 TTS 配音
            <span v-if="voicesAvail" class="tts-state on">可用</span>
            <span v-else class="tts-state off">不可用</span>
          </h3>
          <div class="tts-panel">
            <div class="tts-row">
              <select v-model="ttsVoice" :disabled="!voicesAvail">
                <option value="">选择音色…</option>
                <optgroup v-for="g in voiceGroups" :key="g.label" :label="g.label">
                  <option v-for="v in g.items" :key="v.id" :value="v.id">{{ v.name }}</option>
                </optgroup>
              </select>
              <select v-model="ttsRate" :disabled="!voicesAvail">
                <option v-for="r in RATES" :key="r.v" :value="r.v">{{ r.label }}</option>
              </select>
              <button class="btn btn-primary" :disabled="!voicesAvail || !ttsText.trim() || ttsBusy"
                      @click="generateVoice">
                {{ ttsBusy ? '生成中…' : '🎙 生成配音' }}
              </button>
            </div>
            <div class="field" style="margin-top: 8px">
              <label>配音文本（默认取对白）</label>
              <textarea v-model="ttsText" rows="2"></textarea>
            </div>
            <div v-if="shot.voices && shot.voices.length" class="voice-list">
              <div v-for="v in shot.voices" :key="v.id" class="voice-item">
                <audio :src="v.audio_url" controls preload="metadata"></audio>
                <span class="v-meta">{{ v.character_name || v.voice }} · {{ v.duration_s.toFixed(1) }}s · {{ v.engine }}</span>
                <button class="icon-btn danger" @click="$emit('delete-voice', v.id)">✕</button>
              </div>
            </div>
          </div>
        </section>

        <!-- 生成参数 -->
        <section class="form-section">
          <h3>生成参数</h3>
          <div class="engine-cards">
            <div v-for="e in engines" :key="e.id" class="engine-card"
                 :class="{ active: genForm.engine === e.id, disabled: e.status !== 'ready' }"
                 @click="e.status === 'ready' && (genForm.engine = e.id)">
              <div class="ec-name"><span class="dot" :class="e.status"></span>{{ e.name }}</div>
              <div class="ec-desc">{{ e.description }}</div>
              <div v-if="e.note" class="ec-note">⚠ {{ e.note }}</div>
            </div>
          </div>

          <div v-if="genForm.engine === 'wan22'" class="chips" style="margin-top: 12px">
            <span v-for="q in presets" :key="q.id" class="chip"
                  :class="{ active: genForm.preset === q.id }" @click="applyPreset(q)">{{ q.name }}</span>
          </div>

          <div class="param-grid" style="margin-top: 12px">
            <div class="field">
              <div class="range-label"><span>帧数</span><b>{{ genForm.num_frames }}</b></div>
              <input type="range" min="8" :max="genForm.engine === 'wan22' ? 121 : 32" step="2"
                     v-model.number="genForm.num_frames" />
            </div>
            <div class="field">
              <div class="range-label"><span>帧率 FPS</span><b>{{ genForm.fps }}</b></div>
              <input type="range" min="4" :max="genForm.engine === 'wan22' ? 24 : 16" step="1"
                     v-model.number="genForm.fps" />
            </div>
            <div class="field">
              <div class="range-label"><span>步数</span><b>{{ genForm.steps }}</b></div>
              <input type="range" min="10" max="50" step="1" v-model.number="genForm.steps" />
            </div>
            <div class="field">
              <div class="range-label"><span>CFG</span><b>{{ genForm.guidance }}</b></div>
              <input type="range" min="1" :max="genForm.engine === 'wan22' ? 10 : 15" step="0.5"
                     v-model.number="genForm.guidance" />
            </div>
            <div class="field">
              <label>宽度</label>
              <input type="number" v-model.number="genForm.width" min="256" :max="genForm.engine === 'wan22' ? 1344 : 1024" step="16" />
            </div>
            <div class="field">
              <label>高度</label>
              <input type="number" v-model.number="genForm.height" min="256" :max="genForm.engine === 'wan22' ? 768 : 1024" step="16" />
            </div>
            <div class="field">
              <label>随机种子（-1 随机）</label>
              <input type="number" v-model.number="genForm.seed" min="-1" />
            </div>
          </div>
          <p v-if="genForm.engine === 'comfyui'" class="comfy-hint">
            将按 <b>ComfyUI 工作流模板</b> 的节点映射注入以上参数与参考图；
            模板可在「ComfyUI 设置」中粘贴你自己导出的工作流。
          </p>
        </section>

        <!-- 版本 -->
        <section v-if="shot.versions && shot.versions.length" class="form-section">
          <h3>候选版本（{{ shot.versions.length }}）</h3>
          <div class="version-list">
            <div v-for="v in shot.versions" :key="v.id" class="version-item"
                 :class="{ current: v.id === shot.current_version_id }">
              <video v-if="v.video_url" :src="v.video_url" controls preload="metadata" muted></video>
              <div class="version-info">
                <div class="v-head">
                  <b>v{{ v.version_no }}</b>
                  <span v-if="v.id === shot.current_version_id" class="badge done">采用中</span>
                  <span class="v-time">{{ (v.created_at || '').slice(5, 16).replace('T', ' ') }}</span>
                </div>
                <div class="v-params">
                  {{ v.engine }} · seed {{ v.seed }} ·
                  {{ v.params ? JSON.parse(v.params).width : '' }}×{{ v.params ? JSON.parse(v.params).height : '' }} ·
                  {{ v.params ? JSON.parse(v.params).num_frames : '' }}帧
                </div>
                <div class="v-actions">
                  <button v-if="v.id !== shot.current_version_id" class="btn"
                          @click="$emit('adopt', v.id)">采用此版本</button>
                  <button class="btn danger" @click="$emit('delete-version', v.id)">删除</button>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <footer class="modal-foot">
        <span v-if="errMsg" class="form-msg">{{ errMsg }}</span>
        <div style="margin-left: auto; display: flex; gap: 10px">
          <button class="btn" @click="$emit('close')">取消</button>
          <button class="btn" :disabled="saving" @click="save">💾 保存修改</button>
          <button class="btn btn-primary" :disabled="!canGenerate || saving" @click="generate">
            🎬 {{ shot.status === 'approved' || shot.status === 'rework' ? '重新制作' : '生成' }}
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const props = defineProps({
  shot: { type: Object, required: true },
  engines: { type: Array, default: () => [] },
  presets: { type: Array, default: () => [] },
  characters: { type: Array, default: () => [] },
  siblingShots: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'saved', 'refresh', 'generate', 'adopt', 'delete-version', 'delete-voice'])

const SHOT_SIZES = ['远景', '全景', '中景', '近景', '特写', '大特写']
const CAMERA_MOVES = ['固定', '推镜', '拉镜', '摇镜', '移镜', '跟镜', '环绕', '手持']
const MODES = [
  { id: 't2v', name: '文生视频', desc: '纯提示词生成（默认）' },
  { id: 'i2v', name: '图生视频', desc: '以参考图为首帧生成' },
  { id: 'first_last', name: '首尾帧', desc: '指定首帧与末帧生成中间过程' },
  { id: 'continuation', name: '视频续接', desc: '接上一镜头末帧继续生成' },
]
const RATES = [
  { v: '-20%', label: '慢速 -20%' }, { v: '-10%', label: '稍慢 -10%' },
  { v: '+0%', label: '正常' }, { v: '+10%', label: '稍快 +10%' }, { v: '+20%', label: '快速 +20%' },
]

const saving = ref(false)
const errMsg = ref('')

const form = reactive({
  title: props.shot.title || '',
  dialogue: props.shot.dialogue || '',
  shot_size: props.shot.shot_size || '中景',
  camera_movement: props.shot.camera_movement || '固定',
  duration_s: props.shot.duration_s || 3,
  reference_image: props.shot.reference_image || '',
  prompt: props.shot.prompt || '',
  negative_prompt: props.shot.negative_prompt || '',
  character_ids: [...(props.shot.character_ids || [])],
  mode: props.shot.mode || 't2v',
  continue_from: props.shot.continue_from || '',
})

const genForm = reactive({
  engine: 'anime',
  width: 512, height: 512,
  num_frames: 16, fps: 8, steps: 25, guidance: 7.5,
  seed: -1, preset: '',
})

const canGenerate = computed(() => form.prompt.trim().length > 0)

// ---------- LLM 提示词优化 ----------
const polishing = ref(false)
const polishMsg = ref('')
const polishMsgIsError = ref(false)

async function polishPrompt() {
  polishing.value = true
  polishMsg.value = ''
  polishMsgIsError.value = false
  try {
    const r = await api.llmPolish({
      prompt: form.prompt,
      shot_size: form.shot_size,
      dialogue: form.dialogue,
    })
    form.prompt = r.prompt
    polishMsg.value = '已优化为视频模型友好的英文提示词'
  } catch (e) {
    polishMsgIsError.value = true
    polishMsg.value = e.message.includes('未配置')
      ? 'LLM 未配置：请到「🧩 模型服务」页配置后使用'
      : e.message
  } finally {
    polishing.value = false
  }
}

// ---------- TTS ----------
const voices = ref([])
const voicesAvail = ref(false)
const ttsVoice = ref('')
const ttsRate = ref('+0%')
const ttsText = ref(props.shot.dialogue || '')
const ttsBusy = ref(false)

const voiceGroups = computed(() => {
  const mainland = voices.value.filter((v) => v.locale === 'zh-CN')
  const other = voices.value.filter((v) => v.locale !== 'zh-CN')
  const groups = []
  if (mainland.length) groups.push({ label: '大陆普通话', items: mainland })
  if (other.length) groups.push({ label: '其他地区/离线', items: other })
  return groups
})

// 打开弹窗时：若对白归属某个绑定角色，预选该角色音色
onMounted(async () => {
  try {
    const r = await api.ttsVoices()
    voices.value = r.voices || []
    voicesAvail.value = !!r.available
  } catch { /* ignore */ }
  const bound = (props.characters || []).filter((c) => form.character_ids.includes(c.id))
  const withVoice = bound.find((c) => c.voice_id)
  if (withVoice) ttsVoice.value = withVoice.voice_id
})

function charColor(name) {
  let h = 0
  for (const ch of name || '') h = (h * 31 + ch.codePointAt(0)) % 360
  return `hsl(${h}, 65%, 60%)`
}

function toggleCharacter(id) {
  const i = form.character_ids.indexOf(id)
  if (i >= 0) form.character_ids.splice(i, 1)
  else form.character_ids.push(id)
}

async function onRefUpload(e) {
  const file = e.target.files?.[0]
  if (!file) return
  try {
    const r = await api.uploadFile(file)
    form.reference_image = r.url
  } catch (err) {
    errMsg.value = err.message
  }
  e.target.value = ''
}

async function generateVoice() {
  ttsBusy.value = true
  errMsg.value = ''
  try {
    await api.generateVoice(props.shot.id, {
      text: ttsText.value, voice: ttsVoice.value, rate: ttsRate.value,
    })
    // 只刷新数据（拿到新 voices 列表），不关闭弹窗
    emit('refresh')
  } catch (e) {
    errMsg.value = e.message
  } finally {
    ttsBusy.value = false
  }
}

function applyPreset(q) {
  genForm.preset = q.id
  genForm.width = q.width
  genForm.height = q.height
  genForm.num_frames = q.num_frames
  genForm.fps = q.fps
  genForm.steps = q.steps
  genForm.guidance = q.guidance
}

function save() {
  emit('saved', { ...form })
}

function generate() {
  if (!form.prompt.trim()) {
    errMsg.value = '请先填写提示词'
    return
  }
  errMsg.value = ''
  // 先落编辑内容，再发起生成（带模式与续接来源）
  emit('saved', { ...form }, { then: 'generate', params: { ...genForm, mode: form.mode, continue_from: form.continue_from } })
}
</script>

<style scoped>
.modal-mask {
  position: fixed; inset: 0; background: rgba(8, 10, 15, 0.75);
  display: flex; align-items: center; justify-content: center;
  z-index: 200; padding: 24px; backdrop-filter: blur(3px);
}
.modal {
  width: min(860px, 100%); max-height: 92vh; display: flex; flex-direction: column;
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
  display: flex; align-items: center; gap: 8px;
}
.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12.5px; color: var(--text-dim); margin-bottom: 5px; }
.btn.mini {
  font-size: 11.5px; padding: 2px 10px; border-radius: 6px;
  vertical-align: middle;
}
.field input, .field select, .field textarea {
  width: 100%; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 8px; color: var(--text); padding: 8px 10px; font-size: 13px;
  font-family: inherit;
}
.field input:focus, .field select:focus, .field textarea:focus {
  border-color: var(--accent); outline: none;
}
.param-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.icon-btn {
  background: transparent; border: none; color: var(--text-dim);
  cursor: pointer; padding: 4px 7px; border-radius: 6px; font-size: 13px;
}
.icon-btn:hover:not(:disabled) { background: var(--bg-hover); color: var(--text); }
.icon-btn.danger:hover { color: var(--red); }
.btn {
  background: var(--bg-card); border: 1px solid var(--border); color: var(--text);
  border-radius: 8px; padding: 8px 16px; font-size: 13px; cursor: pointer; font-family: inherit;
}
.btn:hover:not(:disabled) { border-color: var(--accent); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary {
  background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600;
}
.btn.danger { border-color: var(--red); color: var(--red); }
.hint-text { font-size: 12px; color: var(--text-dim); margin: 6px 0 0; line-height: 1.6; }

.char-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.char-chip {
  display: flex; align-items: center; gap: 6px; font-size: 12.5px; cursor: pointer;
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 999px;
  padding: 5px 13px; color: var(--text-dim); transition: all 0.15s;
}
.char-chip:hover { border-color: var(--accent); }
.char-chip.on { border-color: var(--accent); color: var(--text); background: rgba(99, 102, 241, 0.12); }
.char-chip em { font-style: normal; font-size: 11px; color: var(--text-dim); }
.char-dot { width: 8px; height: 8px; border-radius: 50%; }

.mode-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.mode-card {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 12px; cursor: pointer; transition: all 0.15s;
}
.mode-card:hover { border-color: #3a4358; }
.mode-card.active { border-color: var(--accent); background: rgba(99, 102, 241, 0.1); }
.mc-name { font-size: 13px; font-weight: 600; margin-bottom: 3px; }
.mc-desc { font-size: 11px; color: var(--text-dim); line-height: 1.4; }

.ref-input-row { display: flex; gap: 8px; }
.ref-input-row input { flex: 1; }
.upload-btn { flex-shrink: 0; }

.tts-state { font-size: 11px; padding: 2px 9px; border-radius: 999px; font-weight: 600; }
.tts-state.on { background: rgba(52, 211, 153, 0.15); color: var(--green); }
.tts-state.off { background: rgba(248, 113, 113, 0.15); color: var(--red); }
.tts-panel {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 14px;
}
.tts-row { display: flex; gap: 8px; align-items: center; }
.tts-row select {
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: 8px;
  color: var(--text); padding: 8px 10px; font-size: 12.5px; font-family: inherit;
}
.tts-row select:first-child { flex: 1; min-width: 0; }
.voice-list { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.voice-item { display: flex; align-items: center; gap: 10px; }
.voice-item audio { height: 32px; flex-shrink: 0; }
.v-meta { font-size: 11.5px; color: var(--text-dim); flex: 1; min-width: 0; }

.comfy-hint {
  font-size: 12.5px; color: var(--text-dim); background: var(--bg-card);
  border-radius: 8px; padding: 10px 12px; margin-top: 12px; line-height: 1.6;
}
.engine-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.engine-card {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px;
  padding: 12px; cursor: pointer; transition: border-color 0.15s;
}
.engine-card:hover { border-color: #3a4358; }
.engine-card.active { border-color: var(--accent); }
.engine-card.disabled { opacity: 0.45; cursor: not-allowed; }
.ec-name { font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 6px; }
.ec-desc { font-size: 11.5px; color: var(--text-dim); margin-top: 4px; line-height: 1.5; }
.ec-note { font-size: 11px; color: var(--yellow); margin-top: 4px; }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot.ready { background: var(--green); }
.dot.unavailable { background: var(--text-dim); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  font-size: 11.5px; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 999px; padding: 3px 12px; cursor: pointer; color: var(--text-dim);
}
.chip:hover { border-color: var(--accent); color: var(--text); }
.chip.active { border-color: var(--accent); color: var(--accent); background: rgba(99, 102, 241, 0.12); }
.range-label { display: flex; justify-content: space-between; font-size: 12px; color: var(--text-dim); margin-bottom: 4px; }
.range-label input { width: 100%; }
.version-list { display: flex; flex-direction: column; gap: 12px; }
.version-item {
  display: flex; gap: 14px; background: var(--bg-card);
  border: 1px solid var(--border); border-radius: 10px; padding: 12px;
}
.version-item.current { border-color: var(--green); }
.version-item video { width: 220px; border-radius: 8px; background: #000; flex-shrink: 0; }
.version-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.v-head { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.v-time { font-size: 11.5px; color: var(--text-dim); margin-left: auto; }
.v-params { font-size: 11.5px; color: var(--text-dim); font-family: ui-monospace, monospace; }
.v-actions { display: flex; gap: 8px; margin-top: auto; }
.v-actions .btn { padding: 5px 12px; font-size: 12px; }
.badge {
  font-size: 11px; padding: 2px 9px; border-radius: 999px;
  background: var(--bg-card); border: 1px solid var(--border); color: var(--text-dim);
}
.badge.done { background: rgba(52, 211, 153, 0.15); color: var(--green); border-color: transparent; }
.form-msg { color: var(--red); font-size: 12.5px; }
</style>
