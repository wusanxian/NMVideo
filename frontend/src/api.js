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
  engines: () => request('/engines'),
  jobs: () => request('/jobs?limit=200'),
  createJob: (data) => request('/jobs', { method: 'POST', body: JSON.stringify(data) }),
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
  syncTimeline: (pid) => request(`/projects/${pid}/timeline/sync`, { method: 'POST' }),
  updateTimelineItem: (id, data) => request(`/timeline/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  reorderTimeline: (pid, ids) => request(`/projects/${pid}/timeline/reorder`, { method: 'POST', body: JSON.stringify({ ids }) }),
  removeTimelineItem: (id) => request(`/timeline/${id}`, { method: 'DELETE' }),

  createExport: (pid) => request(`/projects/${pid}/export`, { method: 'POST' }),
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

  llmScript: (data) => request('/llm/script', { method: 'POST', body: JSON.stringify(data) }),
  llmPolish: (data) => request('/llm/polish', { method: 'POST', body: JSON.stringify(data) }),
  llmDialogue: (data) => request('/llm/dialogue', { method: 'POST', body: JSON.stringify(data) }),
  imagegenGenerate: (data) => request('/imagegen/generate', { method: 'POST', body: JSON.stringify(data) }),
}
