<template>
  <div class="page launch">
    <header class="launch-head">
      <h1>环境准备</h1>
      <div class="page-sub">
        按本机实测配置推荐引擎：装好下面这几样就能出片，顺序按「宿主 → 引擎 → 配音 → 依赖」排。
      </div>
    </header>

    <!-- 硬件横幅：推荐结论由后端按显存 × 内存 × 磁盘算，不写死 -->
    <section class="hw-banner" :class="{ muted: !hwLine }">
      <div class="hw-ico">🖥</div>
      <div class="hw-text">
        <b>{{ hwLine || '正在检测本机配置…' }}</b>
        <span v-if="rec.summary">→ {{ rec.summary }}</span>
      </div>
      <button class="btn btn-sm" :disabled="checking" @click="refresh(true)">
        {{ checking ? '检测中…' : '重新检测' }}
      </button>
    </section>

    <div class="sec-label">需要安装 / 对接的工具</div>

    <!-- ① ComfyUI：引擎宿主。已装则填地址直接对接 -->
    <section class="tool-card">
      <div class="tc-top">
        <div class="tc-ico">🎬</div>
        <div class="tc-main">
          <div class="tc-title">
            ComfyUI
            <span class="tc-chip">引擎宿主 · 必需</span>
          </div>
          <div class="tc-desc">
            视频生成底座。本地已装直接填地址对接；没装的话先装 ComfyUI 并启动（默认
            <code>127.0.0.1:8188</code>）。
          </div>
        </div>
        <span class="tc-state" :class="comfyOnline ? 'ok' : 'idle'">
          {{ comfyOnline ? '已连接' : '待对接' }}
        </span>
      </div>

      <div class="tc-form">
        <label>ComfyUI 地址</label>
        <input
          v-model="comfyUrl"
          type="text"
          class="mono-input"
          placeholder="http://127.0.0.1:8188"
          @keyup.enter="connectComfy"
        />
        <button class="btn btn-primary btn-sm" :disabled="comfyBusy" @click="connectComfy">
          {{ comfyBusy ? '对接中…' : '测试并对接' }}
        </button>
      </div>

      <div v-if="comfyMsg" class="tc-msg" :class="comfyOnline ? 'ok' : 'warn'">{{ comfyMsg }}</div>
      <div v-else-if="comfyOnline" class="tc-msg ok">
        ✓ 已连接 {{ data.comfyui.url }} · 队列运行 {{ data.comfyui.queue_running }} / 排队
        {{ data.comfyui.queue_pending }}
        <template v-if="data.comfyui.vram_total_gb">
          · 显存 {{ data.comfyui.vram_free_gb }} / {{ data.comfyui.vram_total_gb }} GB
        </template>
      </div>
    </section>

    <!-- ② 视频生成引擎：H3 / Wan 二选一，可用性来自本机实测 -->
    <section class="tool-card">
      <div class="tc-top">
        <div class="tc-ico">🧠</div>
        <div class="tc-main">
          <div class="tc-title">
            视频生成引擎
            <span class="tc-chip accent">已按配置推荐</span>
          </div>
          <div class="tc-desc">
            显存决定画布上限，内存决定量化档位。选中的引擎会成为全站默认（生成页 / 分镜共用）。
          </div>
        </div>
        <span class="tc-state" :class="currentOpt && currentOpt.available ? 'ok' : 'warn'">
          {{ currentOpt ? currentOpt.name : '—' }}
        </span>
      </div>

      <div class="opt-list">
        <button
          v-for="opt in options"
          :key="opt.id"
          class="opt"
          :class="{
            active: currentOpt && currentOpt.id === opt.id,
            disabled: !opt.available,
          }"
          :disabled="!opt.available"
          @click="pickEngine(opt)"
        >
          <div class="opt-head">
            <span class="opt-radio" :class="{ on: currentOpt && currentOpt.id === opt.id }"></span>
            <b>{{ opt.name }}</b>
            <span v-if="rec.recommended === opt.id" class="opt-badge rec">推荐</span>
            <span v-else-if="!opt.available" class="opt-badge off">不可用</span>
            <span class="opt-tier">{{ opt.tier_label }}</span>
          </div>
          <div class="opt-sub">{{ opt.subtitle }}</div>
          <div class="opt-note" :class="{ bad: !opt.available }">
            {{ opt.available ? opt.note : (opt.blockers && opt.blockers[0]) || opt.note }}
          </div>
          <div v-if="!opt.available && opt.blockers && opt.blockers.length > 1" class="opt-more">
            另：{{ opt.blockers[1] }}
          </div>
          <div v-if="opt.available && opt.need_ram_gb" class="opt-meta">
            权重 {{ opt.weights_gb }}GiB · 需内存 {{ opt.need_ram_gb }}GB · 需磁盘 {{ opt.need_disk_gb }}GB
            · 画布上限 {{ opt.max_pixels }}px
          </div>
        </button>
      </div>

      <div class="tc-msg" :class="engineMsg ? 'ok' : ''" v-if="engineMsg">{{ engineMsg }}</div>
    </section>

    <!-- ③ 配音 TTS -->
    <section class="tool-card">
      <div class="tc-top">
        <div class="tc-ico">🎙</div>
        <div class="tc-main">
          <div class="tc-title">
            配音 · 语音合成 (TTS)
            <span class="tc-chip">可选 · 剪辑台用</span>
          </div>
          <div class="tc-desc">
            {{ tts.note || '联网走 edge-tts 神经音色，离线自动切系统语音；角色卡可绑定固定音色。' }}
          </div>
        </div>
        <span class="tc-state" :class="tts.online ? 'ok' : 'warn'">
          {{ tts.online ? '可用' : '不可用' }}
        </span>
      </div>
      <div class="tc-msg" :class="tts.online ? 'ok' : 'warn'">
        <template v-if="tts.online">
          ✓ 引擎就绪：分镜编辑弹窗里可直接生成台词配音，无需额外安装。
        </template>
        <template v-else>
          未检测到 TTS 库：<code>pip install edge-tts pyttsx3</code> 后重启后端。
        </template>
      </div>
    </section>

    <!-- ④ 运行依赖：只报在不在，不编进度 -->
    <section class="tool-card">
      <div class="tc-top">
        <div class="tc-ico">🧩</div>
        <div class="tc-main">
          <div class="tc-title">
            运行依赖
            <span class="tc-chip" :class="{ danger: missingDeps.length }">
              {{ missingDeps.length ? `缺 ${missingDeps.length} 项` : '全部就绪' }}
            </span>
          </div>
          <div class="tc-desc">
            Python 侧依赖在「模型与设置 → 环境」里能看到安装指引；这里只做体检，不替你装。
          </div>
        </div>
      </div>
      <div class="dep-grid">
        <div v-for="d in deps" :key="d.id" class="dep" :class="{ bad: !d.installed }">
          <span class="dep-mark">{{ d.installed ? '✓' : '✗' }}</span>
          <span class="dep-name">{{ d.name }}</span>
          <span class="dep-why">{{ d.why }}</span>
        </div>
      </div>
    </section>

    <!-- ⑤ 存储目录：点击直接在文件管理器打开 -->
    <section class="tool-card">
      <div class="tc-top">
        <div class="tc-ico">📁</div>
        <div class="tc-main">
          <div class="tc-title">
            生成视频存储目录
            <span class="tc-chip">点击打开</span>
          </div>
          <div class="tc-desc">
            成片、抽帧、配音都落在这里。换盘请在「模型与设置 → 环境」改
            <code>AIVIDEO_OUTPUTS_DIR</code>。
          </div>
        </div>
        <button class="btn btn-sm" :disabled="opening === 'outputs'" @click="openFolder('outputs')">
          {{ opening === 'outputs' ? '打开中…' : '打开目录' }}
        </button>
      </div>
      <div class="path-box mono">{{ data.paths.outputs.path || '—' }}</div>
      <div class="path-meta">
        已用 {{ gb1(data.paths.outputs.size_gb) }} · {{ data.paths.outputs.files || 0 }} 个文件
        <span class="sep">·</span>
        磁盘可用 {{ data.disk.free_gb >= 0 ? data.disk.free_gb + ' GB' : '未知' }}
      </div>
      <div v-if="openMsg" class="tc-msg" :class="openOk ? 'ok' : 'warn'">{{ openMsg }}</div>

      <div class="sub-row">
        <span>模型目录</span>
        <span class="mono">{{ data.paths.models.path || '—' }}</span>
        <button class="btn btn-sm" :disabled="opening === 'models'" @click="openFolder('models')">
          打开
        </button>
      </div>
    </section>

    <footer class="launch-actions">
      <span class="launch-tip">{{ error || autoTip }}</span>
      <router-link class="btn" to="/settings?tab=env">环境设置</router-link>
      <button class="btn btn-primary" :disabled="!comfyOnline" @click="goWorkbench">
        进入工作台
      </button>
    </footer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { getEnginePref, setEnginePref } from '../enginePref'

const router = useRouter()

const empty = {
  ok: false,
  ready: false,
  version: '',
  progress: 0,
  steps: [],
  comfyui: { url: '', online: false, vram_total_gb: 0, vram_free_gb: 0, queue_running: 0, queue_pending: 0 },
  paths: {
    project: '', backend: '', db: '',
    models: { path: '', size_gb: 0, files: 0 },
    outputs: { path: '', size_gb: 0, files: 0 },
  },
  disk: { free_gb: -1, total_gb: -1 },
  hardware: {
    gpu_name: '', vram_gb: 0, ram_gb: 0, available_ram_gb: null,
    device: '', compute_cap: '', disk_free_gb: 0, torch_cuda_available: false,
  },
  recommend: { recommended: '', summary: '', options: [] },
  deps: [],
}

const data = reactive(JSON.parse(JSON.stringify(empty)))
const checking = ref(false)
const error = ref('')
const comfyUrl = ref('')
const comfyBusy = ref(false)
const comfyMsg = ref('')
const engineMsg = ref('')
const tts = reactive({ online: false, note: '' })
const pref = ref(getEnginePref())
const opening = ref('')
const openMsg = ref('')
const openOk = ref(true)
let timer = null
let msgTimer = null

const comfyOnline = computed(() => !!data.comfyui.online)
const rec = computed(() => data.recommend || empty.recommend)
const options = computed(() => rec.value.options || [])
const deps = computed(() => data.deps || [])
const missingDeps = computed(() => deps.value.filter((d) => !d.installed))

/** 当前生效引擎：偏好可用就用偏好，否则落到后端推荐项。 */
const currentOpt = computed(() => {
  const list = options.value
  if (!list.length) return null
  const hit = list.find((o) => o.id === pref.value)
  if (hit && hit.available) return hit
  return list.find((o) => o.id === rec.value.recommended) || list[0]
})

const hwLine = computed(() => {
  const h = data.hardware || {}
  if (!h.gpu_name && !h.ram_gb) return ''
  const parts = [h.gpu_name || '未检测到独立显卡']
  if (h.vram_gb) parts.push(`${h.vram_gb}GB 显存`)
  if (h.ram_gb) parts.push(`${h.ram_gb}GB 内存`)
  return `已检测本机：${parts.join(' · ')}`
})

const autoTip = computed(() =>
  comfyOnline.value ? '本页每 10 秒自动复检' : 'ComfyUI 未连接 · 本页每 2 秒自动检测'
)

const gb1 = (v) => `${Number(v || 0).toFixed(1)} GB`

function flash(msg, ok = true) {
  openMsg.value = msg
  openOk.value = ok
  clearTimeout(msgTimer)
  msgTimer = setTimeout(() => (openMsg.value = ''), 4000)
}

async function refresh(manual = false) {
  if (manual) checking.value = true
  try {
    // 手动「重新检测」跳过服务端探测缓存：用户刚拉起 ComfyUI，不该等缓存过期
    const got = await api.launchStatus(manual)
    Object.assign(data, got)
    if (!comfyUrl.value) comfyUrl.value = got.comfyui.url || ''
    error.value = ''
    schedule(got.ready ? 10000 : 2000)
  } catch (e) {
    error.value = `状态读取失败：${e.message}`
    schedule(3000)
  } finally {
    checking.value = false
  }
}

function schedule(ms) {
  clearTimeout(timer)
  timer = setTimeout(() => refresh(false), ms)
}

async function loadSide() {
  // ComfyUI 地址以设置里的值为准（可能跟默认端口不同）
  try {
    const cfg = await api.servicesConfig()
    if (cfg.comfy_url) comfyUrl.value = cfg.comfy_url
  } catch {
    /* 后端离线时保持默认值 */
  }
  try {
    const st = await api.servicesStatus()
    tts.online = !!st.tts?.online
    tts.note = st.tts?.note || ''
  } catch {
    /* 忽略：TTS 卡片会显示「不可用」 */
  }
}

async function connectComfy() {
  comfyBusy.value = true
  comfyMsg.value = ''
  const url = (comfyUrl.value || '').trim() || 'http://127.0.0.1:8188'
  try {
    await api.saveComfyConfig({ comfy_url: url })
    const got = await api.launchStatus(true)
    Object.assign(data, got)
    comfyUrl.value = got.comfyui.url || url
    comfyMsg.value = got.comfyui.online
      ? `✓ 已连接 ${got.comfyui.url}（队列 ${got.comfyui.queue_running} 运行 / ${got.comfyui.queue_pending} 排队）`
      : `未连上 ${url}：确认 ComfyUI 已启动（python main.py），地址端口是否一致`
    schedule(got.ready ? 10000 : 2000)
  } catch (e) {
    comfyMsg.value = e.message
  } finally {
    comfyBusy.value = false
  }
}

function pickEngine(opt) {
  if (!opt.available) return
  pref.value = opt.id
  setEnginePref(opt.id)
  engineMsg.value = `已设为默认引擎：${opt.name}`
  setTimeout(() => {
    if (engineMsg.value.startsWith('已设为默认引擎')) engineMsg.value = ''
  }, 2500)
}

async function openFolder(key) {
  opening.value = key
  try {
    await api.openFolder(key)
    flash('已在文件管理器中打开')
  } catch (e) {
    // 后端不在本机（远程部署）时退到桌面桥，再退到复制路径
    const p = key === 'outputs' ? data.paths.outputs.path : data.paths.models.path
    if (typeof window !== 'undefined' && window.desk && window.desk.openPath && p) {
      try {
        await window.desk.openPath(p)
        flash('已打开目录')
        return
      } catch {
        /* 落到复制 */
      }
    }
    if (p) {
      try {
        await navigator.clipboard.writeText(p)
        flash(`无法直接打开（${e.message}），路径已复制：${p}`, false)
      } catch {
        flash(`无法打开目录：${e.message}`, false)
      }
    } else {
      flash(`无法打开目录：${e.message}`, false)
    }
  } finally {
    opening.value = ''
  }
}

function goWorkbench() {
  router.push('/projects')
}

onMounted(async () => {
  await Promise.all([refresh(true), loadSide()])
})
onUnmounted(() => {
  clearTimeout(timer)
  clearTimeout(msgTimer)
})
</script>

<style scoped>
.launch { max-width: 880px; margin: 0 auto; }

.launch-head { margin: 8px 0 20px; }
.launch-head h1 { font-size: 22px; margin: 0 0 6px; }

/* ---------- 硬件横幅 ---------- */
.hw-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 16px;
  margin-bottom: 20px;
  border: 1px solid rgba(var(--accent-rgb), .35);
  border-radius: 12px;
  background: rgba(var(--accent-rgb), .09);
}
.hw-banner.muted { border-color: var(--border); background: var(--bg-panel); }
.hw-ico { font-size: 18px; flex: none; }
.hw-text { flex: 1; min-width: 0; font-size: 12.5px; line-height: 1.7; color: var(--accent-soft); }
.hw-text b { color: var(--text); font-weight: 600; }
.hw-text span { display: block; color: var(--accent-soft); }
.hw-banner.muted .hw-text,
.hw-banner.muted .hw-text span { color: var(--text-dim); }

.sec-label { font-size: 12.5px; color: var(--text-dim); margin: 0 2px 10px; }

/* ---------- 工具卡 ---------- */
.tool-card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
  margin-bottom: 14px;
}

.tc-top { display: flex; align-items: flex-start; gap: 12px; }
.tc-ico {
  width: 32px; height: 32px; flex: none;
  display: grid; place-items: center;
  font-size: 16px;
  border-radius: 9px;
  background: rgba(var(--accent-rgb), .16);
}
.tc-main { flex: 1; min-width: 0; }
.tc-title {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 14.5px; font-weight: 600; color: var(--text);
}
.tc-desc { font-size: 12.5px; color: var(--text-dim); line-height: 1.7; margin-top: 4px; }
.tc-desc code {
  font-family: ui-monospace, monospace; font-size: 11.5px;
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 4px; padding: 0 4px;
}

.tc-chip {
  font-size: 11px; font-weight: 500;
  padding: 1px 8px; border-radius: 999px;
  color: var(--text-dim);
  border: 1px solid var(--border);
}
.tc-chip.accent { color: var(--accent-soft); border-color: rgba(var(--accent-rgb), .45); }
.tc-chip.danger { color: var(--red); border-color: rgba(248, 113, 113, .45); }

.tc-state {
  flex: none; font-size: 12px;
  padding: 3px 10px; border-radius: 999px;
  border: 1px solid var(--border);
}
.tc-state.ok { color: var(--green); border-color: rgba(52, 211, 153, .5); }
.tc-state.idle { color: var(--text-dim); }
.tc-state.warn { color: var(--yellow); border-color: rgba(251, 191, 36, .5); }

/* 对接表单 */
.tc-form { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
.tc-form label { font-size: 12.5px; color: var(--text-dim); flex: none; }
.tc-form input {
  flex: 1; min-width: 0; width: auto;
  padding: 8px 12px; font-size: 13px;
}
.mono-input { font-family: ui-monospace, monospace; font-size: 12.5px !important; }

.tc-msg { margin-top: 10px; font-size: 12.5px; line-height: 1.7; }
.tc-msg.ok { color: var(--green); }
.tc-msg.warn { color: var(--yellow); }

/* ---------- 引擎选项 ---------- */
.opt-list { display: grid; gap: 10px; margin-top: 14px; }
.opt {
  text-align: left;
  font-family: inherit;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-card);
  color: var(--text);
  cursor: pointer;
  transition: border-color .15s, background .15s;
}
.opt:hover:not(.disabled) { border-color: var(--accent); }
.opt.active { border-color: var(--accent); background: rgba(var(--accent-rgb), .08); }
.opt.disabled { opacity: .6; cursor: not-allowed; }
.opt-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13.5px; }
.opt-radio {
  width: 13px; height: 13px; flex: none;
  border-radius: 50%; border: 1px solid var(--border-strong);
  background: transparent;
}
.opt-radio.on {
  border-color: var(--accent);
  background: radial-gradient(circle, var(--accent) 0 45%, transparent 50%);
}
.opt-badge {
  font-size: 11px; font-weight: 500;
  padding: 1px 7px; border-radius: 999px;
}
.opt-badge.rec { color: var(--green); background: rgba(52, 211, 153, .14); }
.opt-badge.off { color: var(--yellow); background: rgba(251, 191, 36, .14); }
.opt-tier {
  margin-left: auto; font-size: 11px; color: var(--text-dim);
  font-family: ui-monospace, monospace;
}
.opt-sub { font-size: 12px; color: var(--text-dim); margin-top: 4px; }
.opt-note { font-size: 12px; color: var(--text-dim); line-height: 1.65; margin-top: 6px; }
.opt-note.bad { color: var(--yellow); }
.opt-more { font-size: 11.5px; color: var(--yellow); margin-top: 4px; opacity: .85; }
.opt-meta {
  font-size: 11.5px; color: var(--text-dim);
  font-family: ui-monospace, monospace;
  margin-top: 6px;
}

/* ---------- 依赖 ---------- */
.dep-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 8px;
  margin-top: 14px;
}
.dep {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-card);
  font-size: 12px;
}
.dep-mark { color: var(--green); flex: none; font-weight: 700; }
.dep.bad .dep-mark { color: var(--red); }
.dep-name { color: var(--text); flex: none; }
/* 说明允许换行：截断成「内置 ffmpeg，合成 MP4…」反而看不懂是什么 */
.dep-why { color: var(--text-dim); font-size: 11.5px; line-height: 1.55; }

/* ---------- 存储 ---------- */
.path-box {
  margin-top: 12px;
  padding: 9px 12px;
  border-radius: 8px;
  background: var(--bg);
  border: 1px solid var(--border);
  font-size: 12px;
  color: var(--text);
  overflow-wrap: anywhere;
}
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.path-meta { font-size: 12px; color: var(--text-dim); margin-top: 8px; }
.path-meta .sep { margin: 0 6px; opacity: .6; }

.sub-row {
  display: flex; align-items: center; gap: 10px;
  margin-top: 14px; padding-top: 12px;
  border-top: 1px solid var(--border);
  font-size: 12.5px; color: var(--text-dim);
}
.sub-row .mono { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ---------- 底部 ---------- */
.launch-actions { display: flex; align-items: center; gap: 10px; padding: 6px 0 24px; }
.launch-actions .btn { text-decoration: none; display: inline-flex; align-items: center; }
.launch-tip { margin-right: auto; font-size: 12px; color: var(--text-dim); }
</style>
