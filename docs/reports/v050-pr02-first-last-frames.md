# PR-02：首尾帧与多参考素材（图中 PR-02 / 改造.md PR-08）

- 日期：2026-10-19
- 分支：`hotfix/p0-comfy-robustness`（接续 `4833a1e`）
- 依据：用户新版计划图（PR-01~05 重排）——图中 PR-02「首尾帧与多参考素材」
- 前置确认：图中 PR-01（生成任务生命周期）三条验收已在开发线全绿（见 `v040` 报告），不重复修改

## 1. 变更清单（10 文件）

### 后端（数据模型与冻结）

| 文件 | 变更 |
|---|---|
| `db.py` | 第四批轻量迁移：shots + jobs 各加 `first_frame_image`/`last_frame_image`/`source_video_version_id`（TEXT DEFAULT ''，旧库自动补列，零破坏） |
| `storyboard.py` | `update_shot` 白名单加三字段；`get_shot` 附带 `_attach_inheritance_staleness`（续接镜头对比来源镜头当前采用版本 → `inheritance_stale` 标记；新增 `get_shot_basic` 轻量读取防递归） |
| `routers/storyboard.py` | ① ShotCreate/ShotUpdate 加 `first_frame_image`/`last_frame_image`；② generate_shot：first_last 必须同时有首+尾帧（400 明示）；**模板未映射尾帧节点 / 首帧无任何可走节点 → 400 明确失败（不静默忽略）**；③ 图片路由：i2v 沿用 reference_image（行为不变）；first_last 首帧优先走 `first_frame_image` 专用节点、旧式单节点模板兼容走 reference_image；continuation 首帧走 reference_image 且新模板可走 first_frame_image；④ 三类图片按需上传 ComfyUI input（失败 400）；⑤ **入队冻结**：job 记录本机 URL（first/last/source_video_version_id），可复现 |
| `queue_manager.py` | worker 透传 `first_frame_image`/`last_frame_image` 给引擎 |
| `engines/base.py` | generate 契约加两参数（其他引擎忽略） |
| `engines/comfy.py` | generate 签名 + inject 三键；兜底路径（无快照）按模板映射现场上传首/尾帧 |
| `engines/anime.py` `engines/wan.py` | 签名同步忽略新参数 |
| `shotcheck.py` | first_last 校验拆分：首帧（兼容旧 reference_image）+ 尾帧独立校验（`no_last_frame`） |

### 前端

| 文件 | 变更 |
|---|---|
| `ShotEditorDialog.vue` | first_last 模式提供**首帧 + 尾帧两个独立输入**（各带上传按钮，`onFrameUpload(e, field)` 通用化）；旧 first_last 单参考图自动预填为首帧（用户可见可改）；i2v 单图输入不变 |

## 2. 与文档方案的差异（如实记录）

1. **URL 字段而非 asset_id**：改造.md PR-08 写 `first_frame_asset_id` 等——当前系统没有资产注册表，现有 reference_image 就是 URL 字符串。本批用 `first_frame_image`/`last_frame_image` URL 字段保持一致性；引入资产 ID 注册表需新表+迁移，留待 PR-03 能力中心一并设计。
2. **角色多参考图注入延后**：角色卡 `reference_images` 数组存储已存在（assets.py），但多图注入需要知道工作流里有哪些 LoadImage 节点可承接——这正是图中 PR-03 能力中心（`/object_info` 探测 + required_nodes）的职责。本批先落地首尾帧双图注入（验收核心），多图注入随 PR-03 实施。
3. **多参考冻结**：改造.md 要求「入队时冻结所有输入资产、哈希和来源版本」——本批冻结了 URL 与来源版本；文件哈希随资产注册表（PR-03）补。

## 3. 测试结果（本轮真实执行）

```
pytest tests/test_first_last_frames.py -q     → 9 passed（6.72s）
pytest tests -q（全量）                        → 144 passed, 1 skipped（102.92s）
npm run build（frontend）                     → ✓ built in 4.31s
```

新增 9 例覆盖：双图分别到达两个不同 LoadImage 节点且互不相同 / 缺尾帧 400 / 模板未映射尾帧节点 400 不留任务 / 未映射首帧节点 400 / 旧式混合模板兼容（reference_image 充当首帧）/ i2v 行为不变回归护栏 / 续接记录 source_video_version_id 且未过期 / 上游换版本后 `inheritance_stale=true` / 引擎兜底路径按映射上传两图。

唯一 skip 仍为沙箱 mklink 限制的符号链接用例（诚实跳过）。

**未执行（如实标注）**：真实 ComfyUI 实机双节点注入、Windows GUI、Electron smoke——沙箱无实例/GUI。

## 4. 行为变化说明（对旧数据/旧流程零破坏）

- 旧 first_last 用法（只有一张 reference_image）：**生成会被 400 拦截并提示设置尾帧**（旧行为是静默只注入一张图）——这是验收要求的「不默默忽略」，UI 已预填首帧引导用户补尾帧；
- t2v / i2v / continuation 链路行为完全不变；
- 旧库自动加列（DEFAULT ''），无需人工迁移。

## 5. 风险与回滚

- `git revert <本提交>`；DB 迁移为纯加列，回滚后旧代码读取新列无感知（SELECT * 多出的列不影响）。
- 下游依赖：图中 PR-03（能力中心）将以本批的 `_aivideo_map` 三图片键 + preset `_meta` 为地基。
