# H3 8GB 量化支持（2026-10-23）

## 1. 结论先行

**8GB 显存本身不是障碍，内存才是。**

实测权重体积（HF 文件清单，`?recursive=true` 逐文件求和）：

| 组件 | 分片数 | 体积 |
|---|---|---|
| `h3-base/transformer` | 14 | **66.28 GB = 61.7 GiB**（bf16） |
| `text_encoder`（Qwen3-VL-32B） | 14 | **66.71 GB = 62.1 GiB**（bf16） |
| 合计 | 28 | **123.8 GiB**，磁盘 139 GB |

`apply_group_offloading` 的实现是「权重放 CPU 内存，按块搬进显存」——
**省显存，不省内存**。所以低显存档位的真实门槛是内存：

| 可用内存 | 最高精度 | 权重驻留 | 结论 |
|---|---|---|---|
| ≥ 140GB | 全精度 | 123.8 GiB | 可用 |
| ≥ 79GB | int8 | 62.5 GiB | 可用 |
| ≥ 52GB | **int4** | 37.7 GiB | 可用（实验档） |
| < 52GB | — | — | **不可用**，无论显存多大 |

因此：

- **8GB 显存 + 64GB 内存 → 能跑**（int4 + 按块卸载 + 画布压到 512×384 级）
- **8GB 显存 + 32GB 内存 → 仍然跑不了**，差 20GB 内存，任何显存技巧都补不回来

## 2. 本批改了什么

### 2.1 新增 `backend/app/engines/h3_quant.py`

纯策略模块，不 import torch / torchao，单测在无 GPU 机器可跑：

- `plan(vram_gib, ram_gib, compute_cap)` → `H3QuantPlan`
  - **内存决定精度上限**（从 full → int8 → int4 挑第一个装得下的）
  - **显存决定两件事**：权重能否常驻（`vram ≥ 权重+6` 则常驻，否则按块流式）、画布上限
  - 算力校验：int8 需 sm_70+、int4 需 sm_80+（torchao weight-only kernel）
  - 失败时 `reason` 给出可执行建议（加内存到 64GB / 换 Wan 2.2 TI2V-5B）
- `build_quantization_config()` → 产出 `{"transformer": diffusers.TorchAoConfig, "text_encoder": transformers.TorchAoConfig}`
  - 转换器与文本编码器分属两个库，配置类**不能混用**，这里按组件分派
  - 延迟 import torchao：没有量化后端的环境不会因导入本模块失败

### 2.2 修掉「24GB 卡必然 OOM」的旧分支

旧 `_strategy`：`vram >= 24 → offload=none` 然后 `pipe.transformer.to("cuda")`。
transformer 是 61.7 GiB，24GB 卡确定装不下（int8 也还有 31 GiB）。
现改为：权重常驻的前提是 `vram ≥ 权重 + 6` —— 实际只有 80GB 卡跑 int8 时才会常驻。

### 2.3 量化真正接上了

旧代码 `load_kwargs["fp8"] = {"transformer": True}` 传给 `from_pretrained`——
`fp8` 不是 `from_pretrained` 的参数，会被当成 config 覆盖项**静默吞掉**，
等于从未生效。现改为官方路径：

```python
load_kwargs["quantization_config"] = {
    "transformer":   TorchAoConfig(Int8WeightOnlyConfig()),   # diffusers
    "text_encoder":  TorchAoConfig(Int8WeightOnlyConfig()),   # transformers
}
pipe.load_components(**load_kwargs)
```

### 2.4 画布随档位收缩

`normalize_size(width, height, max_pixels=None)` 新增上限参数：

| 显存 | 画布上限 | 典型尺寸 |
|---|---|---|
| < 10GB | 196608px | 512×384 |
| < 24GB | 524288px | 896×584 |
| ≥ 24GB | 1032192px | 1344×768（旧上限，不变） |

帧数压不下去：模型最短 5.17 秒 = 124 帧（`17n+5`），这是激活体积的硬下限。

### 2.5 低显存档开 VAE 分块解码

`plan.max_pixels < 1032192` 时对 `vae` / `audio_vae` 调用 `enable_tiling()`，
失败只打日志不阻断（不同 diffusers 版本/V AE 类型不一定有该方法）。

### 2.6 启动器选型口径对齐

`packaging/launcher.py::choose_plan`：H3 自动选型条件由
`vram>=16 且 ram>=32` 改为 **`vram>=10 且 ram>=80`**（原条件在 16GB/32GB 机器上
会选中 H3，然后在加载阶段必挂）。8GB+52GB 的机器会看到提示：
默认给 TI2V-5B，想试 H3 可 `--model h3`（int4 实验档）。

## 3. 测试

新增 `backend/tests/test_h3_quant.py`（30 例）：

- 档位矩阵：8/12/24/40/80GB × 内存组合 → tier / offload / 画布
- 8GB+32GB 必须被拒，且 reason 说明是**内存**、指向 Wan
- int4 需 sm_80：sm_75 被拒
- `build_quantization_config` 真产出 diffusers / transformers 两份 `TorchAoConfig`
- 引擎接线：`_strategy` 档位、`describe` 门禁文案、`_get_pipe` 在 import 模型前就报错、
  `normalize_size` 上限生效且默认行为不变

同时调整 `tests/test_h3_engine.py`：
- `_profile` 默认内存 32GB → 128GB（旧断言默认「24GB 显存即就绪」，在新口径下内存不足）
- `test_describe_rejects_low_vram` 改为 4GB 显存（显存线降到 6GB）
- 新增 `test_describe_rejects_low_ram`（8GB 显存 + 32GB 内存）

## 4. 未验证项（必须如实标注）

| 项 | 状态 |
|---|---|
| torchao int8/int4 在真实 H3 权重上的加载 | **未执行**（本机无 N 卡、权重未下载） |
| int4 文本编码器的画质影响 | **未标定**（这是 int4 标为实验档的原因） |
| 8GB 卡 + 64GB 内存的实际显存峰值 | **未实测**（推算激活约 5~8GB，卡在边缘） |
| VAE `enable_tiling()` 在 H3 的 VAE 上是否可用 | **未实测**（有 try/except 兜底） |
| 按块卸载 + 量化的出片耗时 | **未实测** |

**本批不能宣称「8GB 已能出片」**，只能说「8GB + 64GB 内存的加载路径已经打通，
等实机验收」。

## 5. 实机验收步骤（有 N 卡时照做）

```bash
# 1) 确认后端识别到正确档位（不加载模型，秒级）
cd backend && python -c "from app.engines.h3 import H3Engine; import json; print(json.dumps(H3Engine().describe(), ensure_ascii=False, indent=2))"

# 2) 最小配置试跑：124 帧（模型最短 5.17 秒）、512×384、单条
#    盯 nvidia-smi 的显存峰值与内存峰值

# 3) 若 int4 画质不可接受：内存加到 128GB 走 int8 对照
```

验收记录需包含：tier / 显存峰值 / 内存峰值 / 单镜头耗时 / 画质主观评价。

## 6. 已知缺口（下一批）

1. **入队快照与实际画布可能不一致**：`quality.py` 仍按 1032192 上限归一，
   8GB 档实际出片被引擎缩到 512×384，DB 里记录的 width/height 偏大。
   修法是在 `quality.py` 的 h3 分支接入 `h3_quant.plan`（需先解决每次入队
   都跑 `hw_probe.probe()` 的开销，建议加缓存）。
2. **8GB + 32GB 内存仍不可用**：要真正压进去需要「分阶段加载」——
   先用 int4 文本编码器编码 prompt 并 `unload_components(["text_encoder"])`，
   再加载 transformer 去噪，峰值内存 ≈ 19GiB。diffusers 提供
   `load_components(names=...)` / `unload_components(names=...)`，路径可行，
   但需要实机调通 block 调用顺序，不能盲写。
