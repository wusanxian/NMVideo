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

        <!-- 动作与构图（PR-10 后半：跨镜头连续性约束，生成时拼入提示词） -->
        <section class="form-section">
          <h3>动作与构图（生成时自动拼入提示词）</h3>
          <div class="field">
            <label>动作模板</label>
            <select v-model="form.motion_template_id" @change="onTemplateChange">
              <option value="">无（自由发挥）</option>
              <option v-for="t in motionTemplates" :key="t.id" :value="t.id">
                {{ t.name }}{{ t.is_builtin ? '' : '（自定义）' }}
              </option>
            </select>
            <p v-if="templateAction" class="hint-text">动作描述：{{ templateAction }}</p>
          </div>
          <div v-if="boundCharacters.length" class="field">
            <label>人物站位与朝向（可选；仅对已关联角色生效）</label>
            <div v-for="c in boundCharacters" :key="c.id" class="comp-row">
              <span class="comp-name">
                <i class="char-dot" :style="{ background: charColor(c.name) }"></i>{{ c.name }}
              </span>
              <select v-model="compPositions[c.id]" @change="syncComposition" aria-label="站位">
                <option value="">站位不限</option>
                <option value="left">画面左侧</option>
                <option value="center">画面中央</option>
                <option value="right">画面右侧</option>
              </select>
              <select v-model="compFacings[c.id]" @change="syncComposition" aria-label="朝向">
                <option value="">朝向不限</option>
                <option value="toward_camera">面向镜头</option>
                <option value="back_to_camera">背对镜头</option>
                <option value="toward_left">面向画面左侧</option>
                <option value="toward_right">面向画面右侧</option>
                <option value="profile">侧身对镜头</option>
              </select>
            </div>
          </div>
          <p v-else class="hint-text">关联角色后可为每人物设置站位与朝向约束（双人对手戏左右关系保持的关键）。</p>
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
                 :class="{ active: form.mode === m.id, disabled: !modeSupported(m.id) }"
                 :aria-disabled="!modeSupported(m.id)"
                 @click="modeSupported(m.id) && (form.mode = m.id)">
              <div class="mc-name">{{ m.name }}</div>
              <div class="mc-desc">{{ m.desc }}</div>
            </div>
          </div>
          <p v-if="!modeSupported(form.mode)" class="comfy-hint">当前引擎不支持此模式，请选择 ComfyUI 或切换到文生视频。</p>

          <!-- 图生视频：参考图/首帧（沿用既有单图链路） -->
          <div v-if="form.mode === 'i2v'" class="field" style="margin-top: 12px">
            <label>参考图 / 首帧（需要工作流支持图片条件）</label>
            <div class="ref-input-row">
              <input type="text" v-model="form.reference_image" placeholder="/outputs/xxx.png 或 http://..." />
              <label class="btn upload-btn">
                上传
                <input type="file" accept="image/*" hidden @change="onFrameUpload($event, 'reference_image')" />
              </label>
            </div>
            <!-- PR-08 后半：多参考图（额外参考，最多 4 张；工作流需映射对应槽位，
                 不支持时生成会明确提示而不是忽略） -->
            <div v-for="(ref, i) in form.reference_images" :key="i" class="ref-input-row" style="margin-top: 6px">
              <input type="text" v-model="form.reference_images[i]" :placeholder="`参考图 ${i + 2}`" />
              <label class="btn upload-btn">
                上传
                <input type="file" accept="image/*" hidden @change="onMultiRefUpload($event, i)" />
              </label>
              <button type="button" class="btn" @click="form.reference_images.splice(i, 1)">移除</button>
            </div>
            <button
              v-if="form.reference_images.length < 4"
              type="button" class="btn" style="margin-top: 6px" @click="form.reference_images.push('')">
              + 添加参考图（{{ form.reference_images.length }}/4）
            </button>
            <p v-else class="comfy-hint">已达多参考图上限（4 张）。实际可用数量取决于工作流映射的 reference_image_N 槽位。</p>
          </div>

          <!-- 首尾帧（PR-02）：首帧与尾帧分别独立输入，分别注入工作流首/尾帧节点 -->
          <div v-if="form.mode === 'first_last'" class="field" style="margin-top: 12px">
            <label>首帧图片（本地引擎仅记录，实际首尾帧走 ComfyUI 工作流）</label>
            <div class="ref-input-row">
              <input type="text" v-model="form.first_frame_image" placeholder="/outputs/first.png 或 http://..." />
              <label class="btn upload-btn">
                上传
                <input type="file" accept="image/*" hidden @change="onFrameUpload($event, 'first_frame_image')" />
              </label>
            </div>
          </div>
          <div v-if="form.mode === 'first_last'" class="field" style="margin-top: 12px">
            <label>尾帧图片（工作流需映射尾帧节点；未映射时生成会明确提示而不是忽略）</label>
            <div class="ref-input-row">
              <input type="text" v-model="form.last_frame_image" placeholder="/outputs/last.png 或 http://..." />
              <label class="btn upload-btn">
                上传
                <input type="file" accept="image/*" hidden @change="onFrameUpload($event, 'last_frame_image')" />
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
              请使用支持图片条件的 ComfyUI 工作流；本地文生视频引擎不支持此模式。
            </p>
          </div>
        </section>

        <!-- 相邻镜头衔接预览（PR-04 导演工作台） -->
        <section class="form-section">
          <h3>相邻镜头衔接</h3>
          <p v-if="shot.mode === 'continuation' && shot.inheritance_stale"
             class="hint-text" style="color: #f87171">
            ⚠ 上游镜头已重新生成了新版本，本镜头的继承帧已过期——建议重新生成或更换续接来源
          </p>
          <div v-if="adjacentShots.length" class="adjacent-grid">
            <div v-for="a in adjacentShots" :key="a.pos" class="adjacent-cell">
              <div class="adj-title">
                {{ a.pos === 'prev' ? '⬆ 上一镜头' : '⬇ 下一镜头' }}
                <b>#{{ a.shot.order_index + 1 }} {{ a.shot.title || '未命名' }}</b>
              </div>
              <video v-if="a.shot.current_version && a.shot.current_version.video_url"
                     :src="a.shot.current_version.video_url" controls muted
                     preload="metadata"></video>
              <p v-else class="hint-text">尚无采用版本</p>
            </div>
          </div>
          <p v-else class="hint-text">本镜头是场景内第一个/最后一个镜头，无相邻镜头</p>
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
              <select v-model="ttsCharacter" aria-label="对白说话人" @change="selectSpeaker">
                <option value="">旁白 / 自选音色</option>
                <option v-for="c in characters" :key="c.id" :value="c.id">{{ c.name }}</option>
              </select>
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
                {{ ttsBusy ? '生成中…' : '🎙 添加一句配音' }}
              </button>
            </div>
            <div class="field" style="margin-top: 8px">
              <label>本句配音文本（同镜头按添加顺序播放；重录后删除旧条目）</label>
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
              <div class="ec-name">
                <span class="dot" :class="e.status"></span>{{ e.name }}
                <span v-if="e.is_default" class="ec-badge">默认</span>
                <span v-else-if="e.execution_channel === 'native'" class="ec-badge fallback">兜底</span>
              </div>
              <div class="ec-desc">{{ e.description }}</div>
              <div v-if="e.note" class="ec-note">⚠ {{ e.note }}</div>
            </div>
          </div>

          <div v-if="genForm.engine !== 'anime'" class="chips" style="margin-top: 12px">
            <span v-for="q in presets" :key="q.id" class="chip"
                  :class="{ active: genForm.preset === q.id }" @click="applyPreset(q)">{{ q.name }}</span>
          </div>
          <label v-if="genForm.engine !== 'anime'" class="check-line">
            <input type="checkbox" v-model="genForm.preview" />
            先生成预览，满意后在画廊生成高清候选（FreeVideo 可继续二采）
          </label>

          <div class="param-grid" style="margin-top: 12px">
            <div class="field">
              <div class="range-label"><span>帧数</span><b>{{ genForm.num_frames }}</b></div>
              <input type="range" min="8" :max="['wan22','h3','h3_native'].includes(genForm.engine) ? 121 : 32" step="2"
                     v-model.number="genForm.num_frames" />
            </div>
            <div class="field">
              <div class="range-label"><span>帧率 FPS</span><b>{{ genForm.fps }}</b></div>
              <input type="range" min="4" :max="['wan22','h3','h3_native'].includes(genForm.engine) ? 24 : 16" step="1"
                     v-model.number="genForm.fps" />
            </div>
            <div class="field">
              <div class="range-label"><span>步数</span><b>{{ genForm.steps }}</b></div>
              <input type="range" min="10" max="50" step="1" v-model.number="genForm.steps" />
            </div>
            <div class="field">
              <div class="range-label"><span>CFG</span><b>{{ genForm.guidance }}</b></div>
              <input type="range" min="1" :max="['wan22','h3','h3_native'].includes(genForm.engine) ? 10 : 15" step="0.5"
                     v-model.number="genForm.guidance" />
            </div>
            <div class="field">
              <label>宽度</label>
              <input type="number" v-model.number="genForm.width" min="256" :max="['wan22','h3','h3_native'].includes(genForm.engine) ? 1344 : 1024" step="16" />
            </div>
            <div class="field">
              <label>高度</label>
              <input type="number" v-model.number="genForm.height" min="256" :max="['wan22','h3','h3_native'].includes(genForm.engine) ? 768 : 1024" step="16" />
            </div>
            <div class="field">
              <label>随机种子（-1 随机）</label>
              <input type="number" v-model.number="genForm.seed" min="-1" />
            </div>
          </div>
          <p v-if="genForm.engine === 'h3'" class="comfy-hint">
            走 <b>ComfyUI 通道</b>：按模式自动套用内置 <b>freevideo_h3_*</b> 预设并固化快照。
            宽高按 32 对齐、帧数吸附到 17n+5；步数与 CFG 由工作流节点决定（8 步蒸馏图通常固定 8 步）。
            ComfyUI 侧缺 FreeVideo 节点时这张卡会标记不可用。
          </p>
          <p v-else-if="genForm.engine === 'h3_native'" class="comfy-hint">
            原生兜底通道（进程内推理）：H3 是 guidance 蒸馏模型，<b>固定 8 步、无 CFG</b>，
            上方「步数 / CFG」两项不生效；模型最少出 5 秒画面，更短的镜头会自动截取到设定时长。
            宽高按 32 对齐，帧数自动吸附到 17n+5。
          </p>
          <p v-if="genForm.engine === 'comfyui'" class="comfy-hint">
            将按 <b>ComfyUI 工作流模板</b> 的节点映射注入以上参数与参考图；
            模板可在「ComfyUI 设置」中粘贴你自己导出的工作流。
          </p>
          <div v-if="genForm.engine === 'comfyui'" class="field">
            <label><input type="checkbox" v-model="genForm.use_character_images" /> 本次使用角色参考图（FreeVideo 首尾帧/图生模式需关闭）</label>
            <label><input type="checkbox" v-model="genForm.use_character_lora" /> 本次使用角色 LoRA</label>
            <p class="hint-text">自动角色视觉条件仅在 FreeVideo 本地预设中生效；其他模板需自行配置。</p>
          </div>
        </section>

        <!-- 版本 -->
        <section v-if="shot.versions && shot.versions.length" class="form-section">
          <h3>候选版本（{{ shot.versions.length }}）</h3>
          <!-- A/B 对比区（PR-04：候选版本对比） -->
          <div v-if="compareIds.length === 2" class="compare-area">
            <div class="compare-head">
              <b>版本对比</b>
              <button class="btn" @click="compareIds = []">退出对比</button>
            </div>
            <div class="compare-grid">
              <div v-for="vid in compareIds" :key="vid" class="compare-cell">
                <video v-if="versionById(vid) && versionById(vid).video_url"
                       :src="versionById(vid).video_url" controls muted
                       preload="metadata"></video>
                <div class="compare-label">
                  v{{ versionById(vid) ? versionById(vid).version_no : '?' }}
                  <span v-if="vid === shot.current_version_id" class="badge done">采用中</span>
                </div>
              </div>
            </div>
          </div>
          <div class="version-list">
            <div v-for="v in shot.versions" :key="v.id" class="version-item"
                 :class="{ current: v.id === shot.current_version_id, comparing: compareIds.includes(v.id) }">
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
                <div v-if="v.characters && v.characters.length" class="v-chars">
                  🔒 角色快照：{{ v.characters.map(c => c.name + (c.outfit ? `（${c.outfit}）` : '')).join('、') }}
                </div>
                <div class="v-actions">
                  <button v-if="v.id !== shot.current_version_id" class="btn"
                          @click="$emit('adopt', v.id)">采用此版本</button>
                  <button class="btn" :class="{ active: compareIds.includes(v.id) }"
                          @click="toggleCompare(v.id)">
                    {{ compareIds.includes(v.id) ? '✓ 已选对比' : '对比' }}
                  </button>
                  <button class="btn danger" @click="$emit('delete-version', v.id)">删除</button>
                </div>
              </div>
            </div>
          </div>
          <p v-if="compareIds.length === 1" class="hint-text">已选 1 个版本，再选一个即可并排对比</p>
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
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps({
  shot: { type: Object, required: true },
  engines: { type: Array, default: () => [] },
  presets: { type: Array, default: () => [] },
  characters: { type: Array, default: () => [] },
  siblingShots: { type: Array, default: () => [] },
  projectId: { type: String, default: '' },
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

function _parseRefList(value) {
  if (Array.isArray(value)) return value.filter((v) => typeof v === 'string')
  if (typeof value === 'string' && value) {
    try {
      const arr = JSON.parse(value)
      return Array.isArray(arr) ? arr.filter((v) => typeof v === 'string') : []
    } catch { /* 旧数据脏值按空处理 */ }
  }
  return []
}

const form = reactive({
  title: props.shot.title || '',
  dialogue: props.shot.dialogue || '',
  shot_size: props.shot.shot_size || '中景',
  camera_movement: props.shot.camera_movement || '固定',
  duration_s: props.shot.duration_s || 3,
  reference_image: props.shot.reference_image || '',
  // PR-08 后半：多参考图（shot 存 JSON 字符串，编辑态转数组；保存时数组原样
  // 提交，后端 ShotUpdate 会 json.dumps 存回）
  reference_images: _parseRefList(props.shot.reference_images),
  // PR-02：独立首/尾帧。旧 first_last 只有一张参考图 → 预填为首帧，用户可见可改
  first_frame_image: props.shot.first_frame_image
    || (props.shot.mode === 'first_last' ? (props.shot.reference_image || '') : ''),
  last_frame_image: props.shot.last_frame_image || '',
  prompt: props.shot.prompt || '',
  negative_prompt: props.shot.negative_prompt || '',
  character_ids: [...(props.shot.character_ids || [])],
  mode: props.shot.mode || 't2v',
  continue_from: props.shot.continue_from || '',
  // PR-10 后半：动作模板 + 构图约束（编辑态拆成站位/朝向映射，保存时合成 JSON）
  motion_template_id: props.shot.motion_template_id || '',
  composition: {},
})

// ---- PR-10 后半：动作模板 + 构图约束 ----
const motionTemplates = ref([])
const compPositions = reactive({})
const compFacings = reactive({})

function _parseComposition(value) {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value
  if (typeof value === 'string' && value) {
    try {
      const obj = JSON.parse(value)
      return obj && typeof obj === 'object' && !Array.isArray(obj) ? obj : {}
    } catch { /* 脏值按空处理 */ }
  }
  return {}
}

const shotComposition = _parseComposition(props.shot.composition)
for (const [cid, pos] of Object.entries(shotComposition.character_positions || {})) compPositions[cid] = pos
for (const [cid, face] of Object.entries(shotComposition.character_facing || {})) compFacings[cid] = face

const boundCharacters = computed(() =>
  (props.characters || []).filter((c) => form.character_ids.includes(c.id)))

const templateAction = computed(() => {
  const t = motionTemplates.value.find((x) => x.id === form.motion_template_id)
  return t?.action_prompt || ''
})

function onTemplateChange() {
  // 选模板联动默认镜头运动（仍可手动改；固定不动）
  const t = motionTemplates.value.find((x) => x.id === form.motion_template_id)
  if (t?.default_camera_movement && t.default_camera_movement !== '固定') {
    form.camera_movement = t.default_camera_movement
  }
}

function syncComposition() {
  const positions = {}
  const facings = {}
  for (const c of boundCharacters.value) {
    if (compPositions[c.id]) positions[c.id] = compPositions[c.id]
    if (compFacings[c.id]) facings[c.id] = compFacings[c.id]
  }
  form.composition = {}
  if (Object.keys(positions).length) form.composition.character_positions = positions
  if (Object.keys(facings).length) form.composition.character_facing = facings
}

const genForm = reactive({
  // 默认走 ComfyUI 通道；不可用时由下方 watch 自动降级到兜底引擎
  engine: 'comfyui',
  width: 512, height: 512,
  num_frames: 16, fps: 8, steps: 25, guidance: 7.5,
  seed: -1, preset: '', preview: false,
  use_character_images: true, use_character_lora: true,
})

// 默认引擎是 ComfyUI（唯一视频生成底座）；ComfyUI 未连接时自动降级到第一个
// 可用引擎（anime/wan22/h3 属兜底通道），别让用户对着不可用卡片发呆。
watch(() => props.engines, (list) => {
  if (!list || !list.length) return
  const current = list.find((e) => e.id === genForm.engine)
  if (!current || current.status !== 'ready') {
    const usable = list.find((e) => e.status === 'ready')
    if (usable) genForm.engine = usable.id
  }
}, { immediate: true })

function modeSupported(mode) {
  const engine = props.engines.find((item) => item.id === genForm.engine)
  const modes = engine?.supported_modes || (genForm.engine === 'comfyui'
    ? ['t2v', 'i2v', 'first_last', 'continuation'] : ['t2v'])
  return modes.includes(mode)
}

const canGenerate = computed(() => form.prompt.trim().length > 0 && modeSupported(form.mode))

// ---- PR-04 导演工作台：相邻镜头衔接 + 候选版本对比 ----
const adjacentShots = computed(() => {
  const list = props.siblingShots || []
  const i = list.findIndex((s) => s.id === props.shot.id)
  const out = []
  if (i > 0) out.push({ pos: 'prev', shot: list[i - 1] })
  if (i >= 0 && i < list.length - 1) out.push({ pos: 'next', shot: list[i + 1] })
  return out
})

const compareIds = ref([])
const versionById = (vid) => (props.shot.versions || []).find((v) => v.id === vid) || null

function toggleCompare(vid) {
  const i = compareIds.value.indexOf(vid)
  if (i >= 0) {
    compareIds.value.splice(i, 1)
  } else {
    compareIds.value.push(vid)
    if (compareIds.value.length > 2) compareIds.value.shift()
  }
}

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
const ttsCharacter = ref('')
function selectSpeaker() {
  const character = props.characters.find(c => c.id === ttsCharacter.value)
  ttsVoice.value = character?.voice_id || ''
}
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
  // PR-10 后半：动作模板列表（内置 + 本项目自定义）
  try {
    const r = await api.motionTemplates(props.projectId)
    motionTemplates.value = r.templates || []
  } catch { /* 拉取失败不阻塞编辑，仅无模板可选 */ }
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

async function onFrameUpload(e, field) {
  const file = e.target.files?.[0]
  if (!file) return
  try {
    const r = await api.uploadFile(file)
    form[field] = r.url
  } catch (err) {
    errMsg.value = err.message
  }
  e.target.value = ''
}

async function onMultiRefUpload(e, idx) {
  const file = e.target.files?.[0]
  if (!file) return
  try {
    const r = await api.uploadFile(file)
    form.reference_images[idx] = r.url
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
      text: ttsText.value, voice: ttsVoice.value, rate: ttsRate.value, character_id: ttsCharacter.value,
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
  syncComposition()
  emit('saved', { ...form })
}

function generate() {
  if (!form.prompt.trim()) {
    errMsg.value = '请先填写提示词'
    return
  }
  errMsg.value = ''
  syncComposition()
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

/* ---- PR-10 后半：动作与构图 ---- */
.comp-row {
  display: flex; align-items: center; gap: 8px; margin-bottom: 8px;
}
.comp-name {
  display: flex; align-items: center; gap: 6px; font-size: 12.5px;
  color: var(--text); flex: 0 0 96px; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.comp-row select {
  flex: 1; min-width: 0; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 8px; color: var(--text); padding: 7px 9px; font-size: 12.5px;
  font-family: inherit;
}
.comp-row select:focus { border-color: var(--accent); outline: none; }

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
/* ---- PR-04 相邻衔接与版本对比 ---- */
.adjacent-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
.adjacent-cell { border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; background: var(--bg-card); }
.adjacent-cell video { width: 100%; border-radius: 8px; background: #000; }
.adj-title { font-size: 12.5px; margin-bottom: 6px; color: var(--text-dim); }
.compare-area { border: 1px solid var(--accent); border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; }
.compare-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.compare-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.compare-cell video { width: 100%; border-radius: 8px; background: #000; }
.compare-label { font-size: 12.5px; margin-top: 4px; display: flex; gap: 6px; align-items: center; }
.version-item.comparing { border-color: var(--accent); }
.v-chars { font-size: 12px; color: var(--text-dim); }
.btn.active { border-color: var(--accent); color: var(--accent); }
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
