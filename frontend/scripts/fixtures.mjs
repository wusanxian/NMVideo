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
  // 启动页：首启现场（ComfyUI 还没起来）——正是要验收的那个状态
  '/api/launch/status': {
    ok: false,
    ready: false,
    version: '0.1.9',
    progress: 0.333,
    steps: [
      { key: 'backend', label: '本地服务', state: 'done', detail: '已就绪 · v0.1.9' },
      { key: 'comfyui', label: 'ComfyUI', state: 'blocked', detail: '未检测到 http://127.0.0.1:8188——请先启动 ComfyUI（桌面端「系统管理」或命令行）' },
      { key: 'engine', label: '生成引擎', state: 'blocked', detail: '默认引擎 comfyui 走 ComfyUI 通道，服务未连接时无法出片' },
    ],
    comfyui: { url: 'http://127.0.0.1:8188', online: false, vram_total_gb: 0, vram_free_gb: 0, ram_free_gb: 0, queue_running: 0, queue_pending: 0 },
    paths: {
      project: 'D:\\work\\NMVideo',
      backend: 'D:\\work\\NMVideo\\backend',
      db: 'D:\\work\\NMVideo\\backend\\aivideo.db',
      models: { path: 'D:\\work\\NMVideo\\backend\\models', size_gb: 16.1, files: 128 },
      outputs: { path: 'D:\\work\\NMVideo\\backend\\outputs', size_gb: 3.4, files: 260 },
    },
    disk: { free_gb: 116.1, total_gb: 300 },
    hardware: {
      gpu_name: 'NVIDIA GeForce RTX 4060', vram_gb: 8, free_vram_gb: 1.9, ram_gb: 32,
      available_ram_gb: 17.3, device: 'cuda', compute_cap: 'sm_89', disk_free_gb: 116.1,
      torch_cuda_available: true, wan_model_id: 'ti2v-5b', wan_quant: 'fp8',
    },
    // 引擎推荐：本机 8G/32G 的真实结论——H3 内存不够，落到 Wan 2.2
    recommend: {
      recommended: 'wan22',
      summary: 'H3 需 52GB 内存 / 139GB 磁盘，本机 32GB 内存 / 116GB 磁盘不满足 → 推荐 Wan 2.2 TI2V-5B',
      options: [
        {
          id: 'h3', name: 'MiniMax H3', subtitle: '画质旗舰 · 走 ComfyUI 通道',
          available: false, tier: 'int4', tier_label: 'int4（不可用）',
          weights_gb: 37.8, need_ram_gb: 52, need_disk_gb: 139, max_pixels: 196608,
          note: 'int4 需要 52GB 内存，当前 32GB。',
          blockers: ['内存不足：即使 int4 量化也要约 38GiB 权重常驻内存（含余量 52GiB），当前可用 32GiB。'],
        },
        {
          id: 'wan22', name: 'Wan 2.2 TI2V-5B', subtitle: '低显存主力 · 480p / 5 秒',
          available: true, tier: 'fp8', tier_label: 'fp8',
          weights_gb: 0, need_ram_gb: 0, need_disk_gb: 0, max_pixels: 0,
          note: 'ti2v-5b · fp8 量化 · 8GB 显存可跑 480p', blockers: [],
        },
      ],
    },
    deps: [
      { id: 'torch', name: 'PyTorch', why: '视频生成推理底座', installed: true },
      { id: 'diffusers', name: 'diffusers', why: 'H3 / Wan 管线', installed: true },
      { id: 'torchao', name: 'torchao', why: '权重量化 int4/int8/fp8', installed: false },
      { id: 'edge_tts', name: 'edge-tts', why: '神经网络配音（联网）', installed: true },
      { id: 'pyttsx3', name: 'pyttsx3', why: '离线配音兜底（SAPI5）', installed: false },
      { id: 'imageio_ffmpeg', name: 'imageio-ffmpeg', why: '内置 ffmpeg，合成 MP4 / 导出成片', installed: true },
    ],
    resource: { guard: 'enforce', watchdog: true, ram_floor_gb: 1, ram_tight_gb: 2.5 },
  },
  '/api/projects': [{ id: 'demo-project', name: '测试项目', description: '用于渲染验收的样例项目', created_at: '2026-10-27T10:00:00' }],
  '/api/projects/demo-project': PROJECT,
  '/api/gallery': [JOB],
  '/api/novels': [{ id: 'nv_1', title: '长夜将至', author: '测试', chapter_count: 3, status: 'imported', created_at: '2026-10-27T10:00:00' }],
  // 服务状态：字段与后端 routers/services.py 的返回一致（configured/online/base_url/note）
  '/api/services/status': {
    comfyui: { configured: true, online: true, base_url: 'http://127.0.0.1:8188', note: '' },
    llm: { configured: true, online: true, base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat', api_key_set: true, note: '支持 DeepSeek/通义/Kimi/Ollama 等 OpenAI 兼容端点' },
    imagegen: { configured: false, online: false, base_url: '', model: '', api_key_set: false, note: '支持任意 OpenAI 兼容 images 端点' },
    tts: { configured: true, online: true, note: 'edge-tts 可用（14 个中文音色）' },
  },
  '/api/services/config': {
    llm: { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat', api_key: 'sk-****cdef' },
    imagegen: { base_url: '', model: '', api_key: '' },
    comfy_url: 'http://127.0.0.1:8188',
  },
  '/api/services/mcp': { enabled: false, token_masked: '', has_token: false },
  '/api/comfy/status': { available: true, url: 'http://127.0.0.1:8188', node_count: 928, version: '0.3.x' },
  '/api/comfy/workflow': { nodes: [], links: [] },
  '/api/comfy/presets': [{ id: 'vdn-minimax-h3', name: 'H3 文生视频', modes: ['t2v'] }],
  '/api/comfy/capabilities': { modes: ['t2v'], missing_nodes: [] },
}

/** 页面 → 渲染完成后正文必须出现的标记 */
export const PAGE_EXPECT = [
  // mock 是「首启现场」（launch/status ready=false），所以 / 会被引导到环境准备清单
  ['/', ['环境准备', 'ComfyUI 地址']],
  ['/launch', ['环境准备', '需要安装 / 对接的工具', '已按配置推荐', '打开目录']],
  ['/generate', ['ComfyUI 通道', '本机进程内通道', 'AnimeDiff']],
  ['/queue', ['雨夜霓虹街景', '已自动降档']],
  ['/gallery', ['雨夜霓虹街景']],
  ['/projects', ['测试项目']],
  ['/novel', ['长夜将至']],
  ['/projects/demo-project', ['测试项目', '主角推门进入密室']],
  ['/comfy', ['ComfyUI']],
  // 设置页默认停在「服务」；老链接 /services 会重定向到这里
  ['/settings', ['设置', '视频工作流 · ComfyUI', 'AI 助手接入 · MCP']],
  ['/settings?tab=env', ['设置', '兼容性', '资源守卫档位']],
  ['/settings?tab=prefs', ['设置', '检查更新', '当前版本']],
  ['/services', ['设置', '视频工作流 · ComfyUI']],
]
