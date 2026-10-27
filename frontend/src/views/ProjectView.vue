<template>
  <div class="page" v-if="project">
    <header class="page-head">
      <div>
        <div class="crumb">
          <router-link to="/projects">← 分镜工作台</router-link>
          <input class="proj-name" :value="project.name" @change="renameProject" />
        </div>
        <div class="page-sub">
          {{ project.description || '暂无简介' }} ·
          共 {{ totalShots }} 个分镜，已通过 {{ approvedShots }} 个
        </div>
      </div>
      <div class="head-actions">
        <span class="comfy-pill" :class="comfy.online ? 'on' : 'off'" :title="comfy.url">
          <span class="dot" :class="comfy.online ? 'ready' : 'unavailable'"></span>
          ComfyUI {{ comfy.online ? '已连接' : '未连接' }}
        </span>
        <router-link to="/comfy" class="btn">⚙ ComfyUI 设置</router-link>
      </div>
    </header>

    <!-- 标签页 -->
    <div class="tabs">
      <button :class="{ active: tab === 'shots' }" @click="tab = 'shots'">🎬 分镜</button>
      <button :class="{ active: tab === 'assets' }" @click="tab = 'assets'">🎭 资产库</button>
      <button :class="{ active: tab === 'script' }" @click="tab = 'script'">📜 剧本</button>
      <button :class="{ active: tab === 'timeline' }" @click="tab = 'timeline'">⏱ 时间线</button>
      <button :class="{ active: tab === 'history' }" @click="tab = 'history'">📋 历史</button>
    </div>

    <p v-if="message" class="form-msg banner">{{ message }}</p>

    <!-- ============ 分镜页 ============ -->
    <template v-if="tab === 'shots'">
      <div v-for="scene in project.scenes" :key="scene.id" class="panel scene-block">
        <div class="scene-head">
          <input class="scene-title-input" :value="scene.title"
                 @change="store.saveScene(scene.id, { title: $event.target.value })" />
          <span class="scene-count">{{ scene.shots.length }} 镜</span>
          <div style="margin-left: auto; display: flex; gap: 8px">
            <button class="btn" @click="addShot(scene.id)">＋ 添加分镜</button>
            <button class="btn danger" @click="removeScene(scene.id)">删除场景</button>
          </div>
        </div>
        <div v-if="scene.description || scene.environment || scene.lighting" class="scene-desc">
          <span v-if="scene.environment">环境：{{ scene.environment }}</span>
          <span v-if="scene.lighting">光照：{{ scene.lighting }}</span>
          <span v-if="scene.description">{{ scene.description }}</span>
        </div>
        <div class="shot-grid" @drop="onSceneDrop(scene, $event)" @dragover.prevent @dragend="dragId = ''">
          <ShotCard
            v-for="(shot, i) in scene.shots"
            :key="shot.id"
            :shot="shot"
            :index="i"
            :is-last="i === scene.shots.length - 1"
            :draggable="!!dragId"
            :job="jobOf(shot)"
            @edit="openEditor(shot)"
            @versions="openEditor(shot)"
            @generate="quickGenerate(shot)"
            @review="openReview(shot, $event)"
            @review-design="openReviewDesign(shot)"
            @rework="openRework(shot)"
            @delete="removeShot(shot.id)"
            @move-up="moveShot(scene, i, i - 1)"
            @move-down="moveShot(scene, i, i + 1)"
            @drag-start="dragId = shot.id"
            @drag-end="dragId = ''"
            @drop="onShotDrop(scene, i, $event)"
          />
          <div v-if="!scene.shots.length" class="empty">这个场景还没有分镜，点「添加分镜」或去「剧本」页导入</div>
        </div>
      </div>

      <button class="btn add-scene" @click="store.addScene()">＋ 新建场景</button>
    </template>

    <!-- ============ 资产库页 ============ -->
    <template v-if="tab === 'assets'">
      <div class="panel">
        <div class="tl-head">
          <h3 style="margin: 0">角色身份卡（{{ characters.length }}）</h3>
          <button class="btn btn-primary" @click="editingCharacter = {}">＋ 新建角色</button>
        </div>
        <p class="hint">
          角色卡保存外貌/服装/参考图/固定音色/LoRA。分镜绑定角色后，生成时自动拼入描述，
          解决跨镜头「换脸换装」问题；配音时自动使用角色绑定音色。
        </p>
        <div v-if="characters.length" class="char-grid">
          <div v-for="c in characters" :key="c.id" class="char-card">
            <div class="cc-head">
              <i class="char-dot" :style="{ background: charColor(c.name) }"></i>
              <b>{{ c.name }}</b>
              <span class="badge">{{ c.role }}</span>
              <div class="cc-actions">
                <button class="icon-btn" title="编辑" @click="editingCharacter = c">✏️</button>
                <button class="icon-btn danger" title="删除" @click="removeCharacter(c)">✕</button>
              </div>
            </div>
            <div class="cc-refs">
              <img v-for="(r, i) in c.reference_images" :key="i" :src="r.url" :title="r.type" />
              <span v-if="!c.reference_images.length" class="cc-noref">无参考图</span>
            </div>
            <div class="cc-body">
              <div v-if="c.appearance" class="cc-line" :title="c.appearance">外貌：{{ c.appearance }}</div>
              <div v-if="c.outfit" class="cc-line">服装：{{ c.outfit }}</div>
              <div v-if="c.voice_name" class="cc-line">🎙 {{ c.voice_name }}</div>
              <div v-if="c.lora" class="cc-line">LoRA：{{ c.lora }}</div>
              <div v-if="c.expressions && c.expressions.length" class="cc-line">
                表情：{{ c.expressions.map((e) => e.name).filter(Boolean).join('、') }}
              </div>
            </div>
          </div>
        </div>
        <div v-else class="empty">还没有角色。点「新建角色」给主角建第一张身份卡。</div>
      </div>

      <div class="panel">
        <h3>场景资产</h3>
        <p class="hint">保存环境、光照、布局与风格；生成该场景下的镜头时可作氛围参考。</p>
        <div v-for="scene in project.scenes" :key="scene.id" class="scene-asset">
          <div class="sa-title">🎬 {{ scene.title }}</div>
          <div class="sa-grid">
            <input :value="scene.environment" placeholder="环境，如：霓虹雨夜街道"
                   @change="store.saveScene(scene.id, { environment: $event.target.value })" />
            <input :value="scene.lighting" placeholder="光照，如：冷色霓虹 + 湿地反光"
                   @change="store.saveScene(scene.id, { lighting: $event.target.value })" />
            <input :value="scene.layout" placeholder="布局，如：长街纵深，左侧旅馆"
                   @change="store.saveScene(scene.id, { layout: $event.target.value })" />
            <input :value="scene.style" placeholder="风格，如：赛博朋克动漫"
                   @change="store.saveScene(scene.id, { style: $event.target.value })" />
          </div>
        </div>
        <div v-if="!project.scenes.length" class="empty">还没有场景。</div>
      </div>
    </template>

    <!-- ============ 剧本页 ============ -->
    <template v-if="tab === 'script'">
      <div class="panel">
        <h3>剧本文本</h3>
        <p class="hint">
          支持宽松格式：<br>
          · 用「<b>场景一：雨夜街道</b>」「<b>第2场：密室</b>」或「===」分隔场景<br>
          · 场景内每条非空行 = 一个镜头<br>
          · 「<b>主角：台词内容</b>」自动识别人物对白<br>
          · 行内「远景 / 推 / 3秒」自动提取景别、镜头运动与时长
        </p>
        <textarea v-model="scriptText" rows="16" class="script-area"
                  placeholder="场景一：雨夜街道&#10;主角独自走在霓虹灯下的长街，雨水打湿肩头 (全景, 移, 4秒)&#10;主角：这天要变了。&#10;&#10;场景二：密室&#10;烛光摇曳，主角推开木门 (远景, 推, 3秒)"></textarea>
        <div class="script-actions">
          <button class="btn" :disabled="savingScript" @click="saveScript">💾 保存剧本</button>
          <button class="btn" @click="aiWriterOpen = true">🧠 AI 编剧</button>
          <button class="btn btn-primary" :disabled="importing" @click="doImport('rebuild')">
            ✨ AI 拆分（覆盖重建）
          </button>
          <button class="btn" :disabled="importing" @click="doImport('append')">
            ➕ 追加导入（保留已有）
          </button>
        </div>
      </div>

      <!-- AI 编剧弹窗 -->
      <div v-if="aiWriterOpen" class="modal-mask" @click.self="aiWriterOpen = false">
        <div class="modal">
          <header class="modal-head">
            <h3>🧠 AI 编剧</h3>
            <button class="btn" @click="aiWriterOpen = false">✕</button>
          </header>
          <p class="hint">
            用 LLM 按标准剧本格式生成初稿（场景 / 镜头 / 对白 / 景别 / 运动 / 时长），
            生成后可继续编辑再「AI 拆分」。需先在「模型服务」页配置 LLM。
          </p>
          <div class="ai-form">
            <div class="form-row">
              <label>故事梗概</label>
              <textarea v-model="aiForm.premise" rows="3"
                        placeholder="例如：少年雨夜捡回一只会说话的黑猫，第二天发现猫竟是失踪三年的邻家妹妹……"></textarea>
            </div>
            <div class="form-row">
              <label>类型</label>
              <input v-model="aiForm.genre" placeholder="奇幻 / 悬疑 / 治愈 / 热血……" />
            </div>
            <div class="form-row">
              <label>视觉风格</label>
              <input v-model="aiForm.style" placeholder="日系动画 / 国风水墨 / 赛博朋克……" />
            </div>
            <div class="form-row">
              <label>规模</label>
              <input v-model.number="aiForm.scene_count" type="number" min="1" max="12" style="width:70px" />
              <span class="dim">个场景 ×</span>
              <input v-model.number="aiForm.shot_per_scene" type="number" min="1" max="8" style="width:70px" />
              <span class="dim">镜/场景</span>
            </div>
          </div>
          <p v-if="aiMsg" class="form-msg" :class="{ error: aiMsgIsError }">{{ aiMsg }}</p>
          <footer class="modal-foot">
            <span class="dim" style="font-size:12px">生成后自动填入上方编辑器，不会直接覆盖项目</span>
            <div style="margin-left:auto; display:flex; gap:10px">
              <button class="btn" @click="aiWriterOpen = false">取消</button>
              <button class="btn btn-primary" :disabled="aiBusy || !aiForm.premise.trim()" @click="runAiWriter">
                {{ aiBusy ? '生成中…' : '✨ 生成剧本' }}
              </button>
            </div>
          </footer>
        </div>
      </div>
    </template>

    <!-- ============ 时间线页（多轨） ============ -->
    <template v-if="tab === 'timeline'">
      <div class="panel">
        <div class="tl-head">
          <h3 style="margin: 0">多轨时间线</h3>
          <div style="display: flex; gap: 8px; flex-wrap: wrap">
            <button class="btn" @click="syncTimeline">🔄 同步已通过镜头</button>
            <select v-model="exportCanvas" aria-label="导出画布" style="width: auto">
              <option value="source">跟随首镜头</option>
              <option value="portrait">竖屏 1080×1920</option>
              <option value="landscape">横屏 1920×1080</option>
              <option value="square">方形 1080×1080</option>
            </select>
            <select v-model="exportFit" aria-label="画面适配" style="width: auto">
              <option value="contain">完整画面（留边）</option>
              <option value="cover">填满画布（居中裁切）</option>
            </select>
            <select v-model.number="exportFps" style="width: 110px" title="导出帧率">
              <option :value="16">16 fps</option>
              <option :value="24">24 fps</option>
              <option :value="30">30 fps</option>
            </select>
            <button class="btn btn-primary" :disabled="!timeline.length || exporting" @click="startExport">
              🎞 导出 MP4（混音+字幕）
            </button>
          </div>
        </div>

        <p class="dim">同镜头配音按生成顺序逐条播放，重新配音后请删除不用的旧条目，并重新生成字幕。</p>
        <!-- PR-11 后半：音频越界警告（导出前实时预览） -->
        <div v-if="timelineWarnings.length" class="warn-box">
          <div v-for="(w, i) in timelineWarnings" :key="i">⚠ {{ w }}</div>
        </div>
        <label><input v-model="extendDialogue" type="checkbox" /> 对白超时自动延长末帧；关闭后会阻止超时导出。延长画面会顺延后续镜头，音乐和音效保持设置的位置。</label>

        <div v-if="exportJob" class="export-status">
          <div class="bar"><div class="bar-inner" :style="{ width: Math.round(exportJob.progress * 100) + '%' }"></div></div>
          <div class="progress-text">{{ exportJob.stage }} {{ Math.round(exportJob.progress * 100) }}%</div>
          <a v-if="exportJob.status === 'done'" class="btn btn-primary" :href="exportJob.file_url" download>⬇ 下载成片</a>
          <span v-if="exportJob.status === 'failed'" class="form-msg">{{ exportJob.error }}</span>
        </div>

        <p v-if="!timeline.length" class="empty">
          时间线还是空的。去「分镜」页生成并通过镜头后，点「同步已通过镜头」。
        </p>

        <!-- 视频轨 -->
        <div v-if="timeline.length" class="track">
          <div class="track-head video">
            <span class="tr-name">🎬 视频轨</span>
            <span class="tr-hint">拖动排序 · 出入点裁剪 · 转场</span>
          </div>
          <div class="tl-list" @dragover.prevent>
            <div v-for="(item, i) in timeline" :key="item.id" class="tl-item"
                 draggable="true" @dragstart="tlDrag = i" @drop="onTlDrop(i)" @dragend="tlDrag = null"
                 :class="{ dragging: tlDrag === i }" @click="previewItem = item">
              <span class="tl-pos">{{ i + 1 }}</span>
              <div class="tl-thumb">
                <img v-if="item.poster_url" :src="item.poster_url" alt="" />
                <video v-else-if="item.video_url" :src="item.video_url" muted preload="metadata"></video>
                <span v-else>无视频</span>
              </div>
              <div class="tl-info">
                <div class="tl-title">{{ item.shot_title }}</div>
                <div class="tl-meta">
                  <label>入点 <input type="number" min="0" step="0.1" :value="item.trim_in_s"
                          @change="updateItem(item, { trim_in_s: +$event.target.value || 0 })" /></label>
                  <label>出点 <input type="number" min="0" step="0.1" :value="item.trim_out_s"
                          @change="updateItem(item, { trim_out_s: +$event.target.value || 0 })" /></label>
                  <!-- PR-11 后半：片段变速 -->
                  <select :value="item.speed || 1" @change="updateItem(item, { speed: +$event.target.value })" title="播放速率">
                    <option :value="0.5">0.5×</option>
                    <option :value="0.75">0.75×</option>
                    <option :value="1">原速</option>
                    <option :value="1.25">1.25×</option>
                    <option :value="1.5">1.5×</option>
                    <option :value="2">2×</option>
                  </select>
                  <select :value="item.transition" @change="updateItem(item, { transition: $event.target.value })">
                    <option value="none">硬切</option>
                    <option value="fade">淡入淡出</option>
                  </select>
                </div>
              </div>
              <button class="icon-btn danger" @click.stop="removeItem(item.id)">✕</button>
            </div>
          </div>
        </div>

        <!-- 对白轨（TTS 配音） -->
        <div v-if="timeline.length" class="track">
          <div class="track-head dialogue">
            <span class="tr-name">💬 对白轨（TTS 配音）</span>
            <span class="tr-hint">在分镜编辑弹窗里生成；导出时按镜头位置自动混音</span>
          </div>
          <div class="track-body">
            <div v-for="item in timeline" :key="item.id" class="tr-row">
              <span class="tr-idx">{{ item.shot_title }}</span>
              <template v-if="voicesOf(item).length">
                <div v-for="v in voicesOf(item)" :key="v.id" class="tr-clip">
                  <audio :src="v.audio_url" controls preload="metadata"></audio>
                  <span class="clip-meta">{{ v.character_name || v.voice }} · {{ (v.duration_s || 0).toFixed(1) }}s</span>
                  <!-- PR-11 后半：配音波形 -->
                  <div v-if="waveforms[v.audio_url]" class="wave-bars">
                    <span v-for="(p, i) in waveforms[v.audio_url]" :key="i" class="wave-bar"
                          :style="{ height: Math.max(p * 100, 8) + '%' }"></span>
                  </div>
                  <button class="icon-btn danger" @click="deleteVoice(v.id)">✕</button>
                </div>
              </template>
              <span v-else-if="item.dialogue" class="tr-empty">有对白未配音</span>
              <span v-else class="tr-empty">无对白</span>
            </div>
          </div>
        </div>

        <!-- 音乐 / 音效轨 -->
        <div class="track">
          <div class="track-head music">
            <span class="tr-name">🎵 音乐轨</span>
            <label class="btn small upload-btn">
              ＋ 上传音乐
              <input type="file" accept="audio/*" hidden @change="onAudioUpload($event, 'music')" />
            </label>
          </div>
          <div class="track-body">
            <div v-for="t in musicTracks" :key="t.id" class="tr-row">
              <input class="tr-title-input" :value="t.title" @change="updateTrack(t, { title: $event.target.value })" />
              <label class="tr-field">起点 <input type="number" min="0" step="0.5" :value="t.start_s"
                     @change="updateTrack(t, { start_s: +$event.target.value || 0 })" /></label>
              <label class="tr-field">音量 <input type="range" min="0" max="1" step="0.05" :value="t.volume"
                     @change="updateTrack(t, { volume: +$event.target.value })" /></label>
              <!-- PR-11 后半：音乐波形 -->
              <div v-if="waveforms[t.audio_url]" class="wave-bars wide">
                <span v-for="(p, i) in waveforms[t.audio_url]" :key="i" class="wave-bar"
                      :style="{ height: Math.max(p * 100, 8) + '%' }"></span>
              </div>
              <button class="icon-btn danger" @click="deleteTrack(t.id)">✕</button>
            </div>
            <div v-if="!musicTracks.length" class="tr-empty">还没有音乐，上传后自动混入导出</div>
          </div>
        </div>

        <div class="track">
          <div class="track-head sfx">
            <span class="tr-name">🔔 音效轨</span>
            <label class="btn small upload-btn">
              ＋ 上传音效
              <input type="file" accept="audio/*" hidden @change="onAudioUpload($event, 'sfx')" />
            </label>
          </div>
          <div class="track-body">
            <div v-for="t in sfxTracks" :key="t.id" class="tr-row">
              <input class="tr-title-input" :value="t.title" @change="updateTrack(t, { title: $event.target.value })" />
              <label class="tr-field">起点 <input type="number" min="0" step="0.5" :value="t.start_s"
                     @change="updateTrack(t, { start_s: +$event.target.value || 0 })" /></label>
              <label class="tr-field">音量 <input type="range" min="0" max="1" step="0.05" :value="t.volume"
                     @change="updateTrack(t, { volume: +$event.target.value })" /></label>
              <div v-if="waveforms[t.audio_url]" class="wave-bars wide">
                <span v-for="(p, i) in waveforms[t.audio_url]" :key="i" class="wave-bar"
                      :style="{ height: Math.max(p * 100, 8) + '%' }"></span>
              </div>
              <button class="icon-btn danger" @click="deleteTrack(t.id)">✕</button>
            </div>
            <div v-if="!sfxTracks.length" class="tr-empty">还没有音效</div>
          </div>
        </div>

        <!-- 字幕轨 -->
        <div class="track">
          <div class="track-head subtitle">
            <span class="tr-name">📝 字幕轨（{{ subtitles.length }}）</span>
            <div style="display: flex; gap: 8px">
              <button class="btn small" @click="autoSubtitles">✨ 从对白自动生成</button>
              <button class="btn small" :disabled="!subtitles.length" @click="downloadSrt">⬇ SRT</button>
            </div>
          </div>
          <div class="track-body">
            <div v-for="(s, i) in subtitles" :key="s.id" class="tr-row">
              <span class="tr-idx">{{ i + 1 }}</span>
              <input class="tr-sub-input" :value="s.text" @change="updateSubtitle(s, { text: $event.target.value })" />
              <label class="tr-field">起 <input type="number" min="0" step="0.1" :value="s.start_s"
                     @change="updateSubtitle(s, { start_s: +$event.target.value || 0 })" /></label>
              <label class="tr-field">止 <input type="number" min="0" step="0.1" :value="s.end_s"
                     @change="updateSubtitle(s, { end_s: +$event.target.value || 0 })" /></label>
              <button class="icon-btn danger" @click="deleteSubtitle(s.id)">✕</button>
            </div>
            <div v-if="!subtitles.length" class="tr-empty">还没有字幕，点「从对白自动生成」</div>
          </div>
        </div>

        <div v-if="previewItem && previewItem.video_url" class="tl-preview">
          <video :src="previewItem.video_url" controls autoplay muted></video>
        </div>

        <div v-if="exportHistory.length" class="export-history">
          <h4>导出记录</h4>
          <div v-for="e in exportHistory" :key="e.id" class="exp-row">
            <span class="badge" :class="e.status === 'done' ? 'done' : e.status === 'failed' ? 'failed' : 'running'">
              {{ e.status === 'done' ? '完成' : e.status === 'failed' ? '失败' : '进行中' }}
            </span>
            <span class="exp-time">{{ (e.created_at || '').slice(5, 16).replace('T', ' ') }}</span>
            <a v-if="e.file_url" :href="e.file_url" download>下载</a>
            <span v-if="e.error" class="form-msg">{{ e.error }}</span>
            <!-- PR-11 后半：导出时的音频越界警告回看 -->
            <span v-for="(w, i) in parseWarnings(e.warnings)" :key="'w' + i" class="warn-inline">⚠ {{ w }}</span>
          </div>
        </div>
      </div>
    </template>

    <!-- ============ 历史页 ============ -->
    <template v-if="tab === 'history'">
      <div class="panel">
        <div class="tl-head">
          <h3 style="margin: 0">审核与操作历史（{{ history.length }}）</h3>
          <button class="btn" @click="loadHistory">🔄 刷新</button>
        </div>
        <div v-if="history.length" class="history-list">
          <div v-for="h in history" :key="h.id" class="history-item">
            <span class="badge" :class="h.action === 'approve' ? 'done' : h.action === 'rework' ? 'failed' : 'cancelled'">
              {{ h.action === 'approve' ? '通过' : h.action === 'rework' ? '返工/打回' : h.action }}
            </span>
            <div class="h-body">
              <div class="h-title">
                {{ h.shot_title || h.shot_id }}
                <span class="h-flow">{{ h.from_status }} → {{ h.to_status }}</span>
              </div>
              <div v-if="h.comment" class="h-comment">「{{ h.comment }}」</div>
              <div v-if="h.checks && h.checks.length" class="h-checks">
                <span v-for="(c, i) in h.checks" :key="i" class="h-check" :class="c.level">{{ c.message }}</span>
              </div>
              <div v-else class="h-checks">当时检查无问题</div>
            </div>
            <span class="h-time">{{ (h.created_at || '').slice(5, 16).replace('T', ' ') }}</span>
          </div>
        </div>
        <p v-else class="empty">还没有审核记录。审核分镜后，每次通过/打回都会留痕可回溯。</p>
      </div>
    </template>

    <!-- 分镜编辑弹窗 -->
    <ShotEditorDialog
      v-if="editingShot"
      :shot="editingShot"
      :engines="engines"
      :presets="presets"
      :characters="characters"
      :sibling-shots="siblingsOf(editingShot)"
      :project-id="project?.id || ''"
      @close="editingShot = null"
      @saved="onShotSaved"
      @refresh="onEditorRefresh"
      @adopt="(vid) => store.adoptVersion(editingShot.id, vid)"
      @delete-version="(vid) => store.deleteVersion(editingShot.id, vid)"
      @delete-voice="deleteVoice"
    />

    <!-- 审核弹窗 -->
    <ReviewDialog
      v-if="reviewingShot"
      :shot="reviewingShot"
      :preset="reviewPreset"
      :initial-stage="reviewInitialStage"
      @close="reviewingShot = null"
      @done="onReviewed"
    />

    <!-- 返工选项弹窗 -->
    <div v-if="reworkingShot" class="modal-mask" @click.self="reworkingShot = null">
      <div class="modal mini">
        <header class="modal-head">
          <h2>镜头返工</h2>
          <button class="icon-btn" @click="reworkingShot = null">✕</button>
        </header>
        <div class="modal-body">
          <p class="rework-intro">
            「{{ reworkingShot.title || '未命名镜头' }}」将被打回草稿状态重新生成。
            已生成的版本会保留，可随时对比回看。
          </p>
          <p class="rework-intro dim">后续镜头如果依赖本镜头的画面（续接/情绪连贯），建议一起返工。</p>
        </div>
        <footer class="modal-foot">
          <div style="margin-left: auto; display: flex; gap: 10px; flex-wrap: wrap">
            <button class="btn" @click="reworkingShot = null">取消</button>
            <button class="btn" @click="doRework(false)">↺ 仅重做本镜头</button>
            <button class="btn warn" @click="doRework(true)">↺↺ 连同后续一起返工</button>
          </div>
        </footer>
      </div>
    </div>

    <!-- 角色卡编辑弹窗 -->
    <CharacterEditorDialog
      v-if="editingCharacter"
      :project-id="project.id"
      :character="editingCharacter.id ? editingCharacter : null"
      @close="editingCharacter = null"
      @saved="onCharacterSaved"
    />
  </div>

  <!-- 项目读不到时不能白屏：给出原因和退路（删过的链接、后端换了库都会走到这里） -->
  <div v-else class="page">
    <header class="page-head">
      <div>
        <h1>项目</h1>
        <div class="page-sub">{{ loadError ? '读取失败' : '正在读取项目…' }}</div>
      </div>
    </header>
    <div class="panel">
      <p v-if="loadError" class="form-msg">{{ loadError }}</p>
      <p v-else class="dim">正在加载项目数据，请稍候…</p>
      <p style="margin-top: 14px">
        <router-link to="/projects" class="btn">← 返回项目列表</router-link>
      </p>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { getEnginePref, engineName, resolveEngine } from '../enginePref'
import { useJobsStore } from '../store/jobs'
import { useProjectStore } from '../store/project'
import ShotCard from '../components/ShotCard.vue'
import ShotEditorDialog from '../components/ShotEditorDialog.vue'
import ReviewDialog from '../components/ReviewDialog.vue'
import CharacterEditorDialog from '../components/CharacterEditorDialog.vue'

const route = useRoute()
const router = useRouter()
const store = useProjectStore()
const jobsStore = useJobsStore()

const project = computed(() => store.current)
const loadError = ref('')
const totalShots = computed(() =>
  (project.value?.scenes || []).reduce((n, s) => n + s.shots.length, 0))
const approvedShots = computed(() =>
  (project.value?.scenes || []).reduce(
    (n, s) => n + s.shots.filter((x) => x.status === 'approved').length, 0))

const tab = ref('shots')
const message = ref('')
const scriptText = ref('')
const savingScript = ref(false)
const importing = ref(false)

const engines = ref([])
const presets = ref([])
const editingShot = ref(null)
const dragId = ref('')
const comfy = ref({ online: false, url: '' })

// 资产库
const characters = ref([])
const editingCharacter = ref(null)

// 时间线（多轨）
const timeline = ref([])
const tlDrag = ref(null)
const previewItem = ref(null)
const exporting = ref(false)
const exportJob = ref(null)
const exportHistory = ref([])
const exportFps = ref(24)
const exportCanvas = ref('portrait')
const exportFit = ref('contain')
const extendDialogue = ref(true)
const musicTracks = ref([])
const sfxTracks = ref([])
const subtitles = ref([])
let exportTimer = null

// 历史
const history = ref([])

// 轮询：有活跃分镜时刷新项目与任务进度
let pollTimer = null

const VALID_TABS = ['shots', 'assets', 'script', 'timeline', 'history']
watch(() => route.query.tab, value => {
  if (VALID_TABS.includes(value)) tab.value = value
})

onMounted(async () => {
  try {
    await store.openProject(route.params.id)
  } catch (e) {
    // 项目不存在/后端不可用时，页面必须能说明原因并给退路，而不是一片空白
    loadError.value = e.status === 404
      ? '项目不存在，可能已被删除。'
      : `读取项目失败：${e.message}`
    return
  }
  if (!project.value) {
    loadError.value = '项目数据为空，请返回列表重试。'
    return
  }
  // 支持 ?tab= 直接定位（侧栏导航跳转用）
  const qTab = route.query.tab
  if (qTab && VALID_TABS.includes(qTab)) tab.value = qTab
  if (project.value) scriptText.value = project.value.script || ''
  try { engines.value = await api.engines() } catch { /* ignore */ }
  try { presets.value = await api.presets() } catch { /* ignore */ }
  try { comfy.value = await api.comfyStatus() } catch { /* ignore */ }
  await loadCharacters()
  pollTimer = setInterval(pollActive, 3000)

  // 切标签时按需加载 + 同步 URL（刷新/分享保持定位）
  watch(tab, async (t) => {
    if (t === 'timeline' && project.value) await loadTimeline()
    if (t === 'assets' && project.value) await loadCharacters()
    if (t === 'history' && project.value) await loadHistory()
    const q = { ...route.query, tab: t }
    if (route.query.tab !== t) router.replace({ query: q })
  }, { immediate: true })
})

onUnmounted(() => {
  clearInterval(pollTimer)
  clearInterval(exportTimer)
})

async function pollActive() {
  const scenes = project.value?.scenes || []
  const hasActive = scenes.some((s) =>
    s.shots.some((x) => x.status === 'queued' || x.status === 'generating'))
  if (!hasActive) return
  await jobsStore.fetch()
  await store.refresh()
}

function jobOf(shot) {
  if (!shot.job_id) return null
  return jobsStore.jobs.find((j) => j.id === shot.job_id) || null
}

function charColor(name) {
  let h = 0
  for (const ch of name || '') h = (h * 31 + ch.codePointAt(0)) % 360
  return `hsl(${h}, 65%, 60%)`
}

// ---------- 项目 ----------
async function renameProject(e) {
  const name = e.target.value.trim()
  if (name && name !== project.value.name) {
    await store.saveProject(name)
  }
}

// ---------- 场景 / 分镜 ----------
async function addShot(sceneId) {
  await store.addShot(sceneId, { title: '新镜头' })
}
async function removeScene(sceneId) {
  if (!confirm('删除场景会连同其中所有分镜一起删除，确定？')) return
  await store.removeScene(sceneId)
}
async function removeShot(shotId) {
  if (!confirm('确定删除这个分镜？')) return
  await store.removeShot(shotId)
}

async function moveShot(scene, from, to) {
  if (to < 0 || to >= scene.shots.length) return
  const ids = scene.shots.map((s) => s.id)
  const [moved] = ids.splice(from, 1)
  ids.splice(to, 0, moved)
  await store.reorderShots(scene.id, ids)
}

function onSceneDrop(scene, e) {
  const shotId = e.dataTransfer.getData('text/plain')
  if (!shotId) return
  const target = scene.shots[scene.shots.length - 1]
  if (!target || target.id === shotId) return
  onShotDrop(scene, scene.shots.length - 1, e)
}

async function onShotDrop(scene, index, e) {
  const shotId = typeof e === 'string' ? e : e.dataTransfer.getData('text/plain')
  if (!shotId) return
  const shots = [...scene.shots]
  const from = shots.findIndex((s) => s.id === shotId)
  if (from < 0 || from === index) return
  const [moved] = shots.splice(from, 1)
  shots.splice(index, 0, moved)
  await store.reorderShots(scene.id, shots.map((s) => s.id))
}

function siblingsOf(shot) {
  for (const sc of project.value?.scenes || []) {
    const i = sc.shots.findIndex((s) => s.id === shot.id)
    if (i >= 0) return sc.shots.filter((s) => s.id !== shot.id)
  }
  return []
}

// ---------- 编辑弹窗 ----------
function openEditor(shot) {
  editingShot.value = shot
}

async function onShotSaved(data, action) {
  const shotId = editingShot.value.id
  try {
    await store.saveShot(shotId, data)
    let holdMs = 2500
    if (action && action.then === 'generate') {
      const job = await store.generate(shotId, action.params)
      // 资源守卫可能自动降档：本次实际用的参数与提交的不一样，必须说清楚
      if (job && job.resource_note) {
        message.value = job.resource_note
        holdMs = 8000
      } else {
        message.value = '已提交生成，可在任务队列查看进度'
      }
    } else {
      message.value = '已保存'
    }
    editingShot.value = null
    setTimeout(() => (message.value = ''), holdMs)
  } catch (e) {
    message.value = e.message
  }
}

async function quickGenerate(shot) {
  if (!shot.prompt || !shot.prompt.trim()) {
    message.value = '请先在编辑弹窗里填写提示词'
    return
  }
  try {
    // 快捷生成沿用用户自选的引擎（在生成页 / 分镜弹窗里选过就记住了）；
    // 仅在它当前不可用时临时换一个可用引擎，并告知用户。
    const preferred = getEnginePref()
    const { engine, fallback } = resolveEngine(engines.value, preferred)
    const job = await store.generate(shot.id, { engine })
    if (job && job.resource_note) {
      // 资源守卫自动降档：本次不是提交时那个分辨率，得让用户看见
      message.value = job.resource_note
      setTimeout(() => (message.value = ''), 8000)
    } else if (fallback) {
      message.value = `「${engineName(engines.value, preferred)}」当前不可用，已用「${engineName(engines.value, engine)}」生成`
      setTimeout(() => (message.value = ''), 3500)
    }
  } catch (e) {
    message.value = e.message
  }
}

// ---------- 审核 / 返工 ----------
const reviewingShot = ref(null)
const reviewPreset = ref('')
const reviewInitialStage = ref('video')
const reworkingShot = ref(null)

function openReview(shot, presetAction = '') {
  reviewingShot.value = shot
  reviewPreset.value = presetAction
  // 3A：镜头处于设计审核门时默认打开设计审核页
  reviewInitialStage.value = shot.review_stage === 'design' ? 'design' : 'video'
}
function openReviewDesign(shot) {
  reviewingShot.value = shot
  reviewPreset.value = ''
  reviewInitialStage.value = 'design'
}
async function onReviewed(action) {
  const wasDesign = reviewInitialStage.value === 'design'
  reviewingShot.value = null
  reviewPreset.value = ''
  message.value = wasDesign
    ? (action === 'approve' ? '设计审核已通过' : '已打回修改设计')
    : (action === 'approve' ? '已审核通过' : '已打回重做')
  setTimeout(() => (message.value = ''), 2000)
  await store.refresh()
}

function openRework(shot) {
  reworkingShot.value = shot
}

async function doRework(cascade) {
  const shot = reworkingShot.value
  reworkingShot.value = null
  if (!shot) return
  try {
    const r = await api.reworkShot(shot.id, cascade, cascade ? '连同后续一起返工' : '返工重做')
    message.value = cascade
      ? `已连同后续共返工 ${r.reset.length} 个镜头`
      : '已重做本镜头'
    setTimeout(() => (message.value = ''), 2500)
    await store.refresh()
  } catch (e) {
    message.value = e.message
  }
}

// 配音生成后：只刷新数据不关弹窗
async function onEditorRefresh() {
  await store.refresh()
}

// ---------- 角色资产 ----------
async function loadCharacters() {
  if (!project.value) return
  characters.value = await api.characters(project.value.id)
}
async function onCharacterSaved() {
  editingCharacter.value = null
  await loadCharacters()
  message.value = '角色卡已保存'
  setTimeout(() => (message.value = ''), 2000)
}
async function removeCharacter(c) {
  if (!confirm(`删除角色「${c.name}」？关联分镜会解除绑定。`)) return
  await api.deleteCharacter(c.id)
  await loadCharacters()
}

// ---------- 剧本 ----------
async function saveScript() {
  savingScript.value = true
  try {
    await store.saveScript(scriptText.value)
    message.value = '剧本已保存'
    setTimeout(() => (message.value = ''), 2000)
  } finally {
    savingScript.value = false
  }
}

// ---------- AI 编剧 ----------
const aiWriterOpen = ref(false)
const aiBusy = ref(false)
const aiMsg = ref('')
const aiMsgIsError = ref(false)
const aiForm = reactive({
  premise: '', genre: '', style: '', scene_count: 2, shot_per_scene: 2,
})

async function runAiWriter() {
  aiBusy.value = true
  aiMsg.value = ''
  aiMsgIsError.value = false
  try {
    const r = await api.llmScript({ ...aiForm })
    scriptText.value = r.script
    aiMsg.value = `已生成 ${r.script.length} 字，可继续编辑后点「AI 拆分」`
    setTimeout(() => {
      aiWriterOpen.value = false
      aiMsg.value = ''
    }, 1200)
  } catch (e) {
    aiMsgIsError.value = true
    aiMsg.value = e.message.includes('未配置')
      ? 'LLM 未配置：请到左侧「🧩 模型服务」页填写 Base URL 和模型名'
      : e.message
  } finally {
    aiBusy.value = false
  }
}

async function doImport(mode) {
  importing.value = true
  try {
    // 先做差异预览（不写库），让用户确认后再真正导入
    const pv = await api.importScript(project.value.id, scriptText.value, 'preview')
    const inc = pv.incoming, ex = pv.existing
    let tip = `解析出 ${inc.scene_count} 个场景 / ${inc.shot_count} 个分镜。`
    if (mode === 'rebuild') {
      tip += `\n\n覆盖重建将清空现有 ${ex.scene_count} 个场景 / ${ex.shot_count} 个分镜` +
        '（已生成视频文件保留在 outputs，但版本记录会一并删除）。'
      if (pv.overlap_titles.length) tip += `\n同名场景将被替换：${pv.overlap_titles.join('、')}`
    } else {
      tip += `\n\n追加导入保留现有 ${ex.scene_count} 个场景 / ${ex.shot_count} 个分镜。`
      tip += pv.overlap_titles.length
        ? `\n同名场景将跳过（不覆盖制作成果）：${pv.overlap_titles.join('、')}`
        : '\n没有同名场景。'
    }
    if (!confirm(tip + '\n\n确定继续？')) return
    // 3A：预览绑定回传——预览之后项目被改过则 409 过期提交；
    // 破坏性重建若将损失已生成版本/时间线，服务端先返回损失明细（409）
    const binding = {
      expected_revision: pv.project_revision,
      content_hash: pv.content_hash,
    }
    let r
    try {
      r = await store.importScript(scriptText.value, mode, binding)
    } catch (e) {
      const loss = e.data?.detail?.loss
      if (e.status === 409 && loss) {
        const ok = confirm(
          '覆盖重建将删除以下制作成果：\n' +
          `· 已生成镜头版本 ${loss.version_count} 个\n` +
          `· 时间线片段 ${loss.timeline_count} 个\n` +
          `· 场景 ${loss.scene_count} 个 / 分镜 ${loss.shot_count} 个\n\n` +
          '服务端会先自动备份数据库再重建（备份在 backend/backups/）。确定继续？')
        if (!ok) return
        r = await store.importScript(scriptText.value, mode, { ...binding, confirm: true })
      } else {
        throw e
      }
    }
    message.value = `导入完成（${mode === 'rebuild' ? '覆盖重建' : '追加'}）：` +
      `${r.scene_count} 个场景 / ${r.shot_count} 个分镜` +
      (mode === 'rebuild' && r.removed?.shots ? `，已移除旧分镜 ${r.removed.shots} 个` : '') +
      (r.backup ? '（已自动备份数据库）' : '')
    setTimeout(() => (message.value = ''), 4000)
    tab.value = 'shots'
  } catch (e) {
    message.value = e.message
  } finally {
    importing.value = false
  }
}

// ---------- 时间线 ----------
const timelineWarnings = ref([])   // PR-11 后半：音频越界实时预览
const waveforms = reactive({})     // PR-11 后半：url → peaks[]（波形条）

function parseWarnings(raw) {
  if (Array.isArray(raw)) return raw
  if (typeof raw === 'string' && raw) {
    try { const arr = JSON.parse(raw); return Array.isArray(arr) ? arr : [] } catch { /* 脏值忽略 */ }
  }
  return []
}

async function loadWaveform(url) {
  if (!url || waveforms[url]) return
  try {
    const r = await api.waveform(url, 64)
    waveforms[url] = r.peaks || []
  } catch { /* 波形失败不阻塞时间线 */ }
}

async function loadTimeline() {
  timeline.value = await store.loadTimeline()
  exportHistory.value = await api.listExports(project.value.id)
  const [music, sfx, subs, warns] = await Promise.all([
    api.audioTracks(project.value.id, 'music'),
    api.audioTracks(project.value.id, 'sfx'),
    api.subtitles(project.value.id),
    api.timelineWarnings(project.value.id).catch(() => ({ warnings: [] })),
  ])
  musicTracks.value = music
  sfxTracks.value = sfx
  subtitles.value = subs
  timelineWarnings.value = warns.warnings || []
  // PR-11 后半：懒加载音轨/配音波形（缓存命中时开销可忽略）
  for (const t of [...music, ...sfx]) loadWaveform(t.audio_url)
  for (const item of timeline.value) {
    for (const v of voicesOf(item)) loadWaveform(v.audio_url)
  }
}

async function syncTimeline() {
  timeline.value = await store.syncTimeline()
  message.value = `已同步 ${timeline.value.length} 个片段`
  setTimeout(() => (message.value = ''), 2000)
}

async function updateItem(item, data) {
  const updated = await store.updateTimelineItem(item.id, data)
  if (updated) Object.assign(item, updated)
}

async function removeItem(itemId) {
  await store.removeTimelineItem(itemId)
  timeline.value = timeline.value.filter((i) => i.id !== itemId)
}

async function onTlDrop(index) {
  if (tlDrag.value === null || tlDrag.value === index) return
  const ids = timeline.value.map((i) => i.id)
  const [moved] = ids.splice(tlDrag.value, 1)
  ids.splice(index, 0, moved)
  timeline.value = await store.reorderTimeline(ids)
}

// ---------- 多轨操作 ----------
function voicesOf(item) {
  // 时间线片段 → 镜头 → 配音列表（项目数据里已带 voices）
  for (const sc of project.value?.scenes || []) {
    const shot = sc.shots.find((s) => s.id === item.shot_id)
    if (shot) return shot.voices || []
  }
  return []
}

async function deleteVoice(voiceId) {
  await api.deleteVoice(voiceId)
  await store.refresh()
  message.value = '配音已删除'
  setTimeout(() => (message.value = ''), 1500)
}

async function onAudioUpload(e, kind) {
  const file = e.target.files?.[0]
  if (!file) return
  try {
    const up = await api.uploadFile(file)
    await api.createAudioTrack(project.value.id, {
      kind, title: file.name.replace(/\.[^.]+$/, ''), audio_url: up.url,
    })
    await loadTimeline()
    message.value = '已添加音频'
    setTimeout(() => (message.value = ''), 1500)
  } catch (err) {
    message.value = err.message
  }
  e.target.value = ''
}

async function updateTrack(t, data) {
  const updated = await api.updateAudioTrack(t.id, data)
  if (updated) Object.assign(t, updated)
  // 起点变化会影响越界判定 → 重新拉警告
  api.timelineWarnings(project.value.id).then((r) => (timelineWarnings.value = r.warnings || [])).catch(() => {})
}

async function deleteTrack(id) {
  await api.deleteAudioTrack(id)
  await loadTimeline()
}

async function autoSubtitles() {
  try {
    subtitles.value = await api.autoSubtitles(project.value.id)
    message.value = `已生成 ${subtitles.value.length} 条字幕`
    setTimeout(() => (message.value = ''), 2000)
  } catch (e) {
    message.value = e.message
  }
}

async function updateSubtitle(s, data) {
  const updated = await api.updateSubtitle(s.id, data)
  if (updated) Object.assign(s, updated)
}

async function deleteSubtitle(id) {
  await api.deleteSubtitle(id)
  subtitles.value = subtitles.value.filter((s) => s.id !== id)
}

async function downloadSrt() {
  try {
    const { srt } = await api.exportSrt(project.value.id)
    const blob = new Blob([srt], { type: 'text/plain;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${project.value.name || 'project'}.srt`
    a.click()
    URL.revokeObjectURL(a.href)
  } catch (e) {
    message.value = e.message
  }
}

// ---------- 导出 ----------
async function startExport() {
  exporting.value = true
  try {
    exportJob.value = await store.createExport({ fps: exportFps.value, canvas: exportCanvas.value,
      fit: exportFit.value, extend_dialogue: extendDialogue.value })
    exportTimer = setInterval(async () => {
      exportJob.value = await store.pollExport(exportJob.value.id)
      if (exportJob.value.status === 'done' || exportJob.value.status === 'failed') {
        clearInterval(exportTimer)
        exporting.value = false
        exportHistory.value = await api.listExports(project.value.id)
      }
    }, 1500)
  } catch (e) {
    exporting.value = false
    message.value = e.message
  }
}

// ---------- 历史 ----------
async function loadHistory() {
  if (!project.value) return
  history.value = await api.projectHistory(project.value.id)
}
</script>

<style scoped>
.crumb { display: flex; align-items: center; gap: 14px; margin-bottom: 4px; }
.crumb a { color: var(--text-dim); text-decoration: none; font-size: 13px; }
.crumb a:hover { color: var(--accent); }
.proj-name {
  background: transparent; border: 1px solid transparent; border-radius: 8px;
  color: var(--text); font-size: 20px; font-weight: 700; padding: 4px 10px;
  width: 320px; font-family: inherit;
}
.proj-name:hover, .proj-name:focus { border-color: var(--border); background: var(--bg-card); }

.head-actions { display: flex; align-items: center; gap: 10px; }
.comfy-pill {
  display: flex; align-items: center; gap: 6px; font-size: 12px;
  border: 1px solid var(--border); border-radius: 999px; padding: 5px 12px;
}
.comfy-pill.on { color: var(--green); }
.comfy-pill.off { color: var(--text-dim); }

.tabs { display: flex; gap: 6px; margin-bottom: 18px; border-bottom: 1px solid var(--border); }
.tabs button {
  background: none; border: none; color: var(--text-dim); font-size: 14px;
  padding: 10px 18px; cursor: pointer; border-bottom: 2px solid transparent;
  font-family: inherit;
}
.tabs button:hover { color: var(--text); }
.tabs button.active { color: var(--text); border-bottom-color: var(--accent); font-weight: 600; }

.banner { margin-bottom: 14px; }

.scene-block { padding: 16px 18px; }
.scene-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.scene-title-input {
  background: transparent; border: 1px solid transparent; border-radius: 8px;
  font-size: 16px; font-weight: 700; color: var(--text); padding: 4px 10px;
  width: 300px; font-family: inherit;
}
.scene-title-input:hover, .scene-title-input:focus { border-color: var(--border); background: var(--bg-card); }
.scene-count { font-size: 12px; color: var(--text-dim); }
.scene-desc {
  display: flex; flex-wrap: wrap; gap: 14px; font-size: 12.5px;
  color: var(--text-dim); margin-bottom: 12px;
}

.shot-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px;
}
.add-scene { width: 100%; padding: 14px; border-style: dashed; }

.hint {
  font-size: 13px; color: var(--text-dim); line-height: 1.8;
  background: var(--bg-card); border-radius: 8px; padding: 12px 14px; margin-bottom: 14px;
}
.script-area { font-size: 13.5px; line-height: 1.8; min-height: 320px; }
.script-actions { display: flex; gap: 10px; margin-top: 14px; }

/* ---------- 资产库 ---------- */
.char-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px;
}
.char-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 14px;
}
.cc-head { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.cc-head b { font-size: 14px; }
.cc-actions { margin-left: auto; display: flex; gap: 2px; }
.char-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.cc-refs {
  display: flex; gap: 6px; margin: 10px 0; min-height: 56px; align-items: center;
}
.cc-refs img {
  width: 52px; height: 52px; object-fit: cover; border-radius: 6px;
  border: 1px solid var(--border);
}
.cc-noref { font-size: 11.5px; color: var(--text-dim); }
.cc-body { display: flex; flex-direction: column; gap: 4px; }
.cc-line {
  font-size: 12px; color: var(--text-dim);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.scene-asset {
  border: 1px solid var(--border); border-radius: 10px;
  padding: 12px 14px; margin-bottom: 10px; background: var(--bg-card);
}
.sa-title { font-size: 13.5px; font-weight: 600; margin-bottom: 8px; }
.sa-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.sa-grid input {
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: 7px;
  color: var(--text); padding: 7px 10px; font-size: 12.5px; font-family: inherit;
}
.sa-grid input:focus { border-color: var(--accent); outline: none; }

/* ---------- 多轨时间线 ---------- */
.tl-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; flex-wrap: wrap; gap: 8px; }
.export-status { display: flex; align-items: center; gap: 14px; margin-bottom: 16px; flex-wrap: wrap; width: 100%; }
.export-status .bar { flex: 1; min-width: 200px; }

.track {
  border: 1px solid var(--border); border-radius: 10px;
  margin-bottom: 12px; overflow: hidden;
}
.track-head {
  display: flex; align-items: center; gap: 10px; padding: 8px 12px;
  background: var(--bg-card); border-bottom: 1px solid var(--border);
}
.track-head.video { border-left: 3px solid var(--accent); }
.track-head.dialogue { border-left: 3px solid var(--green); }
.track-head.music { border-left: 3px solid var(--accent-soft); }
.track-head.sfx { border-left: 3px solid var(--yellow); }
.track-head.subtitle { border-left: 3px solid #38bdf8; }
.tr-name { font-size: 13px; font-weight: 700; }
.tr-hint { font-size: 11.5px; color: var(--text-dim); margin-left: auto; }
.track-body { padding: 8px 12px; display: flex; flex-direction: column; gap: 6px; }
.tr-row {
  display: flex; align-items: center; gap: 10px; font-size: 12.5px;
  padding: 4px 0;
}
.tr-idx {
  font-size: 12px; color: var(--text-dim); min-width: 110px; max-width: 110px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-shrink: 0;
}
.tr-clip { display: flex; align-items: center; gap: 8px; }
.tr-clip audio { height: 30px; }
.clip-meta { font-size: 11px; color: var(--text-dim); }
/* ---- PR-11 后半：波形条 / 越界警告 ---- */
.wave-bars {
  display: flex; align-items: center; gap: 1px; height: 26px;
  flex: 0 1 160px; min-width: 0; overflow: hidden;
}
.wave-bars.wide { flex: 1 1 240px; }
.wave-bar {
  flex: 1 0 2px; max-width: 4px; background: var(--accent);
  opacity: 0.55; border-radius: 1px;
}
.warn-box {
  background: rgba(250, 204, 21, 0.1); border: 1px solid rgba(250, 204, 21, 0.45);
  border-radius: 8px; padding: 8px 12px; margin: 8px 0;
  font-size: 12.5px; color: var(--yellow, #eab308); line-height: 1.7;
}
.warn-inline { font-size: 11.5px; color: var(--yellow, #eab308); }
.tr-empty { font-size: 11.5px; color: var(--text-dim); font-style: italic; }
.tr-title-input, .tr-sub-input {
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: 6px;
  color: var(--text); padding: 5px 8px; font-size: 12px; font-family: inherit;
}
.tr-title-input { width: 140px; }
.tr-sub-input { flex: 1; min-width: 120px; }
.tr-field { display: flex; align-items: center; gap: 4px; font-size: 11.5px; color: var(--text-dim); }
.tr-field input {
  width: 58px; background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: 6px; color: var(--text); padding: 3px 6px; font-size: 11.5px;
}
.tr-field input[type="range"] { width: 80px; padding: 0; }
.btn.small { padding: 4px 12px; font-size: 12px; }
.upload-btn { display: inline-flex; align-items: center; }

.tl-list { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; }
.tl-item {
  display: flex; align-items: center; gap: 12px; background: var(--bg-card);
  border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px; cursor: grab;
}
.tl-item.dragging { opacity: 0.5; border-color: var(--accent); }
.tl-pos {
  width: 22px; height: 22px; border-radius: 6px; background: var(--bg-panel);
  border: 1px solid var(--border); display: flex; align-items: center; justify-content: center;
  font-size: 12px; color: var(--text-dim); flex-shrink: 0;
}
.tl-thumb { width: 96px; height: 54px; border-radius: 6px; overflow: hidden; background: #000; flex-shrink: 0; }
.tl-thumb img, .tl-thumb video { width: 100%; height: 100%; object-fit: cover; }
.tl-info { flex: 1; min-width: 0; }
.tl-title { font-size: 13.5px; font-weight: 600; margin-bottom: 4px; }
.tl-meta { display: flex; gap: 12px; font-size: 12px; color: var(--text-dim); align-items: center; }
.tl-meta label { display: flex; align-items: center; gap: 4px; }
.tl-meta input, .tl-meta select {
  width: 64px; background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: 6px; color: var(--text); padding: 3px 6px; font-size: 12px;
}
.tl-preview { margin-top: 16px; }
.tl-preview video { width: 100%; max-width: 640px; border-radius: 10px; background: #000; }

.export-history { margin-top: 20px; border-top: 1px solid var(--border); padding-top: 14px; }
.export-history h4 { margin: 0 0 10px; font-size: 13px; }
.exp-row { display: flex; align-items: center; gap: 10px; font-size: 12.5px; padding: 6px 0; }
.exp-time { color: var(--text-dim); }
.exp-row a { color: var(--accent); }

/* ---------- 历史页 ---------- */
.history-list { display: flex; flex-direction: column; gap: 8px; }
.history-item {
  display: flex; align-items: flex-start; gap: 12px; background: var(--bg-card);
  border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px;
}
.h-body { flex: 1; min-width: 0; }
.h-title { font-size: 13.5px; font-weight: 600; display: flex; gap: 8px; align-items: center; }
.h-flow { font-size: 11px; color: var(--text-dim); font-weight: 400; }
.h-comment { font-size: 12.5px; color: var(--text); margin-top: 4px; }
.h-checks { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
.h-check { font-size: 11px; padding: 1px 8px; border-radius: 999px; background: var(--bg-panel); color: var(--text-dim); }
.h-check.error { color: var(--red); }
.h-check.warn { color: var(--yellow); }
.h-time { font-size: 11.5px; color: var(--text-dim); flex-shrink: 0; }

.empty {
  font-size: 13px; color: var(--text-dim); text-align: center;
  padding: 28px 16px; border: 1px dashed var(--border); border-radius: 10px;
}

.modal-mask {
  position: fixed; inset: 0; background: rgba(8, 10, 15, 0.75);
  display: flex; align-items: center; justify-content: center;
  z-index: 200; padding: 24px; backdrop-filter: blur(3px);
}
.modal.mini { width: min(520px, 100%); }
.modal {
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: 14px;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.55); display: flex; flex-direction: column;
}
.modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 22px; border-bottom: 1px solid var(--border);
}
.modal-head h2 { margin: 0; font-size: 17px; }
.modal-body { padding: 20px 22px; }
.modal-foot {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 22px; border-top: 1px solid var(--border);
}
.rework-intro { font-size: 13.5px; line-height: 1.7; margin: 0 0 8px; }
.rework-intro.dim { color: var(--text-dim); font-size: 12.5px; }

.badge {
  font-size: 11px; padding: 2px 9px; border-radius: 999px; flex-shrink: 0;
  background: var(--bg-panel); border: 1px solid var(--border); color: var(--text-dim);
}
.badge.pending { color: var(--yellow); border-color: rgba(251, 191, 36, 0.4); }
.badge.running { color: var(--accent); border-color: rgba(var(--accent-2-rgb), 0.4); }
.badge.done { color: var(--green); border-color: rgba(52, 211, 153, 0.4); }
.badge.failed { color: var(--red); border-color: rgba(248, 113, 113, 0.4); }
.badge.cancelled { color: var(--text-dim); }
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
.form-msg { color: var(--red); font-size: 12.5px; }
.bar { flex: 1; height: 6px; background: var(--bg-card); border-radius: 3px; overflow: hidden; }
.bar-inner { height: 100%; background: var(--accent); border-radius: 3px; transition: width 0.3s; }
.progress-text { font-size: 11.5px; color: var(--text-dim); white-space: nowrap; }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot.ready { background: var(--green); }
.dot.unavailable { background: var(--text-dim); }
</style>
