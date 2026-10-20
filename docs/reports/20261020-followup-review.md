# 当前项目后续改进检查

日期：2026-10-20。基线：`d4187ba` 加当前未提交的增量改造代码。

修复进度：本报告的 6 项问题已在后续修改中处理；下面保留审查时的复现证据。实现与回归验证见 [后续修复记录](20261020-followup-fixes.md)。

当前最优先的工作是任务生命周期、执行服务绑定与成片音频可靠性收尾。本轮在隔离数据库和临时媒体下复现了 6 项问题，均不依赖安装视频生成模型。

## Brooks-Lint Review

**Mode:** PR Review  
**Scope:** 当前工作区改动及其关联路径，重点抽查队列、ComfyUI 客户端、升级、素材删除、音频混合与导出。改动超过 500 行，属于按风险抽查；排除依赖、模型、生成产物与构建文件。  
**Health Score:** 50/100（2 Critical、4 Warning；限定本次审查范围）  
**Trend:** First run — no trend data

已有正常链路测试和真实 FFmpeg 导出测试，但状态转换、服务切换和原声混音仍有遗漏。

## Findings

### Critical

**C1 / P1 — Change Propagation：切换 ComfyUI 地址后，备料与执行可能落到不同服务器**

Symptom: `generation.py:17` 每次准备任务都按当前设置创建客户端；`engines/comfy.py:58` 则在引擎初始化时固定客户端。`queue_manager.py:284` 只按 `engine_id` 缓存 ComfyEngine。任务未冻结服务地址，恢复探测和素材恢复上传也读取当前设置。

Source: Ousterhout — *A Philosophy of Software Design*, Information Hiding and Leakage；Hunt & Thomas — *The Pragmatic Programmer*, Orthogonality。

Consequence: 从 A 切换到 B 后，新任务在 B 预检、上传，却仍可能向 A 提交。素材文件名和 prompt_id 均属于具体服务实例，恢复、图生视频及原生二采也可能因此查询错误实例或失败。

Remedy: 将执行服务地址与任务、工作流快照一起冻结；上传、提交、探测、恢复、下载和升级都使用该地址。引擎缓存至少按引擎与执行地址区分。原生预览跨实例迁移需要单独处理缓存，不能仅修改全局 URL。

复现：缓存 A 的 ComfyEngine 后，将默认地址改为 B；真实准备/队列调用链记录到 **preflight=B、submit=A**。网络客户端的 I/O 被替换，未访问真实服务。

验收：排队前后修改全局地址，旧任务始终使用原实例，新任务完整使用新实例；重启恢复和升级保持同样规则。

**C2 / P1 — Domain Model Distortion：镜头原声在成片导出时被丢弃**

Symptom: `video.py:217` 拼接时对每段使用 `-an`；`export.py:40` 只混合独立对白、音乐和音效，不读取镜头视频内的声音。没有附加音轨时，`_final_mux()` 也明确使用 `-an`。

Source: Evans — *Domain-Driven Design*, Ubiquitous Language / Domain Model。

Consequence: ComfyUI/FreeVideo 原生视频即使带有对白、环境音或音乐，导出的成片也会失声；添加背景音乐时，原声同样不会进入混音。

Remedy: 将镜头原声建模为可控制的音源，随镜头裁剪、排序、音量和静音设置进入导出清单及混音。对白、音乐、原声的叠加或压低音量应有明确规则。

复现：真实 FFmpeg 合成带声音的 1 秒 MP4，加入时间线并导出；**源视频存在 Audio 流，成片不存在 Audio 流，但导出状态为 done**。

验收：只有原声、原声+TTS、原声+音乐、原声静音四种场景均有实际输出验证，并检查视频裁剪后的音画对齐。

### Warning

**W1 / P1 — Knowledge Duplication：取消排队任务后，镜头仍停在 queued**

Symptom: `queue_manager.py:233` 的 cancel 将 pending job 更新为 cancelled，却只对原状态 needs_attention 的任务同步镜头。worker 在 `:256` 遇到取消标记直接跳过，也不会清理镜头状态和 job_id。

Source: Hunt & Thomas — *The Pragmatic Programmer*, DRY；Evans — *Domain-Driven Design*, Domain Model。

Consequence: 界面显示任务已取消，但镜头仍被认为正在排队，再次生成被 409 拒绝。用户取消长队列后无法直接重做该镜头。

Remedy: 收口取消状态转换，在事务中同步任务与镜头，且仅在镜头仍绑定该任务时清理指针，避免旧任务取消覆盖新任务。worker 跳过取消任务也需保证终态一致；活动任务删除应有相同的生命周期约束。

复现：通过 HTTP 创建镜头生成任务 → 取消 → 再次生成，得到 **job=cancelled、shot=queued、重新生成=409**。

验收：排队中取消、运行中取消、重复取消和旧任务回调均不留下镜头锁定，也不改写后来创建的任务。

**W2 / P1 — Domain Model Distortion：背景音乐可能把成片时长拉长**

Symptom: `export.py:58` 对没有 trim_out 的音乐保留完整长度；`:91` 使用 `amix:duration=longest`。最终封装没有把音频与容器时长约束到时间线终点。

Source: Evans — *Domain-Driven Design*, Domain Model；Hunt & Thomas — *The Pragmatic Programmer*, Orthogonality。

Consequence: 短视频加入较长背景音乐后，文件时长由音乐决定，视频结束后仍有长尾音频。

Remedy: 以冻结时间线的总时长作为输出长度，混音补齐或裁剪到该终点，再做最终封装。不要仅加 `-shortest`，否则音频较短时又可能截短视频。

复现：真实 **1 秒视频 + 3 秒音乐** 导出后，成片时长为 **3 秒**，状态为 done。

验收：长音乐、短音乐、延迟进入的音乐和纯视频均保持预定时间线长度。

**W3 / P1 — Domain Model Distortion：删除一条音轨可能破坏仍在使用同一素材的其他音轨**

Symptom: `tracks.py:124` 的 delete_audio_track 删除记录后直接 unlink 音频文件；delete_voice 也有同样策略。没有检查同一文件是否仍被其他记录引用。

Source: Evans — *Domain-Driven Design*, Entity / Value Object identity；Ousterhout — *A Philosophy of Software Design*, Information Hiding。

Consequence: 同一个上传素材被多次放入时间线后，删除其中一条会让其他条目失效；共享素材库无法可靠复用。已创建导出的冻结副本不能代替对活动项目素材引用的保护。

Remedy: 分离「删除使用记录」与「删除素材文件」，增加素材引用检查和统一回收入口；无引用、可回收的文件才允许清理。可先用保守的不物理删除策略，再补可预览的清理工具。

复现：两条音乐轨引用 `/outputs/shared.wav`，删除第一条后，**第二条记录仍存在，但文件已不存在**。

验收：跨条目、跨项目与对白/音乐共用同一资产时，移除一个引用不破坏其他使用者。

**W4 / P2 — Change Propagation：恢复状态边遍历边修改，offset 分页会漏任务**

Symptom: `queue_manager.py:40` 通过 status + OFFSET 分页；恢复流程会在遍历时将 running / needs_attention 变为其他状态，随后 offset 继续增加。查询结果集缩小，后续任务被跳过。

Source: Hunt & Thomas — *The Pragmatic Programmer*, Orthogonality。

Consequence: 恢复中的任务数量超过一页时，部分任务可能继续停留在 running 或 needs_attention。现有大量 pending 测试不能覆盖这一分支，因为恢复 pending 时没有改变 status 查询条件。

Remedy: 使用稳定游标分页或先固定待处理 ID 集合；不要在正在缩小的结果集上递增 offset。稳定排序需包含唯一 ID，避免相同 created_at 引发遗漏。

复现：向隔离数据库批量放入 **201 个 running 任务**，执行恢复后得到 **200 个 failed，仍有 1 个 running**。

验收：分别对超过 200 条的 running、needs_attention、pending，以及相同创建时间的数据验证所有 ID 都恰好处理一次。

Recommended fix order: W1 → C1 → C2 → W2 → W3 → W4。

## 测试与证据

- 本轮新建了 6 个隔离问题复现检查，结果 **6 passed，7.12 秒**。这里 passed 表示“已确认当前存在该缺陷”，不是正确性回归通过。
- 两个音视频问题使用打包 FFmpeg 实际合成、导出及解码检查；服务地址问题替换网络 I/O，未调用真实 ComfyUI。
- 复现文件：`.phase1/review-20261020/test_review_repros.py`；该目录被 git 忽略，使用独立数据库、设置路径和临时媒体。
- 先前记录为后端全量 210 passed / 1 skipped，加真实导出测试 1 passed；本轮未重复跑无改动的全量构建。此前成功用例未覆盖原生音轨、超长 BGM、pending 取消后重做、服务切换和状态变更分页。
- 本轮未修改业务源代码。当前上轮改造仍保留在未提交工作区，后续适合按任务状态、服务绑定、音频与素材保护拆分提交。

## 后续产品与交付改进

| 优先级 | 方向 | 建议交付 |
|---|---|---|
| P1 | FreeVideo 实机验收 | 按用户已确认的“暂未安装、先完成接入”状态保留待验收项；安装后验证三种生成、参考图/LoRA、原生预览缓存与重连，不能以 mock 通过代替出片效果 |
| P2 | 素材生命周期 | 建资产引用索引、占用统计、清理预览与可恢复删除；覆盖 outputs/assets、workflows、原文件与导出历史 |
| P2 | 版本复用体验 | 将“复用常规参数”和“沿用镜头/角色/工作流再生成”明确分开；原生二采缺缓存时给出可执行恢复选项 |
| P2 | 时间线与长视频 | 原声轨与音量控制、同步 A/B、接缝前后预览、片段失败恢复和长视频上下文链 |
| P2 | 测试与安装 | 增加生成→取消→重做→升级→导出的 UI 流程测试；锁定已验证的 Python/torch/diffusers/ComfyUI 组合，增加干净环境安装与升级回归 |
| P3 | 资源与导演能力 | 收集实际耗时和内存峰值后做预测、OOM 恢复与独立推理进程；AI 导演继续后置 |

## Summary

先完成上述 6 项可靠性收尾，再扩大长视频与自动导演功能。下一批最小可验收范围是取消后可重做、任务全程绑定正确服务、成片保留原声且时长正确，以及共享素材删除保护。
