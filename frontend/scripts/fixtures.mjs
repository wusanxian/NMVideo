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
      recommended: 'h3',
      // 口径：H3 走 ComfyUI 插件通道（FreeVideo/VDN-H3），8GB 显存 / 16GB 内存起；
      // 原生通道（52GB 内存 / 139GB 磁盘）只作为说明出现，不参与推荐。
      summary: '推荐 MiniMax H3（ComfyUI 插件通道：8GB 显存 / 32GB 内存即可，需下载 92GB 素材）',
      options: [
        {
          id: 'h3', label: '画质优先', name: 'MiniMax H3', subtitle: '走 ComfyUI 插件通道 · 分块流式，慢但画质最好',
          channel: 'comfyui',
          available: true, tier: 'plugin', tier_label: '插件分块流式',
          weights_gb: 92.3, need_vram_gb: 8, need_ram_gb: 16, need_disk_gb: 100.3,
          max_pixels: 0, disk_tight: false,
          note: '8GB 显存按块流式运行：官方实测 4060 Ti 8GB + 64GB 内存，1344×768 10 秒约 10 分钟；内存越少回读磁盘的块越多、越慢',
          blockers: [],
          packs: [
            { id: 'video', name: '视频模型', size_gib: 61.7, why: '生成画面的主体权重，按 50 块分片，按显存/内存/磁盘分级放置' },
            { id: 'text', name: '文本编码器', size_gib: 14.6, why: '把提示词与参考图理解成模型输入，独立进程跑完即退出、不占推理内存' },
            { id: 'decoder', name: '视频与音频解码器', size_gib: 10.3, why: '把潜变量还原成画面与声音，低显存下按块流式解码' },
            { id: 'cache', name: '采样与加速缓存', size_gib: 5.7, why: '8 步蒸馏与画质提升所需的附加权重，装上才跑得动低步数' },
          ],
          native: {
            available: false, min_ram_gb: 52, need_disk_gb: 139,
            reason: '内存不足：即使 int4 量化也要约 38GiB 权重常驻内存（含余量 52GiB），当前可用 32GiB。',
          },
        },
        {
          id: 'wan22', label: '低显存 · 快速出片', name: 'Wan 2.2 TI2V-5B', subtitle: '480p / 5 秒档 · 8GB 显存直接跑，出片快',
          available: true, tier: 'fp8', tier_label: 'fp8',
          weights_gb: 32, need_ram_gb: 0, need_disk_gb: 0, max_pixels: 0,
          // 权重默认不下载：没下就如实说，并给一条去「设置 → 模型」的路
          weights_ready: false,
          note: '权重未下载（约 32GB）· 到「设置 → 模型」里自己点下载，下完即可出片；本程序不会自动拉这份权重',
          blockers: [],
        },
      ],
      downloads: {
        total_gib: 92.3, need_disk_gb: 100.3,
        packs: [
          { id: 'video', name: '视频模型', size_gib: 61.7, why: '生成画面的主体权重' },
          { id: 'text', name: '文本编码器', size_gib: 14.6, why: '理解提示词与参考图' },
          { id: 'decoder', name: '视频与音频解码器', size_gib: 10.3, why: '还原画面与声音' },
          { id: 'cache', name: '采样与加速缓存', size_gib: 5.7, why: '8 步蒸馏与画质提升' },
        ],
      },
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
  // 资源占用明细：前端只在「磁盘不够」时才调，当前 fixture 是磁盘够用的现场，
  // 所以这段不会渲染出来；把它放这儿是为了让 mock 端点的返回结构与后端一致
  // （真机「磁盘差 19GB、误下的 Wan 占 29GB」那个场景用真实数据验证过）。
  '/api/launch/storage': {
    ok: true,
    path: 'D:\\work\\NMVideo\\backend\\models',
    total_gb: 31.9,
    idle_gb: 0,
    items: [{ id: 'wan-ti2v-5b', size_gb: 29.0, engine: 'wan22', in_use: true }],
  },
  '/api/projects': [{ id: 'demo-project', name: '测试项目', description: '用于渲染验收的样例项目', created_at: '2026-10-27T10:00:00' }],  // 模型库：权重默认不下载，界面上要能看出「未下载」和「点哪儿下」
  '/api/models': {
    models_dir: 'D:\\work\\NMVideo\\backend\\models',
    disk_free_gb: 116.1,
    downloading: false,
    items: [
      {
        id: 'wan-ti2v-5b', label: '低显存 · 快速出片权重',
        detail: '8GB 显存直接跑 480p / 5 秒档；本程序不再默认下载，需要时自己点',
        engine: 'wan22', repo: 'Wan-AI/Wan2.2-TI2V-5B-Diffusers', approx_gb: 32,
        state: 'absent', size_gb: 0, path: 'D:\\work\\NMVideo\\backend\\models\\wan-ti2v-5b', task: null,
      },
      {
        id: 'anime', label: '动漫引擎权重',
        detail: '动漫风格底模 + 运动模块，CPU 也能跑（分钟级出片），约 5GB',
        engine: 'anime', repo: 'dreamlike-art/dreamlike-anime-1.0', approx_gb: 5,
        state: 'ready', size_gb: 4.8, path: 'D:\\work\\NMVideo\\backend\\models\\anime-diffusers', task: null,
      },
    ],
  },
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
  // 模型库：可选档的权重默认不下载，页面必须把「未下载」和「点哪儿下」讲清楚
  ['/settings?tab=models', ['设置', '模型库', '低显存 · 快速出片权重', '下载约 32GB']],
  ['/settings?tab=prefs', ['设置', '检查更新', '当前版本']],
  ['/services', ['设置', '视频工作流 · ComfyUI']],
]
