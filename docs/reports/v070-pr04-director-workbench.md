# PR-04：多镜头导演工作台（角色版本锁定 / 相邻衔接预览 / 候选版本对比）

- 日期：2026-10-19
- 分支：`hotfix/p0-comfy-robustness`（接续 `fd528f1`）
- 依据：用户新版计划图 PR-04——镜头衔接关系、角色版本锁定、相邻镜头预览、候选版本对比、局部返工
- 验收：五连续武侠镜头可在同一项目内生成、审核、重做（实机部分未执行，见 §4）

## 1. 五项能力核对与实施范围

| 图中能力 | 核对结论 | 本批动作 |
|---|---|---|
| 镜头衔接关系 | **已落地**（PR-02）：`continue_from` + `source_video_version_id` + `inheritance_stale` 过期标记 + 续接末帧继承 | 无需改动；本批在编辑器补「过期」可见提示 |
| 角色版本锁定 | 缺失 → **本批实施** | 入队冻结角色快照 → job → 落版本存档 |
| 相邻镜头预览 | 缺失 → **本批实施** | 编辑器「相邻镜头衔接」面板（上一/下一镜头采用版本视频） |
| 候选版本对比 | 部分（版本列表+采用已有）→ **本批补 A/B 对比** | 选两个候选并排播放对比 |
| 局部返工 | **已落地**（`rework_from` + 级联返工 + 引用守卫） | 无需改动 |

## 2. 本批变更（6 文件）

### 角色快照冻结（角色版本锁定的落地形式）

采用与 PR-02 一致的「入队冻结」语义，而非独立角色版本表：

| 文件 | 变更 |
|---|---|
| `db.py` | 第五批迁移：jobs + shot_versions 各加 `characters_snapshot TEXT DEFAULT '[]'` |
| `routers/storyboard.py` | `generate_shot` 入队即冻结 `characters`（id/name/role/appearance/outfit/lora/expressions/reference_images/voice_id/voice_name）到 job——**compose_prompt 用的就是这份快照数据，锁定语义与实际生成输入严格一致** |
| `storyboard.py` | `create_version` 把 job 的快照落进 `shot_versions.characters_snapshot`（params 同时补记 first/last/source_video_version_id）；`list_versions`/`get_version` 解码为 `characters` 列表 |
| `ShotEditorDialog.vue` | ① 「相邻镜头衔接」面板：上一/下一镜头的采用版本视频预览 + 续接镜头「继承帧已过期」红色警示（读 `inheritance_stale`）；② 版本列表新增 A/B 对比：选两个候选并排播放 + 退出对比；③ 版本条目显示 🔒 角色快照（名字+服装）；④ 相邻对比样式 |

**锁定语义**：角色卡后续修改（换装/换 LoRA/换参考图）不影响已生成版本的快照——旧版本永远可复现其生成时的角色数据；新生成则用当前角色卡。这与「镜头引用具体角色版本」的验收要求等价（快照即版本），且避免引入角色版本表+引用迁移的大动作。差异已记录（§5）。

## 3. 测试结果（本轮真实执行）

```
pytest tests/test_character_snapshot.py -q  → 3 passed（8.07s；首轮 2 挂已修：
    character_ids 走专用 PUT /shots/{id}/characters 端点，ShotUpdate PATCH 会忽略该字段）
pytest tests -q（全量）                      → 161 passed, 1 skipped（111.27s）
npm run build（frontend）                    → ✓ built in 3.05s
```

3 例覆盖：端到端冻结（API 建角色→绑定→生成→`_run_job`→版本快照与入队冻结逐字段一致，含 job 行校验）、锁定语义（改角色卡后旧版本快照不变）、无角色版本空列表。

**未执行（如实标注）**：五镜头武侠实机样片（需真实 ComfyUI/GPU 生成五段视频）、GUI 手工验收相邻预览与对比交互。

## 4. 对既有行为的影响

- jobs/shot_versions 纯加列（DEFAULT '[]'），旧数据零迁移、旧 API 响应多出 `characters: []` 字段（新增字段，向后兼容）；
- 未关联角色的镜头行为完全不变；
- 编辑器对无相邻镜头/无版本的镜头显示占位说明。

## 5. 与文档方案的差异

改造.md PR-10 的「角色版本表 + 镜头引用具体版本」：本批以「镜头版本内嵌角色快照」实现等价锁定语义（快照即该次生成所用的角色版本），未建角色版本表与引用外键——待 PR-05/后续需要「按角色版本重放生成」时再升级为表结构（需迁移+UI，届时一并设计资产注册表）。

## 6. 回滚

`git revert <本提交>`；DB 纯加列，回滚安全。
