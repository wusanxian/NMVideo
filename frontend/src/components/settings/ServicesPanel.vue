<template>
  <div>
    <!-- ComfyUI -->
    <div class="set-group">视频工作流 · ComfyUI</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">服务地址</div>
          <div class="set-row-desc">分镜选「ComfyUI 工作流」引擎时提交到这里。</div>
        </div>
        <div class="set-row-ctl wide">
          <span class="set-badge" :class="comfy.online ? 'on' : 'off'">
            {{ comfy.online ? '已连接' : '未连接' }}
          </span>
          <input class="set-input mono" v-model="comfyForm.comfy_url" placeholder="http://127.0.0.1:8188" />
          <button class="btn" :disabled="busy.comfy" @click="saveComfy">
            {{ busy.comfy ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">连接</div>
          <div class="set-row-desc">{{ comfy.note || (comfy.online ? 'ComfyUI 已响应，可以出片。' : '请先启动 ComfyUI（python main.py --listen）。') }}</div>
        </div>
        <div class="set-row-ctl">
          <button class="btn" :disabled="busy.refresh" @click="refreshAll">
            {{ busy.refresh ? '检测中…' : '重新检测' }}
          </button>
        </div>
      </div>
      <div class="set-hint">
        ComfyUI 由你自行启动，本产品只提交任务、不托管它的 Python 环境。桌面端可在「系统管理」里看启动状态。
      </div>
    </div>

    <!-- LLM -->
    <div class="set-group">LLM · AI 编剧 / 提示词优化</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">Base URL</div>
        </div>
        <div class="set-row-ctl wide">
          <input class="set-input mono" v-model="llmForm.base_url" placeholder="https://api.deepseek.com/v1" />
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">模型名</div>
        </div>
        <div class="set-row-ctl wide">
          <input class="set-input" v-model="llmForm.model" placeholder="deepseek-chat" />
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">API Key</div>
          <div class="set-row-desc">留空 = 保持原 Key 不变；支持 Ollama 这类免 Key 端点。</div>
        </div>
        <div class="set-row-ctl wide">
          <input class="set-input mono" v-model="llmForm.api_key" type="password" placeholder="sk-..." />
        </div>
      </div>
      <div class="set-actions">
        <button class="btn" :disabled="busy.llmTest" @click="testLlm">
          {{ busy.llmTest ? '测试中…' : '测试连接' }}
        </button>
        <button class="btn btn-primary" :disabled="busy.llm" @click="saveLlm">
          {{ busy.llm ? '保存中…' : '保存配置' }}
        </button>
      </div>
      <div v-if="msg.llm" class="set-msg" :class="testResults.llm.ok ? 'ok' : 'err'">{{ msg.llm }}</div>
      <div class="set-foot">用途：剧本页「AI 编剧」、分镜编辑「优化提示词」、台词润色</div>
    </div>

    <!-- 图像生成 -->
    <div class="set-group">图像生成 · 参考图 / 概念图 / 首帧</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">Base URL</div>
          <div class="set-row-desc">任意 OpenAI 兼容 <code>/images/generations</code> 端点，返回 URL 或 b64 都行。</div>
        </div>
        <div class="set-row-ctl wide">
          <input class="set-input mono" v-model="imagegenForm.base_url" placeholder="https://api.openai.com/v1" />
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">模型名</div>
        </div>
        <div class="set-row-ctl wide">
          <input class="set-input" v-model="imagegenForm.model" placeholder="dall-e-3 / gpt-image-1" />
        </div>
      </div>
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">API Key</div>
          <div class="set-row-desc">留空 = 保持原 Key 不变。</div>
        </div>
        <div class="set-row-ctl wide">
          <input class="set-input mono" v-model="imagegenForm.api_key" type="password" placeholder="sk-..." />
        </div>
      </div>
      <div class="set-actions">
        <button class="btn" :disabled="busy.imagegenTest" @click="testImagegen">
          {{ busy.imagegenTest ? '测试中…' : '测试连接' }}
        </button>
        <button class="btn btn-primary" :disabled="busy.imagegen" @click="saveImagegen">
          {{ busy.imagegen ? '保存中…' : '保存配置' }}
        </button>
      </div>
      <div v-if="msg.imagegen" class="set-msg" :class="testResults.imagegen.ok ? 'ok' : 'err'">{{ msg.imagegen }}</div>
      <div v-if="testResults.imagegen.ok && testResults.imagegen.url" class="test-preview">
        <img :src="testResults.imagegen.url" alt="测试图" />
      </div>
      <div class="set-foot">未配置也能用：可以直接上传参考图，不影响出片</div>
    </div>

    <!-- TTS -->
    <div class="set-group">TTS · 配音</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">语音合成</div>
          <div class="set-row-desc">
            无需配置：联网用 edge-tts 神经音色（14 个中文音色），离线自动切 Windows SAPI5。
            角色卡可绑固定音色，全片声音一致。
          </div>
        </div>
        <div class="set-row-ctl">
          <span class="set-badge" :class="tts.online ? 'on' : 'warn'">{{ tts.online ? '可用' : '不可用' }}</span>
        </div>
      </div>
      <div v-if="tts.note" class="set-hint">{{ tts.note }}</div>
    </div>

    <!-- MCP -->
    <div class="set-group">AI 助手接入 · MCP</div>
    <div class="set-card">
      <div class="set-row">
        <div class="set-row-main">
          <div class="set-row-title">开启连接端点</div>
          <div class="set-row-desc">开启后可在 AI 助手里直接说「生成一段 8 秒视频」，助手自己查状态、出片、汇报。</div>
        </div>
        <div class="set-row-ctl">
          <span class="set-badge" :class="mcpState.enabled ? 'on' : 'off'">{{ mcpState.enabled ? '已开启' : '未开启' }}</span>
          <button class="set-switch" :class="{ on: mcpState.enabled }" :disabled="busy.mcp"
                  role="switch" :aria-checked="mcpState.enabled" @click="toggleMcp"></button>
        </div>
      </div>
      <template v-if="mcpState.enabled">
        <div class="set-row">
          <div class="set-row-main">
            <div class="set-row-title">连接地址</div>
            <div class="set-row-desc">带令牌鉴权，仅本机可访问。</div>
          </div>
          <div class="set-row-ctl wide">
            <input class="set-input mono" readonly :value="mcpUrl" @focus="$event.target.select()" />
            <button class="btn" @click="copyMcpUrl">{{ copied ? '已复制 ✓' : '复制' }}</button>
          </div>
        </div>
        <div class="set-row stack">
          <details class="mcp-help">
            <summary>接入方法（点开看示例）</summary>
            <p class="set-row-desc">WorkBuddy：连接器管理 → 自定义 MCP → 粘贴：</p>
            <pre class="mcp-code">{ "mcpServers": { "nmvideo": { "url": "{{ mcpUrl }}" } } }</pre>
            <p class="set-row-desc">Claude Code 命令行：</p>
            <pre class="mcp-code">claude mcp add --transport http nmvideo "{{ mcpUrl }}"</pre>
          </details>
        </div>
        <div class="set-actions">
          <button class="btn" :disabled="busy.mcp" @click="regenMcp">换新令牌</button>
        </div>
      </template>
      <div v-else class="set-hint">
        未开启时不会占用端口；开启后本机 {{ mcpBase }}/mcp 提供带令牌鉴权的接口。
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../../api'

const status = ref({ comfyui: {}, llm: {}, imagegen: {}, tts: {} })
const busy = reactive({ comfy: false, refresh: false, llm: false, imagegen: false, llmTest: false, imagegenTest: false, mcp: false })
const msg = reactive({ llm: '', imagegen: '' })
const testResults = reactive({ llm: { ok: false }, imagegen: { ok: false } })

const mcpState = ref({ enabled: false, token_masked: '', has_token: false })
const mcpToken = ref('')
const copied = ref(false)
const mcpBase = computed(() => window.location.origin)
const mcpUrl = computed(() => (mcpToken.value ? `${mcpBase.value}/mcp?token=${mcpToken.value}` : `${mcpBase.value}/mcp`))

const comfyForm = reactive({ comfy_url: '' })
const llmForm = reactive({ base_url: '', api_key: '', model: '' })
const imagegenForm = reactive({ base_url: '', api_key: '', model: '' })

const comfy = computed(() => status.value.comfyui || {})
const tts = computed(() => status.value.tts || {})

async function refreshAll() {
  busy.refresh = true
  try {
    status.value = await api.servicesStatus()
  } catch {
    /* 后端离线时保持空状态 */
  } finally {
    busy.refresh = false
  }
  try {
    const c = await api.servicesConfig()
    comfyForm.comfy_url = c.comfy_url || (c.comfy && c.comfy.url) || ''
    // api_key 打码返回：留空表示不修改
    llmForm.base_url = (c.llm && c.llm.base_url) || ''
    llmForm.api_key = ''
    llmForm.model = (c.llm && c.llm.model) || ''
    imagegenForm.base_url = (c.imagegen && c.imagegen.base_url) || ''
    imagegenForm.api_key = ''
    imagegenForm.model = (c.imagegen && c.imagegen.model) || ''
  } catch {
    /* ignore */
  }
  try {
    mcpState.value = await api.mcpState()
    if (mcpState.value.enabled) {
      const r = await api.mcpReveal()
      mcpToken.value = r.token || ''
    } else {
      mcpToken.value = ''
    }
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

async function toggleMcp() {
  busy.mcp = true
  try {
    await api.saveMcpConfig({ enabled: !mcpState.value.enabled })
    await refreshAll()
  } catch (e) {
    alert(e.message)
  } finally {
    busy.mcp = false
  }
}

async function regenMcp() {
  busy.mcp = true
  try {
    await api.saveMcpConfig({ enabled: true, regenerate: true })
    await refreshAll()
  } catch (e) {
    alert(e.message)
  } finally {
    busy.mcp = false
  }
}

async function copyMcpUrl() {
  try {
    await navigator.clipboard.writeText(mcpUrl.value)
  } catch {
    /* 剪贴板权限被拒时退化为选中文本让用户 Ctrl+C */
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
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
    testResults.llm = { ok: true }
    msg.llm = '已保存'
    await refreshAll()
  } catch (e) {
    testResults.llm = { ok: false }
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
    testResults.imagegen = { ok: true }
    msg.imagegen = '已保存'
    await refreshAll()
  } catch (e) {
    testResults.imagegen = { ok: false }
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
.test-preview { padding: 0 18px 14px; }
.test-preview img {
  max-width: 260px; border-radius: 10px; border: 1px solid var(--border);
  display: block;
}
.mcp-help summary { cursor: pointer; font-size: 12.5px; color: var(--text-dim); }
.mcp-code {
  font-size: 12px; font-family: ui-monospace, monospace; padding: 9px 12px; margin: 6px 0 10px;
  border: 1px solid var(--border); border-radius: 8px; background: var(--bg);
  white-space: pre-wrap; word-break: break-all; user-select: all;
}
code {
  background: var(--bg); border: 1px solid var(--border); border-radius: 5px;
  padding: 1px 5px; font-size: 12px; font-family: ui-monospace, monospace;
}
</style>
