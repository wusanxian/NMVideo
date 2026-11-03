<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>生成视频</h1>
        <div class="page-sub">选择引擎与质量档位 · 任务在后台队列执行，提交后可离开页面</div>
      </div>
    </header>

    <div class="panel">
      <!-- 引擎选择 -->
      <div class="field">
        <label>生成引擎（两条通道都可用，按你的需要选）</label>
        <div v-for="g in engineGroups" :key="g.key" class="engine-group">
          <div class="engine-group-head">
            <span class="eg-title">{{ g.label }}</span>
            <span class="eg-hint">{{ g.hint }}</span>
          </div>
          <div class="engine-cards">
            <div
              v-for="e in g.items"
              :key="e.id"
              class="engine-card"
              :class="{ active: form.engine === e.id, disabled: e.status !== 'ready' }"
              @click="selectEngine(e)"
            >
              <div class="ec-name">
                <span class="dot" :class="e.status"></span>{{ e.name }}
                <span v-if="form.engine === e.id" class="ec-badge">当前选择</span>
                <span v-else-if="e.status !== 'ready'" class="ec-badge fallback">不可用</span>
              </div>
              <div class="ec-desc">{{ e.description }}</div>
              <div v-if="e.note" class="ec-note">⚠ {{ e.note }}</div>
            </div>
          </div>
        </div>
        <div v-if="engineNotice" class="engine-notice">{{ engineNotice }}</div>
      </div>

      <!-- 硬件面板（Wan 引擎按硬件自适应） -->
      <div v-if="form.engine === 'wan22' && hardware" class="hw-panel">
        <div class="hw-title">🖥 硬件自适应配置（自动探测）</div>
        <div class="hw-grid">
          <div><span>显卡</span><b>{{ hardware.gpu_name }}</b></div>
          <div><span>显存</span><b>{{ hardware.vram_gb || '—' }} GB</b></div>
          <div><span>内存</span><b>{{ hardware.ram_gb }} GB</b></div>
          <div><span>模型</span><b>{{ hardware.model_id === 't2v-a14b' ? 'Wan 2.2 T2V-A14B（双专家）' : 'Wan 2.2 TI2V-5B' }}</b></div>
          <div><span>量化</span><b>{{ { none: '不量化', int8: 'int8 权重量化', fp8: 'fp8 动态量化' }[hardware.quant] || hardware.quant }}</b></div>
          <div><span>显存卸载</span><b>{{ { none: '不需要', model: '模型级卸载', sequential: '逐层流式（8GB 模式）' }[hardware.offload] || hardware.offload }}</b></div>
          <div v-if="hardware.est_minutes_per_5s"><span>预估耗时</span><b>5 秒 720p 约 {{ hardware.est_minutes_per_5s }} 分钟</b></div>
        </div>
        <div v-for="n in hardware.notes" :key="n" class="hw-note">{{ n }}</div>
      </div>

      <!-- 画质档：H3 是「步数 + 两遍加速 + 步缓存」四档，与 Wan 的尺寸档正交，不能混用一个列表 -->
      <div v-if="form.engine === 'h3'" class="field">
        <label>画质档（步数 / 两遍加速 / 步缓存，不动模型）</label>
        <div class="quality-cards">
          <div
            v-for="t in h3Tiers"
            :key="t.id"
            class="quality-card"
            :class="{ active: form.h3_quality === t.id }"
            @click="applyH3Tier(t)"
          >
            <div class="qc-name">{{ t.name }}</div>
            <div class="qc-desc">{{ t.desc }}</div>
            <div class="qc-params">{{ t.params }}</div>
          </div>
        </div>
        <div v-if="h3Limits" class="h3-limits">
          {{ h3Basis }} → 画布 ≤ {{ h3Limits.max_pixels.toLocaleString() }} 像素，
          单段 ≤ {{ h3Limits.max_frames }} 帧（约 {{ h3Limits.max_seconds }} 秒）。
          更长的镜头请拆段，用「续接」接上一段的末帧。
        </div>
        <label class="check-line" :class="{ disabled: !h3PreviewOk }">
          <input type="checkbox" v-model="form.preview" :disabled="!h3PreviewOk" />
          <span v-if="h3PreviewOk">先出半尺寸预览，满意后再按同一档位二采。</span>
          <span v-else>当前档位是单遍采样，不支持预览——预览只服务 8 步两遍档。</span>
        </label>
      </div>

      <!-- 质量档位（Wan 引擎专属：档位参数按 Wan 2.2 尺寸设计） -->
      <div v-else-if="form.engine !== 'anime'" class="field">
        <label>质量目标（具体参数按引擎与工作流能力调整）</label>
        <div class="quality-cards">
          <div
            v-for="q in qualityPresets"
            :key="q.id"
            class="quality-card"
            :class="{ active: form.preset === q.id }"
            @click="applyQuality(q)"
          >
            <div class="qc-name">{{ q.name }}</div>
            <div class="qc-desc">{{ q.desc }}</div>
            <div class="qc-params">{{ q.width }}×{{ q.height }} · {{ q.num_frames }}帧 · {{ q.steps }}步</div>
          </div>
        </div>
        <label class="check-line">
          <input type="checkbox" v-model="form.preview" />
          <span>先出预览，满意后到画廊生成高清候选；H3 原生预览支持继续二采。</span>
        </label>
      </div>
      <div v-else class="anime-tip">
        AnimeDiff 为 SD1.5 级模型：建议 ≤32 帧、≤640×640（512×512 16 帧是甜点位）。
        更大参数请切换到 <b>Wan 2.2</b> 引擎（8GB 显存可跑 720p）。
      </div>

      <!-- 风格预设 -->
      <div class="field">
        <label>风格预设（点击填入提示词）</label>
        <div class="chips">
          <span
            v-for="p in PRESETS"
            :key="p.name"
            class="chip"
            :class="{ active: activePreset === p.name }"
            @click="applyPreset(p)"
          >{{ p.name }}</span>
        </div>
      </div>

      <!-- 提示词 -->
      <div class="field">
        <label>提示词 Prompt</label>
        <textarea v-model="form.prompt" placeholder="描述你想要的画面，如：masterpiece, best quality, anime style, 1girl, ..."></textarea>
      </div>

      <div class="field">
        <label>负面提示词 Negative Prompt</label>
        <textarea v-model="form.negative_prompt" placeholder="不希望出现的元素，如：worst quality, blurry, watermark"></textarea>
      </div>

      <!-- 参数 -->
      <div class="param-grid">
        <div class="field">
          <div class="range-label"><span>帧数（时长=帧数/帧率）</span><b>{{ form.num_frames }} 帧 / {{ (form.num_frames / form.fps).toFixed(1) }}s</b></div>
          <input type="range" :min="frameMin" :max="frameMax" :step="frameStep" v-model.number="form.num_frames" />
        </div>
        <div class="field">
          <div class="range-label"><span>帧率 FPS</span><b>{{ form.fps }}</b></div>
          <input type="range" min="4" :max="['wan22','h3','h3_native'].includes(form.engine) ? 24 : 16" step="1" v-model.number="form.fps" :disabled="form.engine === 'h3'" />
        </div>
        <div v-if="form.engine !== 'h3'" class="field">
          <div class="range-label"><span>采样步数（越高越精细越慢）</span><b>{{ form.steps }}</b></div>
          <input type="range" min="10" max="50" step="1" v-model.number="form.steps" />
        </div>
        <div v-else class="field">
          <div class="range-label"><span>采样步数</span><b>{{ form.steps }} 步{{ twoPass ? ' 两遍' : ' 单遍' }}</b></div>
          <div class="hint-text">H3 的步数由画质档决定，不单独调；改成 8 步以外，两遍加速会自动失效。</div>
        </div>
        <div class="field">
          <div class="range-label"><span>提示词引导 CFG</span><b>{{ form.guidance }}</b></div>
          <input type="range" min="1" :max="['wan22','h3','h3_native'].includes(form.engine) ? 10 : 15" step="0.5" v-model.number="form.guidance" />
        </div>
        <div class="field">
          <label>宽度</label>
          <input type="number" v-model.number="form.width" min="256" :max="['wan22','h3','h3_native'].includes(form.engine) ? 1344 : 1024" step="16" />
        </div>
        <div class="field">
          <label>高度</label>
          <input type="number" v-model.number="form.height" min="256" :max="['wan22','h3','h3_native'].includes(form.engine) ? 768 : 1024" step="16" />
        </div>
        <div class="field">
          <label>随机种子（-1 为随机）</label>
          <input type="number" v-model.number="form.seed" min="-1" />
        </div>
        <div class="field">
          <label>分辨率快捷</label>
          <div class="chips">
            <template v-if="form.engine === 'h3' && h3Limits">
              <span v-for="c in h3Limits.canvas_options" :key="c.join('x')" class="chip" @click="setSize(c[0], c[1])">{{ c[0] }}×{{ c[1] }}</span>
            </template>
            <template v-else-if="['wan22','h3_native'].includes(form.engine)">
              <span class="chip" @click="setSize(832, 480)">480p 横</span>
              <span class="chip" @click="setSize(480, 832)">480p 竖</span>
              <span class="chip" @click="setSize(1280, 720)">720p 横</span>
              <span class="chip" @click="setSize(720, 1280)">720p 竖</span>
            </template>
            <template v-else>
              <span class="chip" @click="setSize(512, 512)">512×512</span>
              <span class="chip" @click="setSize(384, 384)">384×384 快</span>
              <span class="chip" @click="setSize(512, 768)">512×768 竖</span>
              <span class="chip" @click="setSize(768, 512)">768×512 横</span>
            </template>
          </div>
        </div>
      </div>

      <!-- 提交 -->
      <p v-if="form.engine === 'h3'" class="hint-text" style="margin: 0 0 10px">
        当前走 <b>ComfyUI 通道</b>：按模式自动套用内置 <b>nmvideo_h3_*</b> 预设并固化快照。
        宽高按 32 对齐、帧数吸附到 17n+5、帧率固定 24；步数由画质档决定，H3 是 CFG 蒸馏模型，无引导系数。
        ComfyUI 侧缺 H3 插件节点时，这张卡会如实标记为不可用。
      </p>
      <p v-else-if="form.engine === 'h3_native'" class="hint-text" style="margin: 0 0 10px">
        原生兜底通道（进程内推理，不经 ComfyUI）：H3 是 guidance 蒸馏模型，<b>固定 8 步、无 CFG</b>
        （上方「步数 / CFG」不生效）；最少出 5 秒画面，更短的镜头会自动截取到设定时长；宽高按 32 对齐。
      </p>
      <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap">
        <button class="btn btn-primary" :disabled="store.submitting || !form.prompt.trim()" @click="submit">
          {{ store.submitting ? '提交中…' : (form.preview ? '🔍 生成预览' : '🎬 开始生成') }}
        </button>
        <span v-if="message" class="form-msg">{{ message }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { engineName, getEnginePref, resolveEngine, setEnginePref } from '../enginePref'
import { useJobsStore } from '../store/jobs'

const PRESETS = [
  {
    name: '二次元少女',
    prompt: 'masterpiece, best quality, anime style, 1girl, long silver hair, red eyes, standing in cherry blossom garden, petals falling in the wind, soft cinematic lighting, gentle smile',
    negative: 'worst quality, low quality, blurry, watermark, signature, username, bad anatomy',
    steps: 25, guidance: 7.5, frames: 16, fps: 8,
  },
  {
    name: '修仙剑修',
    prompt: 'masterpiece, best quality, chinese xianxia fantasy style, young swordsman in white robe flying above misty mountains, glowing blue sword trail, ancient palaces on cliffs, golden sunset clouds',
    negative: 'worst quality, blurry, modern clothes, city, text',
    steps: 25, guidance: 7.5, frames: 16, fps: 8,
  },
  {
    name: '国风水墨',
    prompt: 'chinese ink wash painting style, misty mountains, small wooden boat on calm river, water ripples spreading, minimal and elegant, soft gray tones, distant waterfall',
    negative: 'worst quality, blurry, oversaturated, text',
    steps: 25, guidance: 7.0, frames: 16, fps: 8,
  },
  {
    name: '赛博夜城',
    prompt: 'cyberpunk city street at night, heavy rain, neon signs reflecting on wet ground, anime style, flying cars in the sky, steam from vents, cinematic atmosphere',
    negative: 'worst quality, blurry, daylight, text',
    steps: 25, guidance: 7.5, frames: 16, fps: 8,
  },
]

const router = useRouter()
const store = useJobsStore()
const engines = ref([])
const qualityPresets = ref([])
const h3Info = ref({ tiers: [], limits: null })
const hardware = ref(null)
const message = ref('')
const activePreset = ref('')
const engineNotice = ref('')

const form = reactive({
  // 引擎由用户自选：默认 ComfyUI 通道，但记的是用户上一次的选择。
  // 选中即写入偏好，其他入口（分镜弹窗 / 小说批量 / 快捷生成）沿用。
  engine: getEnginePref(),
  prompt: '',
  negative_prompt: 'worst quality, low quality, blurry, watermark, signature',
  width: 512,
  height: 512,
  num_frames: 16,
  fps: 8,
  steps: 25,
  guidance: 7.5,
  seed: -1,
  preset: '',
  h3_quality: '',
  preview: false,
})

// ---- H3 画质档与画布上限（档位切步数，上限切尺寸/时长，两件事分开） ----
const h3Tiers = computed(() => h3Info.value.tiers || [])
const h3Limits = computed(() => h3Info.value.limits || null)
const currentTier = computed(
  () => h3Tiers.value.find((t) => t.id === form.h3_quality) || h3Tiers.value[0] || null
)
// 预览只服务 8 步两遍档：单遍档下 preview=true 会被节点直接拒
const h3PreviewOk = computed(() => (form.engine === 'h3' ? !!currentTier.value?.two_pass : true))
// 上限是按谁的显卡算的：H3 跑在 ComfyUI 上，所以正常应该是服务端；
// 服务端连不上时退回本机估算，这里必须说清，别让用户以为上限是凭空来的
const h3Basis = computed(() => {
  const b = h3Limits.value?.basis || {}
  if (b.vram_source === 'server') {
    const gpu = b.gpu_name || 'ComfyUI 服务端'
    return `按 ComfyUI 服务端 ${gpu}${b.vram_gib ? `（${b.vram_gib}GB）` : ''} 标定`
  }
  if (b.vram_source === 'local') return 'ComfyUI 服务端未连上，暂按本机显卡估算'
  return '显存读不到，按最低档保守标定'
})
const twoPass = computed(() => !!currentTier.value?.two_pass)
const frameMin = computed(() => (form.engine === 'h3' ? 39 : 8))
const frameMax = computed(() => {
  if (form.engine === 'h3') return h3Limits.value?.max_frames ?? 430
  return ['wan22', 'h3_native'].includes(form.engine) ? 121 : 32
})
// H3 的帧数必须落在 17n+5 上，滑块步长按 17 走，正好只经过合法值
const frameStep = computed(() => (form.engine === 'h3' ? 17 : ['wan22', 'h3_native'].includes(form.engine) ? 4 : 2))

function applyH3Tier(t) {
  form.h3_quality = t.id
  // 步数是档位的产物，不是用户输入：跟着档位写回，界面读数才不会和实际执行不一致
  form.steps = t.base_steps
  if (!t.two_pass) form.preview = false
}

// 按执行通道分组：把"经 ComfyUI"和"本机进程内"分开摆，但两组都开放给用户选，
// 产品只标注区别，不替用户做取舍。
const engineGroups = computed(() => {
  const comfy = engines.value.filter((e) => e.execution_channel !== 'native')
  const native = engines.value.filter((e) => e.execution_channel === 'native')
  return [
    { key: 'comfyui', label: 'ComfyUI 通道', hint: '提交工作流给 ComfyUI 执行，模型与节点由它管理', items: comfy },
    { key: 'native', label: '本机进程内通道', hint: '不依赖 ComfyUI，直接在本机加载模型推理', items: native },
  ].filter((g) => g.items.length)
})

function selectEngine(e) {
  if (e.status !== 'ready') return
  form.engine = e.id
  setEnginePref(e.id)
  engineNotice.value = ''
}

function applyPreset(p) {
  form.prompt = p.prompt
  form.negative_prompt = p.negative
  form.steps = p.steps
  form.guidance = p.guidance
  form.num_frames = p.frames
  form.fps = p.fps
  activePreset.value = p.name
}

function applyQuality(q) {
  form.preset = q.id
  form.width = q.width
  form.height = q.height
  form.num_frames = q.num_frames
  form.fps = q.fps
  form.steps = q.steps
  form.guidance = q.guidance
}

function setSize(w, h) {
  form.width = w
  form.height = h
}

// 切引擎时清掉不属于新引擎的字段，并把 H3 拉回它的坐标系（24 帧率 + 17n+5 帧数）
watch(
  () => form.engine,
  (e) => {
    if (e === 'anime') {
      form.preset = ''
      form.preview = false
      return
    }
    if (e !== 'h3') return
    form.preset = ''
    form.fps = 24
    // 顺手把帧数和画布收进档位上限：切过来时若停在 8 帧 / 512×512 之外的越界值，
    // 提交才报错不如现在就摆正
    const frames = Math.min(Math.max(form.num_frames, 39), frameMax.value)
    form.num_frames = 5 + 17 * Math.max(2, Math.ceil((frames - 5) / 17))
    if (!form.h3_quality && h3Tiers.value.length) applyH3Tier(h3Tiers.value[0])
  }
)

async function submit() {
  if (!form.prompt.trim()) {
    message.value = '请先输入提示词'
    return
  }
  message.value = ''
  try {
    await store.submit({ ...form })
    router.push('/queue')
  } catch (e) {
    message.value = `提交失败: ${e.message}`
  }
}

onMounted(async () => {
  try {
    engines.value = await api.engines()
    // 沿用用户上次的选择；只有它当前确实不可用，才临时切到别的可用引擎。
    // 注意不回写偏好——引擎恢复后用户的选择应当自动生效。
    const preferred = form.engine
    const { engine, fallback } = resolveEngine(engines.value, preferred)
    if (fallback) {
      form.engine = engine
      engineNotice.value = `你上次选的「${engineName(engines.value, preferred)}」当前不可用，本次先用「${engineName(engines.value, engine)}」；它恢复后会自动沿用你的选择。`
    }
  } catch {
    engines.value = []
  }
  try {
    qualityPresets.value = await api.presets()
  } catch {
    qualityPresets.value = []
  }
  try {
    h3Info.value = await api.h3Tiers()
    // 默认档落定：后端说了算（light），界面别自己猜一个
    if (!form.h3_quality) {
      const wanted = h3Info.value.default || h3Info.value.tiers?.[0]?.id
      const tier = (h3Info.value.tiers || []).find((t) => t.id === wanted)
      if (tier) applyH3Tier(tier)
    }
  } catch {
    h3Info.value = { tiers: [], limits: null }
  }
  try {
    hardware.value = await api.hardware()
  } catch {
    hardware.value = null
  }
  // 画廊「复用参数」写入的草稿
  const saved = localStorage.getItem('aivideo:reuse')
  if (saved) {
    try {
      Object.assign(form, JSON.parse(saved))
    } catch {
      /* ignore */
    }
    localStorage.removeItem('aivideo:reuse')
    // 复用的任务可能来自已不可用的引擎，这里再校一次可用性，别让表单停在坏引擎上
    const reused = form.engine
    const { engine, fallback } = resolveEngine(engines.value, reused)
    if (fallback) {
      form.engine = engine
      engineNotice.value = `复用参数里的「${engineName(engines.value, reused)}」当前不可用，本次先用「${engineName(engines.value, engine)}」。`
    }
  }
})
</script>

<style scoped>
.hw-panel {
  background: var(--bg-inset, rgba(127, 127, 127, 0.08));
  border: 1px solid var(--border, rgba(127, 127, 127, 0.25));
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 18px;
}
.hw-title { font-weight: 600; margin-bottom: 10px; }
.hw-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 8px 16px;
  font-size: 13px;
}
.hw-grid > div { display: flex; justify-content: space-between; gap: 8px; }
.hw-grid span { color: var(--text-dim); }
.hw-note { margin-top: 8px; font-size: 12px; color: var(--text-dim); }

.quality-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}
.quality-card {
  border: 1px solid var(--border, rgba(127, 127, 127, 0.25));
  border-radius: 10px;
  padding: 12px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.quality-card:hover { border-color: var(--accent, #7c6cf0); }
.quality-card.active {
  border-color: var(--accent, #7c6cf0);
  background: color-mix(in srgb, var(--accent, #7c6cf0) 12%, transparent);
}
.qc-name { font-weight: 600; margin-bottom: 4px; }
.qc-desc { font-size: 12px; color: var(--text-dim); margin-bottom: 6px; }
.qc-params { font-size: 11px; opacity: 0.75; font-variant-numeric: tabular-nums; }

.check-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  font-size: 13px;
  color: var(--text-dim);
  cursor: pointer;
}
.check-line.disabled { opacity: 0.55; cursor: not-allowed; }

/* H3 上限提示：数字要能一眼读，别和正文混成一片 */
.h3-limits {
  margin-top: 10px;
  padding: 8px 12px;
  border-left: 2px solid var(--accent, #7c6cf0);
  background: var(--bg-inset, rgba(127, 127, 127, 0.06));
  border-radius: 0 6px 6px 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-dim);
  font-variant-numeric: tabular-nums;
}

.anime-tip {
  font-size: 13px;
  color: var(--text-dim);
  background: var(--bg-inset, rgba(127, 127, 127, 0.08));
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 18px;
}
</style>
