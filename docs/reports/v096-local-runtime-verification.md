# v096 · 本地安装实测（白模链路首跑）+ 四个真机缺陷

日期：2026-11-11
基线：`3a87b34`（v095 白模模式）
实测环境：本机 `D:\work\aivideo\NMVideo`（0.1.10 安装目录）+ ComfyUI 0.34.0（`G:\AI\FreeVideo`）
硬件：RTX 4060 8GB / 32GB RAM / 可用磁盘 113GB

---

## 1. 这轮做的事

把本地安装目录的运行时更新到 `3a87b34`，然后**用真实素材跑了一遍白模生成**。
结论先说：**白模链路真跑通了**（ComfyUI 侧 SAM3DBody 逐帧重建 + silhouette 渲染），
但跑通过程中撞出四个缺陷，其中三个会让功能「看起来有、实际不可用」。

---

## 2. 更新安装目录时的两条硬事实

### 2.1 只改 `runtime/` 无效

`packaging/launcher.py::sync_payload()` 在**每次启动时**都会用 exe 内置的 payload
覆盖 `runtime/backend` 与 `runtime/frontend`（跳过 `models/`、`outputs/`、
`.venv`、`aivideo.db`、`settings.json`）。

所以「拷源码到 runtime」这种更新方式在下次启动就会被冲掉。正确做法只有一条：

```
backend/.venv/Scripts/python.exe packaging/build.py --no-fe
cp packaging/dist/NMVideo.exe <安装目录>/resources/launcher/NMVideo.exe
```

本轮据此重建三次（每次修复后），最终 6.3 MB。

### 2.2 payload 要 `--fresh`

默认增量覆盖，源码里删掉/改名的文件会残留在 payload 里。
本轮第一次重建后 payload 是 **2.7 MB**，`--fresh` 后降到 **1.3 MB** ——
差的那 1.4 MB 全是历史前端 chunk（`runtime/frontend/dist/assets` 里堆了 76 个文件，
而一次干净构建只有 22 个）。体积浪费是次要的，真正的问题是
`ProjectView-*.js` 同时存在 5 个不同 hash 的副本，排查「新代码到底有没有进包」
时会误判。

---

## 3. 实测结果（真数据）

素材：`1cb3efb5.mp4`（512×512、8fps、3.13s、25 帧，本机原有素材）

| 项目 | 值 |
|---|---|
| 提交 | `POST /api/shots/{id}/generate` + `mode=whitemodel` |
| 归档参数 | 512×512 / 25 帧 / 8fps（由源视频实测，非界面默认值） |
| ComfyUI 执行 | `SAM3D body inference 25/25` → `SAM3D silhouette 25/25` |
| 耗时 | 首次 16.0s（含加载 1794MB 权重），热态 5.5s |
| 产物 | 512×512 / 8fps / 3.13s / **21 KB** / bt709 |
| 画面 | 白底人形剪影，无面容、无服装、无背景 |

权重与节点（`GET /api/white-model/capability`，`probe_ok=true`）：
`sam3d_body_mesh` 与 `sdpose_skeleton` 两条路径 `available=true`，
`model_files=['sam_3d_body_dinov3_bf16.safetensors']`，`missing_video_io_nodes=[]`。

`render_style` 的真实形状（本机 `/object_info`）：

```
["COMFY_DYNAMICCOMBO_V3", {"options": [
   {"key": "mesh", "inputs": {"required": {"shader": ["COMFY_DYNAMICCOMBO_V3", ...]}}},
   ...]}
]
```

`mesh` 内层又是一个动态 COMBO，所以预设默认仍用 `silhouette`（无嵌套）。这一条
与 v095 的假设一致，**但从「假设」升级成了「实测」**。

---

## 4. 四个真机缺陷

### 4.1 `mode` 校验正则漏改 → 白模在 pydantic 层就被 422

`ShotGenerate.mode` 的 `pattern` 还是手写的 `^(t2v|i2v|first_last|continuation|v2v)$`。
v095 把 `whitemodel` 加进了引擎能力、生成链路、预设，唯独漏了这处请求体校验。

后果：界面选「白模生成」提交 → `422 string_pattern_mismatch`，
用户看到的像是「后端不认识这个模式」。**193 个单测全绿也抓不到**，因为测试
直接调 `generate_shot` 或绕过请求体。

修：改为从 `workflow_capabilities.SUPPORTED_MODES` 派生正则，不再手写第二份清单。

### 4.2 `ShotUpdate` 缺 `mode` / `continue_from` → 保存即丢

前端模式卡就写在表单里（`form.mode`），保存走 `PATCH /api/shots/{id}`；
`ShotUpdate` 模型里没有这两个字段，pydantic 默认静默丢弃。

后果：接口返回 200、界面提示保存成功，**重开弹窗模式回到 t2v**——
用户会以为「平台记住了」，其实每次都重置。`continue_from` 同样。

修：补两个字段（`mode` 复用同一个派生正则），并加「只传标题时不得顺手改 mode」的用例。

### 4.3 产物提取不滤 `input` 回显 → 白模跑了，存下来的是原片（最严重）

ComfyUI 的 `history.outputs` 里，`LoadVideo` 这类**输入节点也会回显自己读的文件**：

```json
{"1": {"images": [{"filename": "e2cd...mp4", "type": "input"}]},   // 我们上传的原片
 "7": {"images": [{"filename": "shot_00001_.mp4", "type": "output"}]}}  // 真产物
```

`extract_output_files()` 遍历所有节点、不看 `type`，节点 id `"1"` 排在 `"7"` 之前，
所以 `videos[0]` 拿到的是**原始素材**。

后果最隐蔽：任务状态 `done`、有 `video_url`、时长分辨率帧率全都对、画面「看起来正常」
（因为就是原片）——**只有把 NMVideo 存的文件和 ComfyUI 自己 `output/` 目录里的文件
比对尺寸，才能发现**（266 KB vs 21 KB）。

发现方式：抽产物中间帧做视觉检查，发现画面是一只猫（原素材就是猫），
而 ComfyUI 日志明确显示 `SAM3D silhouette 25/25` 跑完了 —— 两者矛盾，才顺下去查到根因。

修：`extract_output_files()` 跳过 `type == "input"`，并把 `temp` 排到 `output` 之后。

### 4.4 项目页 ComfyUI 徽标永远「未连接」

`/api/comfy/status` 返回 `{"available": true, ...}`，前端赋值后模板读 `comfy.online`
→ `undefined` → 恒为假。ComfyUI 明明在跑，项目页却顶着一个灰色的「未连接」，
用户第一反应会是「是不是要我再去设置一遍」。

修：赋值时归一化 `online: s.available ?? s.online`。

---

## 5. 验证局限（必须写清）

1. **素材里没有人**。唯一可用的本机素材是猫的视频，SAM3DBody 仍强行拟合出了
   人形剪影（见产物画面）。这说明**模型只会输出人体**，也说明这份 "跑通" 的
   动作还原度不能代表真人素材效果 —— 真人视频需要另测。
2. **只测了 `silhouette`**。`mesh`（真白模网格）带嵌套 `shader` 子参数，提交形态
   仍未验证，预设 `_meta.unverified_params` 保持原样。
3. **v2v（白模替换）仍未端到端**：本机 `/object_info` 依旧缺 `VHS_LoadVideo`
   （929 个节点里没有），所以 v2v 的参考视频注入无法执行。
4. 本轮未测长素材（>10s）的耗时曲线，只有 25 帧的数据点。

---

## 6. 本机环境的一个坑（不是产品问题）

宿主进程注入了 `http_proxy=http://127.0.0.1:50867`，`requests` 会把
`127.0.0.1:8188` 也送进代理，返回 502，表现为「ComfyUI 连不上」。
测试时必须 `export no_proxy=127.0.0.1,localhost` 再启动服务。
真实用户环境没有这个变量，不影响产品。

另：宿主设置 `ELECTRON_RUN_AS_NODE=1`，会让 `NMVideo.exe` 以 node 身份执行
（`--help` 打印 node 用法）。要起 GUI 必须先 `unset ELECTRON_RUN_AS_NODE`。

---

## 7. 改动清单

| 文件 | 改动 |
|---|---|
| `backend/app/routers/storyboard.py` | `_MODE_PATTERN` 从 `SUPPORTED_MODES` 派生；`ShotUpdate` 补 `mode` / `continue_from` |
| `backend/app/comfyui.py` | `extract_output_files` 滤掉 `input` 回显，`output` 优先于 `temp` |
| `frontend/src/views/ProjectView.vue` | 连接状态归一化 `available → online` |
| `frontend/src/components/ShotEditorDialog.vue` | 白模说明去掉「尚未实跑验证」，改为实测数据 + 「素材里得有人」 |
| `backend/tests/test_whitemodel_mode.py` | +5 例：请求体全模式放行、`ShotUpdate` 持久化、产物提取 3 例 |

测试：**196 通过 / 0 失败**（`tests/run_all.py`，用 `backend/.venv`）。
前端 `check:static` 与 `build` 通过。

---

## 8. 下次接着做

1. 用**真人**素材重跑，检查动作还原度与耗时；补齐 >10s 的耗时曲线。
2. 试 `mesh`：`render_style` 传 `{"mesh": {"shader": "default"}}` 这类嵌套形态，
   确认提交格式后切默认档。
3. v2v 端到端仍缺 `VHS_LoadVideo`——要么在本机装 VideoHelperSuite，
   要么把 v2v 预设换成 `LoadVideo`（本机有）。
4. 白模产物作为 v2v 运动参考的接力还没试过（白模 → 角色替换）。
