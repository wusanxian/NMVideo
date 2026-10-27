/**
 * 页面渲染验收用的假数据（字段与后端返回一致）。
 * 同时供 mock 服务与 jsdom 冒烟复用。
 */
export const ENGINES = [
  { id: 'comfyui', name: 'ComfyUI 工作流', description: '提交任意工作流给 ComfyUI 执行', status: 'ready', note: '', supported_modes: ['t2v', 'i2v', 'first_last', 'continuation'], execution_channel: 'comfyui', is_default: true },
  { id: 'h3', name: 'MiniMax H3（ComfyUI 预设）', description: '按模式套 freevideo_h3_* 预设', status: 'ready', note: '', supported_modes: ['t2v', 'i2v', 'first_last', 'continuation'], execution_channel: 'comfyui', is_default: false },
  { id: 'h3_native', name: 'MiniMax H3 原生', description: '本机进程内推理（8GB 量化档）', status: 'unavailable', note: '未检测到模型权重', supported_modes: ['t2v'], execution_channel: 'native', is_default: false },
  { id: 'anime', name: 'AnimeDiff', description: '动漫风格短片', status: 'ready', note: '', supported_modes: ['t2v'], execution_channel: 'native', is_default: false },
  { id: 'wan22', name: 'Wan 2.2', description: '通用视频生成', status: 'ready', note: '', supported_modes: ['t2v'], execution_channel: 'native', is_default: false },
]

export const JOB = {
  id: 'job_1', engine: 'comfyui', prompt: '雨夜霓虹街景', negative_prompt: '', width: 832, height: 480,
  num_frames: 33, fps: 12, steps: 8, guidance: 5, seed: 42, status: 'done', progress: 1,
  stage: '完成', error: null, video_url: '/outputs/a.mp4', poster_url: '/outputs/a.jpg',
  created_at: '2026-10-27T12:00:00', finished_at: '2026-10-27T12:05:00',
}

export const PROJECT = {
  id: 'demo-project', name: '测试项目', description: '用于渲染验收的样例项目',
  script: '第一场内景', scenes: [
    {
      id: 'sc_1', title: '第一场', order_index: 0,
      shots: [
        { id: 'sh_1', title: '推门', order_index: 0, prompt: '主角推门进入密室', status: 'done', mode: 't2v', duration_s: 3, dialogue: '', character_ids: [], current_version_id: 'v1', reference_image: '', first_frame_image: '', last_frame_image: '', continue_from: '', source_video_version_id: '', versions: [] },
      ],
    },
  ],
  characters: [], timeline: [],
}

export const FIXTURES = {
  '/api/health': { status: 'ok', comfyui: true, version: '0.2.0' },
  '/api/engines': ENGINES,
  '/api/jobs': [
    JOB,
    { ...JOB, id: 'job_2', status: 'running', progress: 0.42, stage: '采样中', video_url: null },
    // 资源守卫自动降档的样例：队列里要显示「已自动降档 + 原因」
    { ...JOB, id: 'job_3', status: 'pending', progress: 0, stage: '排队中', video_url: null,
      width: 640, height: 352, num_frames: 121,
      resource_note: '显存空闲 5.0GB，低于本任务需求 9.4GB（含 1GB 余量）。已自动降档 1280×720 / 121 帧 → 640×352 / 121 帧' },
  ],
  '/api/presets': [
    { id: 'draft', name: '草稿', desc: '快速预览', width: 512, height: 512, num_frames: 16, fps: 8, steps: 12, guidance: 5 },
    { id: 'high', name: '精细', desc: '高质量', width: 832, height: 480, num_frames: 33, fps: 12, steps: 8, guidance: 7 },
  ],
  '/api/hardware': { gpu_name: 'RTX 3060', vram_gb: 12, ram_gb: 32, model_id: 't2v-a14b', quant: 'fp8', offload: 'model', notes: [] },
  '/api/projects': [{ id: 'demo-project', name: '测试项目', description: '用于渲染验收的样例项目', created_at: '2026-10-27T10:00:00' }],
  '/api/projects/demo-project': PROJECT,
  '/api/gallery': [JOB],
  '/api/novels': [{ id: 'nv_1', title: '长夜将至', author: '测试', chapter_count: 3, status: 'imported', created_at: '2026-10-27T10:00:00' }],
  '/api/services/status': {
    comfyui: { online: true, url: 'http://127.0.0.1:8188', detail: '已连接' },
    llm: { ready: true, model: 'gpt-4o-mini', detail: '已配置' },
    imagegen: { ready: false, detail: '未配置' },
    tts: { ready: true, detail: 'edge-tts' },
  },
  '/api/services/config': { llm: { base_url: '', model: '', api_key_set: false }, imagegen: {}, comfy: { url: 'http://127.0.0.1:8188' } },
  '/api/services/mcp': { enabled: false, url: 'http://127.0.0.1:8000/mcp', token: '', connected: [] },
  '/api/comfy/status': { available: true, url: 'http://127.0.0.1:8188', node_count: 928, version: '0.3.x' },
  '/api/comfy/workflow': { nodes: [], links: [] },
  '/api/comfy/presets': [{ id: 'vdn-minimax-h3', name: 'H3 文生视频', modes: ['t2v'] }],
  '/api/comfy/capabilities': { modes: ['t2v'], missing_nodes: [] },
}

/** 页面 → 渲染完成后正文必须出现的标记 */
export const PAGE_EXPECT = [
  ['/', ['测试项目']],
  ['/generate', ['ComfyUI 通道', '本机进程内通道', 'AnimeDiff']],
  ['/queue', ['雨夜霓虹街景', '已自动降档']],
  ['/gallery', ['雨夜霓虹街景']],
  ['/projects', ['测试项目']],
  ['/novel', ['长夜将至']],
  ['/projects/demo-project', ['测试项目', '主角推门进入密室']],
  ['/comfy', ['ComfyUI']],
  ['/services', ['ComfyUI']],
]
