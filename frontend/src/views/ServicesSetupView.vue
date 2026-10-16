<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>模型服务</h1>
        <div class="page-sub">
          四类模型服务统一管理：视频工作流（ComfyUI）、LLM（AI 编剧 / 提示词优化）、
          图像生成（参考图 / 概念图）、TTS（配音）。配置保存在后端 settings.json，重启不丢失。
        </div>
      </div>
      <router-link to="/projects" class="btn">← 返回工作台</router-link>
    </header>

    <!-- 总览 -->
    <div class="panel overview">
      <div v-for="s in cards" :key="s.key" class="ov-item">
        <span class="dot" :class="s.online ? 'ready' : 'unavailable'"></span>
        <div class="ov-text">
          <b>{{ s.title }}</b>
          <span>{{ s.online ? s.onlineText : s.offlineText }}</span>
        </div>
      </div>
      <button class="btn" style="margin-left: auto" @click="refreshAll">🔄 全部重新检测</button>
    </div>

    <!-- ComfyUI -->
    <section class="panel">
      <div class="card-head">
        <span class="dot" :class="comfy.online ? 'ready' : 'unavailable'"></span>
        <h3>🎬 视频工作流 · ComfyUI</h3>
        <span class="tag" :class="comfy.online ? 'ok' : 'off'">{{ comfy.online ? '已连接' : '未连接' }}</span>
      </div>
      <p class="hint">
        本地/远程 ComfyUI 服务器（默认 <code>127.0.0.1:8188</code>）。分镜生成选「ComfyUI 工作流」引擎时调用；
        工作流模板与内置预设（MiniMax H3 / Wan 2.2 / AnimateDiff）在「ComfyUI 设置」页维护。
      </p>
      <div class="form-row">
        <label>服务器地址</label>
        <input v-model="comfyForm.comfy_url" placeholder="http://127.0.0.1:8188" />
        <button class="btn" :disabled="busy.comfy" @click="saveComfy">保存</button>
      </div>
      <p class="hint" v-if="!comfy.online">
        未连接：请先启动 ComfyUI（<code>python main.py --listen</code>），或把地址改成你的 ComfyUI 服务器。
      </p>
    </section>

    <!-- LLM -->
    <section class="panel">
      <div class="card-head">
        <span class="dot" :class="llm.online ? 'ready' : 'unavailable'"></span>
        <h3>🧠 LLM · AI 编剧 / 提示词优化</h3>
        <span class="tag" :class="llm.configured ? 'ok' : 'off'">{{ llm.configured ? '已配置' : '未配置' }}</span>
      </div>
      <p class="hint">
        任意 OpenAI 兼容 <code>/chat/completions</code> 端点：DeepSeek、通义千问、Kimi、智谱，
        或本地 Ollama（<code>http://127.0.0.1:11434/v1</code>，Key 可留空）。
        用途：剧本页「AI 编剧」、分镜编辑「✨ 优化提示词」、台词润色。
      </p>
      <div class="form-grid">
        <div class="form-row">
          <label>Base URL</label>
          <input v-model="llmForm.base_url" placeholder="https://api.deepseek.com/v1" />
        </div>
        <div class="form-row">
          <label>模型名</label>
          <input v-model="llmForm.model" placeholder="deepseek-chat" />
        </div>
        <div class="form-row">
          <label>API Key</label>
          <input v-model="llmForm.api_key" type="password" placeholder="sk-..." />
        </div>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary" :disabled="busy.llm" @click="saveLlm">💾 保存配置</button>
        <button class="btn" :disabled="busy.llmTest" @click="testLlm">
          {{ busy.llmTest ? '测试中…' : '🔌 测试连接' }}
        </button>
      </div>
      <p v-if="msg.llm" class="form-msg" :class="{ error: !testResults.llm.ok }">{{ msg.llm }}</p>
    </section>

    <!-- 图像生成 -->
    <section class="panel">
      <div class="card-head">
        <span class="dot" :class="imagegen.online ? 'ready' : 'unavailable'"></span>
        <h3>🖼 图像生成 · 参考图 / 概念图 / 首帧</h3>
        <span class="tag" :class="imagegen.configured ? 'ok' : 'off'">{{ imagegen.configured ? '已配置' : '未配置' }}</span>
      </div>
      <p class="hint">
        任意 OpenAI 兼容 <code>/images/generations</code> 端点（返回 URL 或 b64_json 均可）。
        用途：资产库角色卡「生成参考图」、场景概念图、镜头首帧（图生视频前置）。
        未配置时也可以直接上传图片，不影响使用。
      </p>
      <div class="form-grid">
        <div class="form-row">
          <label>Base URL</label>
          <input v-model="imagegenForm.base_url" placeholder="https://api.openai.com/v1" />
        </div>
        <div class="form-row">
          <label>模型名</label>
          <input v-model="imagegenForm.model" placeholder="dall-e-3 / gpt-image-1" />
        </div>
        <div class="form-row">
          <label>API Key</label>
          <input v-model="imagegenForm.api_key" type="password" placeholder="sk-..." />
        </div>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary" :disabled="busy.imagegen" @click="saveImagegen">💾 保存配置</button>
        <button class="btn" :disabled="busy.imagegenTest" @click="testImagegen">
          {{ busy.imagegenTest ? '测试中…' : '🔌 测试连接' }}
        </button>
      </div>
      <p v-if="msg.imagegen" class="form-msg" :class="{ error: !testResults.imagegen.ok }">{{ msg.imagegen }}</p>
      <div v-if="testResults.imagegen.ok && testResults.imagegen.url" class="test-preview">
        <img :src="testResults.imagegen.url" alt="测试图" />
      </div>
    </section>

    <!-- TTS -->
    <section class="panel">
      <div class="card-head">
        <span class="dot" :class="tts.online ? 'ready' : 'unavailable'"></span>
        <h3>🎙 TTS · 配音</h3>
        <span class="tag" :class="tts.online ? 'ok' : 'off'">{{ tts.online ? '可用' : '不可用' }}</span>
      </div>
      <p class="hint">
        无需配置：联网时用 edge-tts 神经音色（14 个中文音色，分镜编辑弹窗里可选），
        离线时自动切 Windows SAPI5 本地语音。角色卡可绑定固定音色，全片人物声音一致。
      </p>
      <p class="hint" v-if="tts.note">{{ tts.note }}</p>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const status = ref({ comfyui: {}, llm: {}, imagegen: {}, tts: {} })
const config = ref({ llm: {}, imagegen: {}, comfy_url: '' })

const busy = reactive({ comfy: false, llm: false, imagegen: false, llmTest: false, imagegenTest: false })
const msg = reactive({ llm: '', imagegen: '' })
const testResults = reactive({ llm: { ok: false }, imagegen: { ok: false } })

const comfyForm = reactive({ comfy_url: '' })
const llmForm = reactive({ base_url: '', api_key: '', model: '' })
const imagegenForm = reactive({ base_url: '', api_key: '', model: '' })

const comfy = computed(() => status.value.comfyui || {})
const llm = computed(() => status.value.llm || {})
const imagegen = computed(() => status.value.imagegen || {})
const tts = computed(() => status.value.tts || {})

const cards = computed(() => [
  { key: 'comfy', title: 'ComfyUI 视频工作流', online: comfy.value.online, onlineText: 'MiniMax H3 / Wan 等', offlineText: '未连接' },
  { key: 'llm', title: 'LLM', online: llm.value.configured, onlineText: 'AI 编剧 / 提示词优化', offlineText: '未配置' },
  { key: 'imagegen', title: '图像生成', online: imagegen.value.configured, onlineText: '参考图 / 概念图', offlineText: '未配置' },
  { key: 'tts', title: 'TTS 配音', online: tts.value.online, onlineText: tts.value.note || '可用', offlineText: '不可用' },
])

async function refreshAll() {
  try {
    status.value = await api.servicesStatus()
  } catch {
    /* 后端离线时保持空状态 */
  }
  try {
    const c = await api.servicesConfig()
    config.value = c
    comfyForm.comfy_url = c.comfy_url || ''
    // api_key 打码返回：留空表示不修改
    llmForm.base_url = c.llm.base_url || ''
    llmForm.api_key = ''
    llmForm.model = c.llm.model || ''
    imagegenForm.base_url = c.imagegen.base_url || ''
    imagegenForm.api_key = ''
    imagegenForm.model = c.imagegen.model || ''
  } catch {
    /* ignore */
  }
}

async function saveComfy() {
  busy.comfy = true
  try {
    await api.saveComfyConfig({ comfy_url: comfyForm.comfy_url.trim() })
    await refreshAll()
  } catch (e) {
    alert(e.message)
  } finally {
    busy.comfy = false
  }
}

async function saveLlm() {
  busy.llm = true
  msg.llm = ''
  try {
    const body = {
      base_url: llmForm.base_url.trim(),
      model: llmForm.model.trim(),
      // 密码框留空 = 保持原 key 不变
      ...(llmForm.api_key.trim() ? { api_key: llmForm.api_key.trim() } : {}),
    }
    await api.saveLlmConfig(body)
    llmForm.api_key = ''
    msg.llm = '已保存'
    await refreshAll()
  } catch (e) {
    msg.llm = e.message
  } finally {
    busy.llm = false
  }
}

async function testLlm() {
  busy.llmTest = true
  msg.llm = ''
  try {
    const r = await api.testLlm()
    testResults.llm = r
    msg.llm = r.ok ? `连接成功，模型回复：${r.reply}` : r.error
  } catch (e) {
    testResults.llm = { ok: false }
    msg.llm = e.message
  } finally {
    busy.llmTest = false
  }
}

async function saveImagegen() {
  busy.imagegen = true
  msg.imagegen = ''
  try {
    const body = {
      base_url: imagegenForm.base_url.trim(),
      model: imagegenForm.model.trim(),
      ...(imagegenForm.api_key.trim() ? { api_key: imagegenForm.api_key.trim() } : {}),
    }
    await api.saveImagegenConfig(body)
    imagegenForm.api_key = ''
    msg.imagegen = '已保存'
    await refreshAll()
  } catch (e) {
    msg.imagegen = e.message
  } finally {
    busy.imagegen = false
  }
}

async function testImagegen() {
  busy.imagegenTest = true
  msg.imagegen = ''
  try {
    const r = await api.testImagegen()
    testResults.imagegen = r
    msg.imagegen = r.ok ? '连接成功，已生成测试图' : r.error
  } catch (e) {
    testResults.imagegen = { ok: false }
    msg.imagegen = e.message
  } finally {
    busy.imagegenTest = false
  }
}

onMounted(refreshAll)
</script>

<style scoped>
.overview {
  display: flex; align-items: center; gap: 22px; flex-wrap: wrap;
  padding: 14px 20px;
}
.ov-item { display: flex; align-items: center; gap: 10px; }
.ov-text { display: flex; flex-direction: column; line-height: 1.4; }
.ov-text b { font-size: 13px; }
.ov-text span { font-size: 11.5px; color: var(--text-dim); }

.card-head { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.card-head h3 { margin: 0; font-size: 15px; }
.tag {
  margin-left: auto; font-size: 11.5px; padding: 2px 10px; border-radius: 99px;
  border: 1px solid var(--border);
}
.tag.ok { color: var(--green); border-color: var(--green); }
.tag.off { color: var(--text-dim); }

.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px 18px; }
.form-row { display: flex; align-items: center; gap: 10px; margin: 10px 0; }
.form-row label { width: 76px; font-size: 13px; color: var(--text-dim); flex-shrink: 0; }
.form-row input { flex: 1; }
.btn-row { display: flex; gap: 10px; margin-top: 6px; }
.form-msg { font-size: 12.5px; margin: 10px 0 0; }
.form-msg.error { color: var(--red); }
.test-preview { margin-top: 12px; }
.test-preview img {
  max-width: 260px; border-radius: 10px; border: 1px solid var(--border);
  display: block;
}
</style>
