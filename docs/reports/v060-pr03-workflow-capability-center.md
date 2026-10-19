# PR-03：FreeVideo 式工作流能力中心

- 日期：2026-10-19
- 分支：`hotfix/p0-comfy-robustness`（接续 `dd5ef0f`）
- 依据：用户新版计划图 PR-03——自动检查所需 ComfyUI 节点/模型/输入能力，标注本地免费 vs 付费 API，显存资源提示
- 验收场景：RTX 4060 8GB 选择工作流时，提前提示缺失节点、不支持的输入和资源限制

## 1. 变更清单（8 文件）

| 文件 | 变更 |
|---|---|
| `backend/app/workflow_capabilities.py`（新增） | 能力自述解析：`capability_from_template`（无 `_meta` → None，零阻断）；`load_all_capabilities`（当前模板+默认+4 预设）；`check_missing_nodes`（探测失败不虚构清单，返回 `([], probe_ok=False)`）；`mode_supported`；`vram_warning`（8GB/CPU 场景提示文本） |
| `backend/app/comfyui.py` | `ComfyUIClient.object_info(node_names=None)`：GET `/object_info` + 本地过滤；网络异常/非 200/非 JSON → `ComfyUIError` |
| `backend/app/routers/services.py` | 新增 `GET /api/comfy/capabilities`：各模板能力自述 + 实时缺节点探测（ComfyUI 离线时 `node_probe_done=False` 如实报告）+ 本机 GPU 信息（`hw_probe.probe()`） |
| `backend/app/routers/storyboard.py` | 生成前阻断（备料块内、上传之前）：模板有 `_meta` 时——① `mode` 不在 `supports` → 400 明示支持列表；② `required_nodes` 探测缺失 → 400 带安装指引。**无 `_meta` 的模板零影响**（默认模板/旧配置行为不变，不触发探测） |
| `backend/comfy_workflows/presets/*.json` ×4 | `_meta` 补 `execution_kind`/`model_id`/`required_nodes`（取自各 prompt 实际 class_type）/`supports`/`min_vram_gb`/`vram_hint`——**每文件 +8/-1，只加声明字段不动现有配置**；MiniMax 两项标 `paid_api`（含「不是免费本地模型」提示），wan_ti2v `min_vram_gb=8`（RTX 4060 8GB 可跑 480p），anime_diff `min_vram_gb=4` |
| `frontend/src/api.js` | `comfyCapabilities()` |
| `frontend/src/views/ComfySetupView.vue` | 「工作流能力检查」面板：每模板展示 本地免费/付费 API 徽章、模型 ID、支持输入、依赖节点 chips（缺失标红）、显存/资源提示、本机 GPU 信息；离线时如实提示「无法探测」 |

## 2. 阻断语义（兼容第一）

| 场景 | 行为 |
|---|---|
| 模板无 `_meta`（默认模板/用户旧配置） | **不阻断、不探测**——行为与旧版逐字节一致（有专门回归测试） |
| `_meta.supports` 不含请求 mode（如 t2v 模板收到 i2v） | 400「不支持该模式；声明支持：…」——消灭「静默忽略参考图」旧坑 |
| `_meta.required_nodes` 中有节点在 `/object_info` 缺失 | 400「缺少 ComfyUI 节点：…，请安装对应自定义节点」 |
| ComfyUI 离线/探测失败 | 能力页如实显示「无法探测」；生成侧不虚构缺失清单（探测失败不阻断，交由既有 `is_available` 检查与提交链路） |
| 显存低于 `min_vram_gb` | **提示不阻断**（能力页红字 + `vram_warning` 文本）——与改造.md「不保证任何模型在 8GB 能按固定画质成功生成」一致，硬阻断只留给缺节点/不支持输入 |

## 3. 测试结果（本轮真实执行）

```
pytest tests/test_workflow_capabilities.py -q  → 14 passed（首轮 13 过 1 挂，
                                                  i2v 用例被前置输入检查拦截，补参考图后全过）
pytest tests -q（全量）                         → 158 passed, 1 skipped（108.89s）
npm run build（frontend）                       → ✓ built in 3.27s
```

14 例覆盖：解析（有/无 `_meta`/字段缺失保守默认）、`load_all_capabilities` 六项齐全且 MiniMax=付费 API、`vram_warning`（RTX 4060 8GB 不警告 / CPU 警告 / API 类不警告）、`object_info` 过滤与三类错误、`check_missing_nodes` 三态、capabilities 端点（在线缺节点 / 离线如实报告）、生成阻断四例（缺节点 400 不留 job / 节点齐 201 / 不支持模式 400 / 无 `_meta` 零影响回归护栏）。

**未执行（如实标注）**：真实 ComfyUI `/object_info` 实机探测、RTX 4060 实机生成、GUI。

## 4. 风险与回滚

- 用户自建模板若粘贴了含 `_meta` 的导出且声明与实际不符 → 生成会按声明阻断；「恢复默认」或删除 `_meta` 即恢复旧行为（能力页可查）。
- 预设 JSON 为纯新增字段（+8/-1/文件），JSON 有效性已验证。
- 回滚：`git revert <本提交>`。
- 后续：图中 PR-04（多镜头导演工作台：衔接关系/角色版本锁定/相邻预览/候选对比/局部返工）与 PR-05（剪辑成片：项目帧率/波形多轨）待继续。
