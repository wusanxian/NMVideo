# ComfyUI 运行环境改为自持

日期：2026-11-12
范围：`backend/app/comfy_manager.py`、`packaging/launcher.py`、`backend/tests/test_comfy_manager.py`

## 1. 问题

用户指出：启动 ComfyUI 用的是「别人的解释器」。

核查属实。本机托管启动 ComfyUI 时，实际使用的解释器是：

```
G:\AI\FreeVideo\FreeVideo-engine\envs\comfyui-cf7032f8868e68ca\Scripts\python.exe
```

这不是配置错误，是代码里 `_find_python()` 的候选列表决定的。它按下面顺序找：

1. `<ComfyUI>/venv/Scripts/python.exe`
2. `<ComfyUI 上级或旁支>/envs/comfyui-*/Scripts/python.exe`
3. `<ComfyUI>/python_embeded/python.exe`
4. PATH 上的 `python`

**根本没有把本产品自己的环境列进去。** 于是在任何装了第三方整合包（FreeVideo 等）
的机器上，必然借用人家的 Python——版本、依赖、升级节奏全不由我们控制，用户也没法
单独重装。

## 2. 结论与做法

新增本产品自持的 ComfyUI 运行环境 `<runtime>/comfy-venv`，并让它排在所有外部候选之前。

### 2.1 为什么不直接复用 `runtime/venv`

`runtime/venv` 是后端自己的环境（fastapi / diffusers / edge-tts）。ComfyUI 的依赖面
是另一套（einops / scipy / kornia / spandrel / av / comfyui-frontend-package /
comfy-kitchen…），而且对 torch 版本要求完全不同。混装会互相拽版本，升级任一侧就会
弄坏另一侧。

### 2.2 解释器来源标记

`discover()` 现在返回：

| 字段 | 含义 |
|---|---|
| `python` | 解释器绝对路径 |
| `python_source` | `own` / `comfy-venv` / `envs` / `portable` / `path` / `none` |
| `own_runtime` | 是否为自持环境 |
| `valid` | 目录里有没有 `main.py` |

`state()` 在「有 ComfyUI 但解释器来自外部」时，note 会如实写：

> 本机 ComfyUI 未运行；将借用外部安装的 Python 启动（来源 envs）

界面不再让用户误以为运行环境是自己管的。

### 2.3 就绪判定不靠 import

自家 venv 和 ComfyUI 自带 venv 要求 `Lib/site-packages/torch/__init__.py` 存在才算
可用。只做文件判断，不 `import torch`（一次 5 秒以上，启动链路扛不住）。
装到一半、被清空的 venv 会被判为未就绪并回落，不会让启动必然失败。

## 3. 首次准备（launcher）

`ensure_comfy_env(rt, torch_ver)` 在 `plan.model == "h3"` 时执行（进度归入 `deps`
阶段，不改桌面端 `STAGE_ORDER`）。步骤：

1. 定位 ComfyUI 目录：`settings.comfy_dir` > `AIVIDEO_COMFY_DIR` > C–G 盘
   `X:/AI/FreeVideo/ComfyUI`
2. 建 venv（基座优先 Python 3.10–3.12；3.13 的 wheel 覆盖不全，只作兜底）
3. 装 torch
4. `pip install -r <ComfyUI>/requirements.txt`
5. 若存在 `custom_nodes/FreeVideo`，补装 `diffusers` / `omegaconf`
   （插件要它们，ComfyUI 的 requirements 不含）
6. 写指纹 `.comfy-deps-ok`，之后启动跳过安装

## 4. torch 版本：不能跟后端共用 cu124

**这是本轮最贵的教训。** 用 torch 2.6.0+cu124 起 ComfyUI 0.34，启动即崩：

```
comfy_kitchen/backends/eager/na.py
  → torch.library.infer_schema
  → ValueError: Parameter kernel_size has unsupported type list[int]
```

`comfy_kitchen` 用 PEP585 注解 `list[int]`，torch 2.6 的 `infer_schema` 只认
`typing.List[int]`。不是缺包，是版本不够。

可用组合（与 FreeVideo 实机一致，驱动 617.42）：

| 包 | 版本 |
|---|---|
| torch | `2.13.0+cu130` |
| torchvision | `0.28.0+cu130` |
| torchaudio | `2.11.0+cu130` |

index：`https://download.pytorch.org/whl/cu130/`（cp310–cp314 都有 Windows 轮子）。

后端那套 `cu124 / torch 2.6` 保留给 `h3_native`（diffusers 进程内推理），两套并存。

## 5. Windows 安装踩坑

装 2.5GB 级 wheel 时两次失败：

- `[WinError 5] 拒绝访问`：`torch-2.13.0+cu130.dist-info\RECORD*.tmp` 重命名被拒
- `SHFileOperationW 失败: 0x78`

都是杀软实时扫描锁文件所致。pip 报 `EXIT=1`，但 `import torch` 实际可用；
重跑一轮即可补齐剩余包。**不要因为退出码判死整个环境。**

## 6. 验证

本机（RTX 4060 8187MB / 32GB RAM / 驱动 617.42）实测，2026-11-12：

| 项 | 结果 |
|---|---|
| 自有 venv 路径 | `D:\work\aivideo\NMVideo\resources\launcher\runtime\comfy-venv` |
| Python | 3.10.6 |
| torch / tv / ta | 2.13.0+cu130 / 0.28.0+cu130 / 2.11.0+cu130，`cuda.is_available()=True` |
| `comfy_kitchen` CUDA 后端 | 可用（capabilities 含 `na3d` / `int8_linear` / `nvfp4` 等） |
| 直接启动 `main.py` | 成功，ComfyUI 0.34.0，929 个节点 |
| FreeVideo 节点 | `FreeVideoGenerate` / `Media` / `Reference` / `LoRA` / `LoRAStack` / `Frames` 全部注册 |
| `discover()` | `python_source=own`、`own_runtime=true` |
| `start()` 托管启动 | `{'started': True, 'pid': 28564}`，等就绪约 2 分钟，`online=True` |
| `stop()` | `{'stopped': True}`，进程树清理干净 |
| 后端单测 | 199 通过 / 0 失败（含新增 3 例） |

冷启动约 3 分钟（FreeVideo 插件在后台编译 GPU kernel），比 FreeVideo 自己启动慢，
主要差在首次 import 65MB 引擎 + 插件预编译，属正常。

## 6.1 两个实测细节

1. **同目录不能同时跑两个实例**：8188 已被 FreeVideo 占着时，用同一 `user/`
   目录再起 8199 会卡在 `import nodes` 之后不动（ComfyUI 的 `user/comfyui.db`
   被占用）。验证时加 `--user-directory <独立目录>` 或先停掉另一个。
2. **日志要无缓冲**：不加 `PYTHONUNBUFFERED=1` 时，进程已启动但日志停在
   `No OpenGL_accelerate` 之后十几分钟，看起来像卡死。托管启动写文件时同理。

## 7. 后续优化（未做）

1. **首次准备太慢**：实测 pip 装 torch 约 40 分钟（约 1MB/s），requirements 再 1 小时。
   FreeVideo 用 `uv` 装，明显更快；后续把 comfy 环境改用 uv。
2. **`comfyui-workflow-templates` 可以省**：它拉的 `media-api` / `media-video` /
   `media-other` 各 90–100MB，只是示例工作流的媒体，与运行无关。装完可卸载，省约 1GB。
3. **ComfyUI 源码本身仍在外部目录**：本轮只做到「解释器自持」。若要连 ComfyUI 程序
   也自带，需要把源码（约 50MB）与 `custom_nodes` 收进 `runtime/`，权重用
   `extra_model_paths.yaml` 外挂。未与用户确认，先不动。
