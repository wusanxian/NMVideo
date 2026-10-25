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
        <label>生成引擎</label>
        <div class="engine-cards">
          <div
            v-for="e in engines"
            :key="e.id"
            class="engine-card"
            :class="{ active: form.engine === e.id, disabled: e.status !== 'ready' }"
            @click="e.status === 'ready' && (form.engine = e.id)"
          >
            <div class="ec-name">
              <span class="dot" :class="e.status"></span>{{ e.name }}
              <span v-if="e.is_default" class="ec-badge">默认</span>
              <span v-else-if="e.execution_channel === 'native'" class="ec-badge fallback">兜底</span>
            </div>
            <div class="ec-desc">{{ e.description }}</div>
            <div v-if="e.note" class="ec-note">⚠ {{ e.note }}</div>
          </div>
        </div>
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

      <!-- 质量档位（Wan 引擎专属：档位参数按 Wan 2.2 尺寸设计） -->
      <div v-if="form.engine !== 'anime'" class="field">
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
          <span>先出预览，满意后到画廊生成高清候选；FreeVideo 原生预览支持继续二采。</span>
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
          <input type="range" min="8" :max="['wan22','h3','h3_native'].includes(form.engine) ? 121 : 32" :step="['wan22','h3','h3_native'].includes(form.engine) ? 4 : 2" v-model.number="form.num_frames" />
        </div>
        <div class="field">
          <div class="range-label"><span>帧率 FPS</span><b>{{ form.fps }}</b></div>
          <input type="range" min="4" :max="['wan22','h3','h3_native'].includes(form.engine) ? 24 : 16" step="1" v-model.number="form.fps" />
        </div>
        <div class="field">
          <div class="range-label"><span>采样步数（越高越精细越慢）</span><b>{{ form.steps }}</b></div>
          <input type="range" min="10" max="50" step="1" v-model.number="form.steps" />
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
            <template v-if="['wan22','h3','h3_native'].includes(form.engine)">
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
        当前走 <b>ComfyUI 通道</b>：按模式自动套用内置 <b>freevideo_h3_*</b> 预设并固化快照。
        宽高按 32 对齐、帧数吸附到 17n+5；步数与 CFG 由工作流节点决定（8 步蒸馏图通常固定 8 步、无 CFG）。
        ComfyUI 侧缺 FreeVideo 节点时，这张卡会如实标记为不可用。
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
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
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
const hardware = ref(null)
const message = ref('')
const activePreset = ref('')

const form = reactive({
  // 默认引擎 = ComfyUI 通道（架构口径：ComfyUI 是唯一视频生成底座）。
  // ComfyUI 未连接时由 onMounted 的可用性检查自动降级到兜底引擎。
  engine: 'comfyui',
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
  preview: false,
})

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

// 切到 AnimeDiff 时清掉 Wan 专属字段（档位/预览），避免误提交
watch(
  () => form.engine,
  (e) => {
    if (e === 'anime') {
      form.preset = ''
      form.preview = false
    }
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
    // 默认引擎是 ComfyUI（唯一视频生成底座）。ComfyUI 未连接时自动切到第一个
    // 可用引擎（anime / wan22 / h3 属本机兜底通道），避免一打开就停在不可用引擎上。
    const current = engines.value.find((e) => e.id === form.engine)
    if (!current || current.status !== 'ready') {
      const usable = engines.value.find((e) => e.status === 'ready')
      if (usable) form.engine = usable.id
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

.anime-tip {
  font-size: 13px;
  color: var(--text-dim);
  background: var(--bg-inset, rgba(127, 127, 127, 0.08));
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 18px;
}
</style>
