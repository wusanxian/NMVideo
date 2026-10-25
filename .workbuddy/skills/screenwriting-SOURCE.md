# 编剧 Agent Skills（第三方，随项目携带）

来源：[jtydhr88/screenwriting-skills](https://github.com/jtydhr88/screenwriting-skills)
导入版本：`51115f18d160aa8b2ac5813e4432b6f0b425358f`（2026-10-03）
导入日期：2026-10-25
放置位置：项目级 Agent Skills（`.workbuddy/skills/`），供在本仓库内工作的 agent 使用。

## 这是什么

26 个编剧方法论 skill，按 `agentskills.io` 开放标准编写（`SKILL.md` + `reference*.md`），
正文中文、输出语言跟随提问语言。知识来自 47 本编剧技法书与 23 卷已出版剧本/曲谱/戏剧文本。

**与 NMVideo 产品功能无关**：这批 skill 不参与 `backend/app/novel_script.py` 的
LLM 调用链，不影响打包与运行时。若要接入产品（AI 编剧提示词），需另做一层
索引与 token 预算控制——26 个 `SKILL.md` 合计约 39.6 万字符，不可能全部注入。

## 四层结构

| 层 | 目录 | 用途 |
|---|---|---|
| 1 通用编剧学 | `sw-workflow` `sw-premise-theme` `sw-story-structure` `sw-character-conflict` `sw-dialogue` `sw-scene-craft` `sw-format-adaptation` `sw-truby-anatomy` `sw-genre-anatomy` | 前提、结构、人物、对白、场景、格式 |
| 2 媒介层 | `sw-series-structure` `sw-series-engine-bible` `sw-writers-room` `sw-sitcom-comedy` `sw-chinese-opera-banqiang(-cases)` `sw-chinese-opera-qupai(-cases)` | 剧集、编剧室、半小时喜剧、中国戏曲 |
| 3 传统与行业 | `sw-american-case-studies` `sw-japanese-screenwriting` `sw-korean-french-screenwriting` `sw-chinese-series-practice` `sw-industry-business` | 各国方法与产业运作 |
| 4 大师语料库 | `chekhov-dramaturgy` `ozu-screenplay-style` `succession-series-writing` `sw-series-case-studies` | 一手剧本拆解 |

## 常用入口

| 任务 | 建议组合 |
|---|---|
| 从零起一个剧本项目、追踪进度 | `sw-workflow`（维护 `story-bible.md` 项目状态） |
| 判断这个故事能不能立 | `sw-premise-theme` |
| 三幕/节拍/结构诊断 | `sw-story-structure`，需要更细用 `sw-truby-anatomy` |
| 对白改不动、太直白 | `sw-dialogue` + `sw-scene-craft` |
| 写剧集、拆分集与季弧 | `sw-series-structure` + `sw-series-engine-bible` |
| 小说/IP 改编成 30–50 集剧 | `sw-chinese-series-practice` + `sw-series-engine-bible` |
| 剧本排版与 Fountain 输出 | `sw-format-adaptation` |
| 戏曲（京剧/豫剧/昆曲/传奇） | `sw-chinese-opera-banqiang` 或 `sw-chinese-opera-qupai`，全本案例看对应 `-cases` |

## 使用注意

- 单个 `SKILL.md` 约 9k–27k 字符；`reference*.md` 是实例与引文，按需再读，别一次全读。
- `sw-workflow/terms.md` 是中英术语对照表（logline、act out、beat sheet 等），
  不确定译名时先查它，不要自创译名。
- 输出语言跟随提问语言：中文问就中文写，但**剧本正文保持用户自己的语言**。

## 授权

- skill 本身 MIT，见 `screenwriting-LICENSE.txt`。
- 书中引文版权归原作者与出版方；译者著作权单独存在（即使原作已进入公版）。
  MIT 不覆盖这些内容，边界见 `screenwriting-NOTICE.txt`。对外分发本仓库时，
  这两个文件必须一起保留。

## 升级方法

```bash
# 1. 下载新版本（本机 git clone 走代理常失败，用 codeload tarball 更稳）
curl -sL "https://codeload.github.com/jtydhr88/screenwriting-skills/tar.gz/refs/heads/main" -o sw.tgz
tar -xzf sw.tgz

# 2. 覆盖 skills（保留本说明与 license 文件）
cp -r screenwriting-skills-main/plugins/screenwriting/skills/. .workbuddy/skills/

# 3. 校验：name 与目录一致、description ≤1024、reference 链接可解析
python tools/check-skills.py plugins/screenwriting/skills --strict

# 4. 更新本文件顶部的 commit 与日期
```
