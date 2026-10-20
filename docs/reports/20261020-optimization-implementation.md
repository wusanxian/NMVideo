# FreeVideo 增量优化实施记录

日期：2026-10-20。基线：`d4187ba`。本轮修改保留在工作区，未提交或发布。

用户确认尚未安装 ComfyUI/FreeVideo，先完成代码接入。因此本报告区分自动化验证与实机出片验收。

## 已落地内容

| 方案批次 | 本轮结果 | 主要文件 |
|---|---|---|
| PR-A 生成契约 | 普通生成与分镜共用档位、显式参数、预览与本地尺寸归一化；本地引擎拒绝图片条件模式；独立首帧可通过两个映射别名注入；修复 FFmpeg 中文及特殊字符元数据读回 | `quality.py`、`generation.py`、`engines/*`、`video.py`、两类生成路由 |
| PR-B 快照与升级 | 原子保存工作流文件后才创建 pending 任务；指定快照缺失时拒绝读取当前模板；保存目标参数、原模板与哈希；升级保留原镜头、角色、seed、素材与父任务 | `generation.py`、`generation_assets.py`、`upgrades.py`、`db.py`、画廊 |
| PR-C FreeVideo 本地接入 | 新增 t2v、i2v/末帧续接、first_last 三个本地预设；校验实机节点接口；独立处理 H3 尺寸、固定 24fps、帧数和八步采样；通过原生 `preview/refine_from` 继续二采 | `freevideo.py`、`comfy_workflows/presets/freevideo_h3_*.json`、Comfy 引擎 |
| PR-D 角色视觉条件 | FreeVideo 使用冻结角色卡构建实际 `LoadImage → FreeVideoReference` 与 `FreeVideoLoRA` 链；检查依赖节点和 LoRA；记录消费图片的内容哈希与冻结副本 | `freevideo.py`、`generation_assets.py` |
| PR-E 成片可靠性 | 创建导出时在事务内冻结镜头版本、剪辑、对白、音轨和字幕；保留媒体内容副本；后台仅使用该次清单；混音失败明确失败，不再静默导出无声片 | `export_manifest.py`、`export.py`、`storyboard.py` |
| PR-F 资源管理基础 | 切换本地引擎前卸载旧模型；探测当前空闲显存与可用内存，测量失败显示未知；卸载策略参考当前空闲显存；记录实际量化状态，外部 ComfyUI 不冒用本机 Wan 资源信息 | `engines/local_runtime.py`、`queue_manager.py`、`hw_probe.py` |

生成准备从分镜路由抽出到独立模块；数据库和输出目录可传入准备/存档服务。未引入额外服务、队列产品或数据库。

## 使用入口

1. 普通生成或镜头编辑中勾选「先出预览」。普通高清候选会从预览保存的目标参数恢复；自定义参数同样保留。
2. 画廊点击「生成高清候选」，后端直接创建原镜头的新候选任务；原任务和历史版本保留。`parent_job_id` 标识来源。
3. 安装 FreeVideo 后，在 ComfyUI 设置应用「FreeVideo H3 本地」预设。生成前检查 `/object_info` 中的节点及接口字段。
4. FreeVideo 预览在画廊显示「继续二采」，引用服务端原预览文件与缓存；不重新选取当前角色卡或当前工作流。
5. 为角色卡关联参考图或 LoRA。仅 FreeVideo 本地适配会自动建立视觉条件链；其他自定义工作流需要各自接入，不能依据角色字段已保存推断模型使用了它们。
6. 导出保留当次成片清单及媒体副本。之后编辑时间线、换字幕或删除原对白，不会改变已经创建的该次清单。

新增 API：`POST /api/jobs/{job_id}/upgrade`，可选 body `{"preset":"fine"}`；不传沿用原目标。原生二采固定沿用来源预览的质量目标，改变目标需要新建任务。

## 数据与兼容策略

- 新增 jobs 字段：`generation_target`、`parent_job_id`、`preview_kind`、`native_output`、`generation_inputs`、`use_character_images`、`use_character_lora`；exports 新增 `manifest`。均为现有轻量迁移机制中的增量列。
- 旧导出没有 manifest，继续使用兼容路径；新导出全部使用冻结清单。
- 旧预览没有目标参数时，需要显式指定目标档位；旧 ComfyUI 快照缺少参数映射时，升级明确拒绝，不猜测映射关系。
- 旧任务从未指定快照路径时，保留引擎的旧格式兼容重建；指定路径却丢失文件时，任务明确失败，提示恢复快照。
- 新图片条件保留内容寻址副本与 sha256。升级会校验并重新上传该副本，避免服务端 input 清理后错误引用其他素材；指纹不符明确拒绝升级。
- 修复工作流编辑 API 的 `_aivideo_map`/`_meta` 别名读取，前端保存工作流时保留能力元数据。
- 续接抽帧使用来源版本与随机后缀命名，避免重做时覆盖旧任务使用的首帧文件。
- 新建镜头 API 现在会真正保存独立首尾帧字段。
- 按实际扩展名识别 ComfyUI V3 `PreviewVideo` 的原生视频输出；官方接口将 MP4 放在 `images` 数组中，不能按 PNG 序列处理。[ComfyUI 定义](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_api/latest/_ui.py)
- 对话、音乐和字幕清单冻结会额外占用磁盘；媒体按内容哈希共享副本。当前未增加自动清理策略，以免误删历史生成或导出依赖。

## 验证

自动化测试覆盖：两个入口参数一致、自定义预览目标、非法引擎模式入队前拒绝、首帧双别名、快照写入失败、指定快照丢失、模板/角色修改后的升级、FreeVideo 原生二采与角色节点连接、图片篡改拒绝、工作流编辑元数据保留、导出期间修改与删除素材，以及引擎切换的卸载顺序。

截至本轮最终验证：

- 后端全量：210 passed / 1 skipped，120.10 秒；额外新增的真实媒体导出测试单独执行 1 passed，1.51 秒。
- 实际 FFmpeg 验证：合成视频/音频 → 冻结版本与媒体 → 替换原视频、删除原音频及字幕 → 成功导出可解码、带声音、30fps 的成片，裁剪后时长约 1 秒。
- 前端 `npm run build`：通过。
- 桌面逻辑测试：18 passed（采用 `--test-isolation=none`，避免沙箱对测试子进程的 `spawn EPERM` 限制）。
- Python 源码编译与差异空白检查：通过。
- 测试使用 `.phase1/optimization-tests/` 下隔离数据库和临时目录，不使用业务数据库。

## 实机验收与后续范围

FreeVideo 协议依据本轮检查的[官方节点定义](https://github.com/FlashML-org/FreeVideo/blob/main/freevideo_engine/comfy_nodes.py)实现，并在生成前检测安装实例的接口字段；本轮没有安装 ComfyUI、FreeVideo 或模型，没有执行 GPU 推理。静态预设与 mock 通过不能替代真实节点执行、预览缓存恢复、资源峰值和角色效果验收。

角色参考图在 FreeVideo 官方定义中属于实验能力。官方暂不支持普通请求同时使用首尾帧与多参考图，因此镜头编辑提供本次参考图与 LoRA 开关，混用会在入队前明确拒绝。当前 LoRA 强度采用 1.0，尚未新增独立的角色 LoRA 强度编辑器；兼容性和角色相似度需使用实际模型检查。[官方布局约束](https://github.com/FlashML-org/FreeVideo/blob/main/freevideo_engine/comfy_media.py)

本轮 i2v 的续接仍是「抽上镜头末帧作为首帧」，尚未升级为 FreeVideo 原生长视频上下文链。长视频分段恢复、接缝预览、独立推理进程、实测历史驱动的耗时预测、完整项目包导入、依赖发布矩阵及安装更新验收继续属于后续工作。AI 导演保持后置。

下一步实机验收：在目标安装上分别完成 t2v/i2v/first_last、参考图/LoRA、原生预览后二采、取消/重连与最终混音字幕导出，再收集固定显卡配置的耗时和内存峰值。
