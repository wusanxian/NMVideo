# 第二批 2C：输入上传与产物原样归档 — 实施报告

> 对应《下一阶段改造与优化方案.md》工作包 2C。
> 前置依赖 2A（ec77a38）、3A（807344c）、2B（879b17c）已提交，本包全部建立在其上。
> 目标：① i2v/首尾帧参考图经 ComfyUI input 目录上传后用返回名注入；
> ② ComfyUI 原生视频原样归档，免抽帧重编码。

## 一、变更内容

### 1. 参考图上传（`app/comfyui.py` / `app/routers/storyboard.py` / `app/engines/comfy.py`）

- `ComfyUIClient.upload_image(path)`：`POST /upload/image`（multipart，
  `overwrite=true`）把本地图片传进 ComfyUI input 目录，返回
  `{"name", "subfolder", "type"}`；非 200/缺 name → `ComfyUIError`
- `local_path_from_output_url(url)`：`/outputs/...` URL → 本机路径
  （兼容带域名的绝对 URL，取其中 `/outputs/` 段）；外链返回 None
- `upload_reference_image(client, url)`：定位 + 上传 + 拼引用名
  （有 subfolder 时为 `"subfolder/name"`，LoadImage 约定）；文件缺失/
  外链 → 明确抛错（静默丢失比明确失败更糟）
- **入队时上传**（router `generate_shot`）：模板 `_aivideo_map` 映射了
  `reference_image` 才上传（默认 SD 模板不映射，纯文生视频零影响），
  上传后的引用名写进入队快照——快照即「实际提交给 ComfyUI 的图」；
  上传失败 → 400 明确告知，不留半成品 job
- **执行时兜底**（引擎 `_resolve_workflow`）：无快照（老任务/快照被删）
  才现场上传并补写快照；有快照直接用快照里的引用名，不重复上传
- `build_shot_workflow(params, template=None)`：支持传入已加载模板
  （router 需先看 `_aivideo_map` 决定是否上传，避免重复读盘）

### 2. 原生视频原样归档（`app/engines/comfy.py` / `app/queue_manager.py` / `app/video.py`）

旧流程：ComfyUI 视频输出 → 下载 → ffmpeg 抽帧成 PNG 序列 → libx264
重编码合成 MP4（慢、且有画质损失）。新流程：

- 引擎：视频类输出（videos/gifs）下载到
  `OUTPUTS_DIR/comfy_{prompt_id}{ext}`，设置 `self.last_native_video`
  并**返回空帧列表**（契约变更见 `engines/base.py` docstring）
- 队列 `_run_job`：`last_native_video` 有值时走直通分支——
  `remux_video()` 归档 + 抽海报帧 + 清临时文件；否则走原帧序列合成
  （本地引擎/图片序列输出行为不变）
- `video.remux_video(src, out, metadata)`：
  - mp4/mov 容器：`-c copy` **码流原样** + `comment` 元数据
    （FreeVideo 式「视频自带参数」不丢，秒级完成）
  - 其它容器（webm/gif）：video→video 转码（不经过 PNG 帧序列）
  - remux 失败自动降级转码
- 队列 remux 失败再兜底：原样拷贝（丢元数据不丢成片）
- `video.extract_first_frame()`：从成片抽首帧作海报（画廊/审核用，
  失败不阻断）
- 引擎侧删除已无调用方的 `_video_to_frames`（抽帧逻辑不再需要）

## 二、测试结果（全部真实执行）

```
backend/.venv/Scripts/python.exe -m pytest tests/     # env -u PYTHONPATH
================== 81 passed, 1 warning in 66.52s ======================
```

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| 2A 既有 | 13 | 并发/事务/旧库审计 |
| 3A 数据层 + 接口 | 30 | 审核三态/引用保护/导入保护 |
| 2B test_comfy_reliability.py | 18 | client_id/恢复/快照/取消 |
| **2C test_comfy_archive.py（新增）** | **20** | 上传 multipart/URL 解析/subfolder 拼接/缺失与外链拒传、原生视频直通（不抽帧、空帧列表）、图片序列照旧、兜底上传+注入、快照不重复上传、默认模板不上传、remux copy 保码流+元数据、非 mp4 转码、海报帧、队列直通（元数据/海报/清临时/镜头联动）、remux 失败拷贝兜底、帧序列路径不变、接口层入队上传进快照、上传失败 400 |

remux/海报帧用例用 imageio-ffmpeg 静态 ffmpeg 造真实小视频执行，非 mock。
导入冒烟通过（app.main / video / comfyui / engines.comfy 均正常）。

## 三、实施中修复的实现 bug（测试抓出）

1. **`engines/base.py` 返回契约未文档化**：原生视频直通后 `generate`
   返回空列表是「通知队列走 remux」的信号，不写清楚后续维护者会误判为
   bug → 补契约说明（2C）
2. **router 外层 `except Exception` 会吞 HTTPException**：参考图上传
   失败的 400 被「快照写入失败」兜底捕获、降级为执行时再失败 →
   加 `except HTTPException: raise` 放行明确错误
3. **测试基建**：`_RecordingRequests` 缺 `code` 参数（非 200 用例）

## 四、剩余风险 / 后续

- **ComfyUI input 目录清理**：上传的参考图按 `overwrite=true` 重复覆盖，
  不删除历史文件——长期使用 input 目录会堆积。可在设置页加「清理
  ComfyUI 输入文件」入口（下批可选）
- **resume 路径不重传参考图**：恢复执行直接按 prompt_id 等待，参考图
  在首次提交时已在 input 目录——若用户中途手动清理了 input 目录，
  ComfyUI 侧会报文件缺失（概率低，且失败信息明确）
- **webm/gif 转码分支**：`-c:v libx264` 重编码（非原样），仅在不支持
  copy 的容器上发生；VHS_VideoCombine 默认输出 mp4，主流预设不走此分支
- 与真实 ComfyUI 的联调（上传→提交→视频产出→remux 全链路）排在
  Windows Electron 全链路验证时统一做
- 前端无改动（上传入口/参考图设置在既有页面已可用）；Electron 整合
  子任务后统一冒烟

## 五、回滚点

- 本包开始前提交：879b17c（2B）
- 本包提交：见 git log（`phase2c` 系列）
