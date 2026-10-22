# REST API 摘要

AI 视频工坊后端基于 FastAPI，默认运行在 `http://127.0.0.1:8000`（本地优先，不对外暴露）。

所有端点按模块列出。配置类接口（`/api/services/*`）返回的配置中密钥字段已打码，不会回传明文 token。

## 引擎与硬件

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/engines` | 引擎列表与状态（含硬件探测） |
| GET | `/api/hardware` | 硬件探测结果与 Wan 推荐配置 |
| GET | `/api/presets` | 质量档位列表 |

## 任务（jobs）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/jobs` | 创建任务（+preset/preview 字段） |
| GET | `/api/jobs` | 任务列表 |
| GET | `/api/jobs/{id}` | 任务详情（含进度/耗时） |
| GET | `/api/jobs/{id}/metadata` | 读回视频内嵌生成参数 |
| POST | `/api/jobs/{id}/cancel` | 取消任务 |
| DELETE | `/api/jobs/{id}` | 删除任务及产物 |
| GET | `/outputs/{id}.mp4` | 视频文件 |

## 项目 / 场景 / 分镜（storyboard）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/projects` | 项目列表 / 创建 |
| GET/PATCH/DELETE | `/api/projects/{id}` | 项目详情 / 改 / 删 |
| PUT/POST | `/api/projects/{id}/script` `/script/import` | 保存剧本 / AI 拆分重建 |
| GET/POST | `/api/projects/{id}/scenes` | 场景列表 / 新建 |
| PATCH/DELETE | `/api/scenes/{id}` | 场景改 / 删 |
| POST | `/api/scenes/{id}/shots` | 新建分镜 |
| PATCH/DELETE | `/api/shots/{id}` | 分镜改 / 删 |
| POST | `/api/shots/{id}/generate` | 分镜发起生成（engine/mode：t2v/i2v/first_last/continuation） |
| POST | `/api/shots/{id}/review` | 审核（approve/rework + 意见，质检快照留痕） |
| POST | `/api/shots/{id}/rework` | 返工（cascade=连同后续镜头） |
| GET | `/api/shots/{id}/quality` | 镜头质量检查 |
| GET | `/api/shots/{id}/versions` | 候选版本列表 |
| POST | `/api/shots/{id}/versions/{vid}/adopt` | 采用某版本 |

## 角色与资产（assets）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/projects/{id}/characters` | 角色卡列表 / 新建 |
| PATCH/DELETE | `/api/characters/{id}` | 角色卡改 / 删 |
| PUT | `/api/shots/{id}/characters` | 分镜绑定/解绑角色 |
| POST | `/api/uploads` | 上传图片/音频（参考图、音乐、音效） |

## 配音 / 音乐 / 字幕 / 时间线（audio & timeline）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tts/voices` | TTS 音色目录（14 个中文神经音色） |
| POST | `/api/shots/{id}/voice` | 生成对白配音 |
| DELETE | `/api/voices/{id}` | 删除配音 |
| GET/POST | `/api/projects/{id}/audio-tracks` | 音乐/音效轨列表 / 新增 |
| PATCH/DELETE | `/api/audio-tracks/{id}` | 音轨改 / 删 |
| GET/PUT | `/api/projects/{id}/subtitles` | 字幕列表 / 整轨替换 |
| POST | `/api/projects/{id}/subtitles/auto` | 从对白自动生成字幕 |
| GET | `/api/projects/{id}/subtitles/srt` | 导出 SRT |
| GET | `/api/projects/{id}/timeline` | 时间线片段 |
| POST | `/api/projects/{id}/timeline/sync` | 同步已通过镜头进时间线 |
| POST | `/api/projects/{id}/export` | 创建导出（拼接 + 混音 + 字幕烧录） |
| GET | `/api/exports/{id}` | 导出进度 |
| GET | `/api/projects/{id}/history` | 审核历史 |

## ComfyUI 工作流

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PUT/DELETE | `/api/comfy/status` `/api/comfy/workflow` | ComfyUI 状态 / 工作流模板管理 |
| GET | `/api/comfy/presets` | 内置工作流预设（MiniMax H3 / Wan / AnimateDiff） |
| POST | `/api/comfy/presets/{id}/apply` | 应用内置预设为当前模板 |

## 模型服务（services，key 打码）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/services/status` | 四类模型服务健康状态（ComfyUI/LLM/图像生成/TTS） |
| GET/PUT | `/api/services/config` `/services/llm` `/services/imagegen` `/services/comfy` | 服务配置读写（key 打码） |
| POST | `/api/services/llm/test` `/api/services/imagegen/test` | 服务连通测试 |

## AI 编剧与图像生成（llm / imagegen）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/llm/script` | AI 编剧（梗概 → 标准格式剧本） |
| POST | `/api/llm/polish` `/api/llm/dialogue` | 提示词优化 / 台词润色 |
| POST | `/api/imagegen/generate` | 图像生成（参考图/概念图/首帧，落盘 outputs/assets/） |
