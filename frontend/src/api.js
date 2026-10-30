const BASE = '/api'

async function request(path, options = {}) {
  const resp = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`
    let data = null
    try {
      data = await resp.json()
      if (data.detail) {
        detail = typeof data.detail === 'string'
          ? data.detail
          : (data.detail.message || JSON.stringify(data.detail))
      }
    } catch {
      /* ignore */
    }
    // 3A：把状态码与结构化 detail 挂到错误上——
    // 409 的 references / loss、422 的 issues / score 都靠它展示
    const err = new Error(detail)
    err.status = resp.status
    err.data = data
    throw err
  }
  if (resp.status === 204) return null
  return resp.json()
}

export const api = {
  health: () => request('/health'),
  // refresh=true 跳过服务端的探测缓存（「重新检测」按钮用，轮询时不带）
  launchStatus: (refresh = false) => request(`/launch/status${refresh ? '?refresh=1' : ''}`),
  engines: () => request('/engines'),
  jobs: () => request('/jobs?limit=200'),
  createJob: (data) => request('/jobs', { method: 'POST', body: JSON.stringify(data) }),
  upgradeJob: (id, data = {}) => request(`/jobs/${id}/upgrade`, { method: 'POST', body: JSON.stringify(data) }),
  cancelJob: (id) => request(`/jobs/${id}/cancel`, { method: 'POST' }),
  retryJob: (id) => request(`/jobs/${id}/retry`, { method: 'POST' }),
  deleteJob: (id) => request(`/jobs/${id}`, { method: 'DELETE' }),
  hardware: () => request('/hardware'),
  presets: () => request('/presets'),
  jobMetadata: (id) => request(`/jobs/${id}/metadata`),

  // ---------- 分镜工作台 ----------
  projects: () => request('/projects'),
  createProject: (data) => request('/projects', { method: 'POST', body: JSON.stringify(data) }),
  getProject: (id) => request(`/projects/${id}`),
  updateProject: (id, data) => request(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteProject: (id) => request(`/projects/${id}`, { method: 'DELETE' }),

  getScript: (id) => request(`/projects/${id}/script`),
  saveScript: (id, text) => request(`/projects/${id}/script`, { method: 'PUT', body: JSON.stringify({ text }) }),
  importScript: (id, text, mode = 'rebuild', extra = {}) =>
    request(`/projects/${id}/script/import`, { method: 'POST', body: JSON.stringify({ text, mode, ...extra }) }),

  createScene: (pid, data) => request(`/projects/${pid}/scenes`, { method: 'POST', body: JSON.stringify(data) }),
  updateScene: (id, data) => request(`/scenes/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteScene: (id) => request(`/scenes/${id}`, { method: 'DELETE' }),
  reorderShots: (sceneId, ids) => request(`/scenes/${sceneId}/shots/reorder`, { method: 'POST', body: JSON.stringify({ ids }) }),

  createShot: (sceneId, data) => request(`/scenes/${sceneId}/shots`, { method: 'POST', body: JSON.stringify(data) }),
  updateShot: (id, data) => request(`/shots/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteShot: (id) => request(`/shots/${id}`, { method: 'DELETE' }),

  generateShot: (id, data) => request(`/shots/${id}/generate`, { method: 'POST', body: JSON.stringify(data) }),
  reviewShot: (id, action, versionId = '', comment = '', stage = 'video', expectedVersionId = '') =>
    request(`/shots/${id}/review`, { method: 'POST', body: JSON.stringify({ action, version_id: versionId, comment, stage, expected_version_id: expectedVersionId }) }),
  shotVersions: (id) => request(`/shots/${id}/versions`),
  adoptVersion: (shotId, versionId) => request(`/shots/${shotId}/versions/${versionId}/adopt`, { method: 'POST' }),
  deleteVersion: (shotId, versionId) => request(`/shots/${shotId}/versions/${versionId}`, { method: 'DELETE' }),

  // ---------- 角色与场景资产库 ----------
  characters: (pid) => request(`/projects/${pid}/characters`),
  createCharacter: (pid, data) => request(`/projects/${pid}/characters`, { method: 'POST', body: JSON.stringify(data) }),
  updateCharacter: (id, data) => request(`/characters/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteCharacter: (id) => request(`/characters/${id}`, { method: 'DELETE' }),
  setShotCharacters: (shotId, ids) => request(`/shots/${shotId}/characters`, { method: 'PUT', body: JSON.stringify({ character_ids: ids }) }),

  // ---------- 动作模板（PR-10 后半：对峙/追击/格挡/收招 + 项目自定义） ----------
  motionTemplates: (projectId = '') => request(`/motion-templates${projectId ? `?project_id=${projectId}` : ''}`),
  createMotionTemplate: (data) => request('/motion-templates', { method: 'POST', body: JSON.stringify(data) }),
  updateMotionTemplate: (id, data) => request(`/motion-templates/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteMotionTemplate: (id) => request(`/motion-templates/${id}`, { method: 'DELETE' }),
  uploadFile: async (file) => {
    const fd = new FormData()
    fd.append('file', file)
    const resp = await fetch('/api/uploads', { method: 'POST', body: fd })
    if (!resp.ok) throw new Error((await resp.json()).detail || '上传失败')
    return resp.json()
  },

  // ---------- TTS 配音 ----------
  ttsVoices: () => request('/tts/voices'),
  generateVoice: (shotId, data) => request(`/shots/${shotId}/voice`, { method: 'POST', body: JSON.stringify(data) }),
  deleteVoice: (voiceId) => request(`/voices/${voiceId}`, { method: 'DELETE' }),

  // ---------- 多轨道 ----------
  audioTracks: (pid, kind) => request(`/projects/${pid}/audio-tracks${kind ? `?kind=${kind}` : ''}`),
  createAudioTrack: (pid, data) => request(`/projects/${pid}/audio-tracks`, { method: 'POST', body: JSON.stringify(data) }),
  updateAudioTrack: (id, data) => request(`/audio-tracks/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteAudioTrack: (id) => request(`/audio-tracks/${id}`, { method: 'DELETE' }),

  subtitles: (pid) => request(`/projects/${pid}/subtitles`),
  autoSubtitles: (pid) => request(`/projects/${pid}/subtitles/auto`, { method: 'POST' }),
  replaceSubtitles: (pid, items) => request(`/projects/${pid}/subtitles`, { method: 'PUT', body: JSON.stringify({ items }) }),
  updateSubtitle: (id, data) => request(`/subtitles/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteSubtitle: (id) => request(`/subtitles/${id}`, { method: 'DELETE' }),
  exportSrt: (pid) => request(`/projects/${pid}/subtitles/srt`),

  // ---------- 质量审核 / 历史 / 返工 ----------
  shotQuality: (id, stage = 'video') => request(`/shots/${id}/quality?stage=${stage}`),
  reworkShot: (id, cascade = false, comment = '') =>
    request(`/shots/${id}/rework`, { method: 'POST', body: JSON.stringify({ cascade, comment }) }),
  projectHistory: (pid) => request(`/projects/${pid}/history`),

  timeline: (pid) => request(`/projects/${pid}/timeline`),
  timelineWarnings: (pid) => request(`/projects/${pid}/timeline/warnings`),
  waveform: (url, points = 64) => request(`/audio/waveform?url=${encodeURIComponent(url)}&points=${points}`),
  syncTimeline: (pid) => request(`/projects/${pid}/timeline/sync`, { method: 'POST' }),
  updateTimelineItem: (id, data) => request(`/timeline/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  reorderTimeline: (pid, ids) => request(`/projects/${pid}/timeline/reorder`, { method: 'POST', body: JSON.stringify({ ids }) }),
  removeTimelineItem: (id) => request(`/timeline/${id}`, { method: 'DELETE' }),

  createExport: (pid, params = {}) => request(`/projects/${pid}/export`, { method: 'POST', body: JSON.stringify(params) }),
  listExports: (pid) => request(`/projects/${pid}/exports`),
  getExport: (id) => request(`/exports/${id}`),

  // ---------- ComfyUI ----------
  comfyStatus: () => request('/comfy/status'),
  comfyWorkflow: () => request('/comfy/workflow'),
  saveComfyWorkflow: (data) => request('/comfy/workflow', { method: 'PUT', body: JSON.stringify(data) }),
  comfyPresets: () => request('/comfy/presets'),
  applyComfyPreset: (id) => request(`/comfy/presets/${id}/apply`, { method: 'POST' }),
  comfyCapabilities: () => request('/comfy/capabilities'),

  // ---------- 模型服务（LLM / 图像生成 / 服务配置） ----------
  servicesStatus: () => request('/services/status'),
  servicesConfig: () => request('/services/config'),
  saveLlmConfig: (data) => request('/services/llm', { method: 'PUT', body: JSON.stringify(data) }),
  saveImagegenConfig: (data) => request('/services/imagegen', { method: 'PUT', body: JSON.stringify(data) }),
  saveComfyConfig: (data) => request('/services/comfy', { method: 'PUT', body: JSON.stringify(data) }),
  testLlm: () => request('/services/llm/test', { method: 'POST' }),
  testImagegen: () => request('/services/imagegen/test', { method: 'POST' }),

  // ---------- MCP（AI 助手接入） ----------
  mcpState: () => request('/services/mcp'),
  mcpReveal: () => request('/services/mcp/reveal', { method: 'POST' }),
  saveMcpConfig: (data) => request('/services/mcp', { method: 'PUT', body: JSON.stringify(data) }),

  llmScript: (data) => request('/llm/script', { method: 'POST', body: JSON.stringify(data) }),
  llmPolish: (data) => request('/llm/polish', { method: 'POST', body: JSON.stringify(data) }),
  llmDialogue: (data) => request('/llm/dialogue', { method: 'POST', body: JSON.stringify(data) }),
  imagegenGenerate: (data) => request('/imagegen/generate', { method: 'POST', body: JSON.stringify(data) }),

  // ---------- 小说改编（PR-N01：导入与章节管理） ----------
  novels: () => request('/novels'),
  importNovel: (file, extra = {}) => {
    const fd = new FormData()
    fd.append('file', file)
    for (const [k, v] of Object.entries(extra)) if (v) fd.append(k, v)
    return fetch('/api/novels', { method: 'POST', body: fd }).then(async (resp) => {
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        const err = new Error(data.detail || `导入失败 ${resp.status}`)
        err.status = resp.status
        throw err
      }
      return data
    })
  },
  getNovel: (id) => request(`/novels/${id}`),
  updateNovel: (id, data) => request(`/novels/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteNovel: (id) => request(`/novels/${id}`, { method: 'DELETE' }),
  novelChapters: (id) => request(`/novels/${id}/chapters`),
  novelChapter: (id, cid) => request(`/novels/${id}/chapters/${cid}`),
  updateNovelChapter: (id, cid, data) => request(`/novels/${id}/chapters/${cid}`, { method: 'PATCH', body: JSON.stringify(data) }),
  splitNovelChapter: (id, cid, offset) => request(`/novels/${id}/chapters/${cid}/split`, { method: 'POST', body: JSON.stringify({ offset }) }),
  mergeNovelChapters: (id, chapterId, nextChapterId) => request(`/novels/${id}/chapters/merge`, { method: 'POST', body: JSON.stringify({ chapter_id: chapterId, next_chapter_id: nextChapterId }) }),
  reparseNovel: (id) => request(`/novels/${id}/reparse`, { method: 'POST' }),

  // ---------- 故事圣经（PR-N02：AI 章节分析 + 设定管理） ----------
  novelAnalyzeStart: (id, body = {}) => request(`/novels/${id}/analyze`, { method: 'POST', body: JSON.stringify(body) }),
  novelAnalyzeStatus: (id) => request(`/novels/${id}/analyze`),
  novelBible: (id) => request(`/novels/${id}/bible`),
  novelBibleGenerate: (id) => request(`/novels/${id}/bible/generate`, { method: 'POST' }),
  novelBibleApprove: (id, note = '') => request(`/novels/${id}/bible/approve`, { method: 'POST', body: JSON.stringify({ note }) }),
  novelBibleVersion: (id, version) => request(`/novels/${id}/bible/versions/${version}`),
  bibleEntityCreate: (id, kind, fields) => request(`/novels/${id}/bible/${kind}`, { method: 'POST', body: JSON.stringify(fields) }),
  bibleEntityUpdate: (id, kind, eid, fields) => request(`/novels/${id}/bible/${kind}/${eid}`, { method: 'PUT', body: JSON.stringify(fields) }),
  bibleEntityDelete: (id, kind, eid) => request(`/novels/${id}/bible/${kind}/${eid}`, { method: 'DELETE' }),

  // ---------- 分集与剧本（PR-N03：AI 分集规划 + 动漫剧本 + 交接项目） ----------
  novelEpisodes: (id) => request(`/novels/${id}/episodes`),
  novelOutlineGenerate: (id, body = {}) => request(`/novels/${id}/episodes/generate`, { method: 'POST', body: JSON.stringify(body) }),
  novelEpisodeCreate: (id, body) => request(`/novels/${id}/episodes`, { method: 'POST', body: JSON.stringify(body) }),
  novelEpisode: (id, eid) => request(`/novels/${id}/episodes/${eid}`),
  novelEpisodeUpdate: (id, eid, body) => request(`/novels/${id}/episodes/${eid}`, { method: 'PUT', body: JSON.stringify(body) }),
  novelEpisodeDelete: (id, eid) => request(`/novels/${id}/episodes/${eid}`, { method: 'DELETE' }),
  novelEpisodeApprove: (id, eid, note = '') => request(`/novels/${id}/episodes/${eid}/approve`, { method: 'POST', body: JSON.stringify({ note }) }),
  novelEpisodeVersion: (id, eid, version) => request(`/novels/${id}/episodes/${eid}/versions/${version}`),
  novelScriptGenerate: (id, eid) => request(`/novels/${id}/episodes/${eid}/script/generate`, { method: 'POST' }),
  novelScript: (id, eid) => request(`/novels/${id}/episodes/${eid}/script`),
  novelScriptUpdate: (id, eid, body) => request(`/novels/${id}/episodes/${eid}/script`, { method: 'PUT', body: JSON.stringify(body) }),
  novelScriptApprove: (id, eid, note = '') => request(`/novels/${id}/episodes/${eid}/script/approve`, { method: 'POST', body: JSON.stringify({ note }) }),
  novelScriptVersion: (id, eid, version) => request(`/novels/${id}/episodes/${eid}/script/versions/${version}`),
  novelScriptHandoff: (id, eid) => request(`/novels/${id}/episodes/${eid}/script/handoff`, { method: 'POST' }),

  // ---------- 结构化分镜（PR-N04：逐镜头草案 + 审核 + 交接项目分镜） ----------
  novelShotPlan: (id, eid) => request(`/novels/${id}/episodes/${eid}/shots`),
  novelShotPlanGenerate: (id, eid) => request(`/novels/${id}/episodes/${eid}/shots/generate`, { method: 'POST' }),
  novelShotItemUpdate: (id, eid, iid, body) => request(`/novels/${id}/episodes/${eid}/shots/${iid}`, { method: 'PUT', body: JSON.stringify(body) }),
  novelShotItemReorder: (id, eid, iid, direction) => request(`/novels/${id}/episodes/${eid}/shots/${iid}/reorder`, { method: 'POST', body: JSON.stringify({ direction }) }),
  novelShotPlanApprove: (id, eid, note = '') => request(`/novels/${id}/episodes/${eid}/shots/approve`, { method: 'POST', body: JSON.stringify({ note }) }),
  novelShotPlanVersion: (id, eid, version) => request(`/novels/${id}/episodes/${eid}/shots/versions/${version}`),
  novelShotPlanHandoff: (id, eid) => request(`/novels/${id}/episodes/${eid}/shots/handoff`, { method: 'POST' }),
  novelShotValidate: (id, eid) => request(`/novels/${id}/episodes/${eid}/shots/validate`),

  // ---------- 提交批次（PR-N05：预览转正与安全提交） ----------
  novelShotSubmit: (id, eid, body) => request(`/novels/${id}/episodes/${eid}/shots/submit`, { method: 'POST', body: JSON.stringify(body) }),
  novelShotSubmissions: (id, eid) => request(`/novels/${id}/episodes/${eid}/shots/submissions`),
  novelShotSubmitRetry: (id, eid, sid) => request(`/novels/${id}/episodes/${eid}/shots/submissions/${sid}/retry`, { method: 'POST' }),
  novelShotSubmitCancel: (id, eid, sid) => request(`/novels/${id}/episodes/${eid}/shots/submissions/${sid}/cancel`, { method: 'POST' }),
}
