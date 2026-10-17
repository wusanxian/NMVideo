# Electron 整合：面板化桌面壳 — 实施报告

> 对应《下一阶段改造与优化方案.md》用户子任务 1（第一阶段发布收尾）。
> 前置依赖 2A（ec77a38）、3A（807344c）、2B（879b17c）、2C（21a0ff2）已提交，本包全部建立在其上。
> 审查依据：`docs/reports/phase2-review.md` §1.1 / §3 / §4「Electron」行。
> 目标：① 默认进入创作工作台；② 安装/服务管理/终端/更新收入可展开系统面板（不再整页切回启动台）；③ 两侧边栏去重；④ 返回启动台保留工作台状态；⑤ WebContentsView 迁移结论记录。

## 一、审查结论逐项核对

| 审查行 | 结论 | 说明 |
|---|---|---|
| 保留双端（桌面 + 浏览器） | 不变，已做到 | webview 仍加载 `http://127.0.0.1:<port>`，前端可独立 dev/静态托管 |
| 两套侧边栏重复 | **已解决** | 系统侧边栏去重为 3 项（见 §2.1） |
| 默认进入创作工作台 | **审查结论不准确，行为早已存在** | 见下方「核对说明」 |
| 系统分区 → 可展开面板 | **已解决** | 右侧抽屉 `#system-panel`（见 §2.2） |
| 返回启动台不丢工作台状态 | 原本已做到，本包测试固化 | webview 不销毁 + `loadedOrigin` 防重置 + 路由记忆 |
| WebContentsView 迁移 | **本批不迁移** | 引用审查报告 §3 结论：webview 安全配置已满足要求（主窗 `sandbox:true`、`will-attach-webview` 校验 src+partition、`guardContents` 拦导航、`setWindowOpenHandler` 外链确认），迁移收益低于风险，列入 3B/发布收尾候选 |
| no-sandbox 与 webview 导航安全 | 不变，已加固 | 本包未触碰 `main.js`/`preload.js`/`security.js`，无新 IPC |

### 核对说明：「默认进入创作工作台」一行

审查报告以 `renderer/app.js:82 view:'boot'` 判定「未做到，需手点打开应用」。
经 `git show 63c5b5a:desktop/renderer/app.js` 核对（审查基线自身）：

```
app.js:246  if (!state.loadedOrigin || (!wasOnline && state.view === 'app')) setView('app')
```

该逻辑由 445cbe0 引入且是审查基线 63c5b5a 的祖先——**首次 `backend:ready`
（或 boot() 时 `backendStatus()` 已在线）即自动 `setView('app')`**，`view:'boot'`
只是就绪前的初始态。审查该行判断不准确。本包不改变该行为，但用
`shell-ui.test.js` 的「backend ready enters the workspace by default」
将其固化为回归测试（含面板收起、侧栏高亮、webview src 注入断言）。

## 二、变更内容

### 1. 侧边栏去重（`desktop/renderer/index.html`）

原 7 项（启动/安装/正在处理/打开 ComfyUI/终端/设置/版本号）中，
「启动/安装/正在处理/终端/设置」均以 `setView('boot')` 整页切回启动台，
与前端创作侧边栏（作品/剧本/资产/生成/剪辑/声音/设置）语义重叠
（审查报告点名的「设置」重叠最典型）。去重后仅 3 项：

- `data-action="workspace"` 工作台 —— 在线进应用视图；离线回系统视图看进度
- `data-action="system"` 系统管理 —— 开关右侧系统面板（与标题栏 `#system-btn` 同一开关）
- `data-action="comfy"` 打开 ComfyUI —— 走 `desk.openComfyUI()`（浏览器打开 8188）

侧栏增加 `.active` 高亮（应用视图亮「工作台」，面板展开亮「系统管理」）。

### 2. 系统面板（`index.html` 新增 `#system-panel` + `styles.css` 抽屉样式）

- 右侧固定抽屉（`position:fixed`，宽 480px，`translateX(102%)` → `.open` 时 0，
  0.28s 过渡，z-index 30），**悬浮于应用视图之上，不触发 webview 重排/重载**
- 内容四分区：`#panel-status` 服务状态卡（+ `#service-timer` 计时器）、
  `#install-info` 安装信息、`#terminal` 终端、`#update-bar` 更新
- `aria-hidden` / `aria-expanded` / `aria-controls` 齐备；标题栏「关闭」按钮收起
- **boot 视图 = 系统视图**：面板自动展开（安装进度/终端直接可见）；
  app 视图默认收起，可随时悬浮展开——满足「不得整页切回启动台离开应用视图」

### 3. 交互模型重写（`desktop/renderer/app.js`）

- `state.panelOpen`；`setView(v)` 仅在**视图真正切换**时同步面板
  （进 boot 自动展开、进 app 默认收起；app 内重复触发不动面板）
- `setPanel(open)`：toggle `.open` class + `aria-hidden` + `#system-btn`
  `aria-expanded`/`.active` + 侧栏高亮（`renderNav()`）
- `#system-btn` 由「视图切换」改为「面板开关」；`#panel-close-btn` 收起；
  侧栏 `system` 项同一开关
- `renderProgress()` 改双卡同步：遍历所有 `.progress-card`
  （boot 英雄卡 + 面板服务卡渲染同一份状态），删除单卡 DOM 引用
- `backend:tick` 由「侧栏 processing 徽章」改为 `#service-timer`（MM:SS，
  就绪后隐藏）
- i18n：新增 `workspaceNav`/`close`；删除随旧侧栏一起失效的死键
  （start/install/processing/settings/processingBtn/pauseBtn/connecting/stop/version）
- `#open-app-btn` 点击条件补 `state.online`（与 disabled 态一致）
- `preload.js` / `main.js` **零改动**：新交互全部在 renderer 内完成，
  无新 IPC、无新事件通道（EVENTS 白名单不变）

### 4. 样式（`desktop/renderer/styles.css`）

- 删除 `.app-bar { display: none }`（第 321 行）——应用视图的 app-bar
  （「返回启动台」按钮）恢复可见
- 新增 `.system-panel` 抽屉、`.system-panel-head/body`、`.service-timer`、
  `#system-btn.active`；面板内卡片 margin 修正（`gap` 接管间距）

### 5. 测试

**新增 `desktop/tests/shell-ui.test.js`（jsdom，12 用例，全部真实执行通过）**：
真实加载 `index.html` + 执行 `app.js`（stub `window.desk` 镜像 preload API），
覆盖：侧栏去重、面板四分区与自动展开、默认进入工作台、
面板开关不离开工作台且 webview src 不变、返回启动台保留 webview、
路由记忆与新会话恢复、双卡同步、tick 计时器、离线重连、错误重试、
i18n 切换、ComfyUI 桥接。

**更新 `desktop/tests/electron-smoke.js`（真实 GUI 测试）**：
`#system-btn` 断言从「切回启动台（sidebar display 变化）」改为
「`#system-panel` open class + 不离开应用视图（webview-wrap display 不变、
sidebar 保持 none）」；hardware 检测在面板内完成；新增
「返回启动台保留 guest → 重进工作台恢复同一 guest」往返断言。
**该测试需真实 Electron GUI，本沙箱无法执行——标注「未执行」**，
待用户在 Windows 真机按 `desktop/tests/run-electron-smoke.js` 流程验证。

## 三、测试结果（全部真实执行）

```
desktop> node --test tests/*.test.js     # node 22.22.2（managed）
================== 18 passed ======================
  ok 1-6   runtime.test.js（既有：URL 白名单/IPC 信任/后端复用/重试串行/探针）
  ok 7-18  shell-ui.test.js（本包新增 12 例）
```

| 文件 | 用例数 | 状态 |
|---|---|---|
| `desktop/tests/runtime.test.js` | 6 | 通过（既有回归） |
| `desktop/tests/shell-ui.test.js` | 12 | 通过（新增） |
| `desktop/tests/electron-smoke.js` | — | **未执行**（需真实 Electron GUI；断言已按新交互模型更新） |
| `backend/.venv pytest` | — | 未执行（本包零后端改动，无回归面） |

jsdom 无布局引擎，抽屉 transform/display 的计算值由 electron-smoke.js
在真实 Electron 中验证（`getComputedStyle` 断言已就位）。

## 四、实现期发现并修复的问题

1. **`styles.css:321` `.app-bar { display:none }` 会隐藏「返回启动台」按钮**——
   旧模型下 app-bar 无存在必要（系统操作靠侧栏），新模型它是应用视图
   唯一的返回入口，已删除该规则
2. **`backendReady` 自动进入与面板状态的竞态**——boot() 中 `setPanel(true)`
   必须在任何 `await` 之前同步执行，否则 `backend:ready` 抢先到达会把面板
   收起后又被 boot 重新展开；代码顺序已保证（同步段 → await → 状态查询）
3. **i18n 死键清理**——随旧侧栏移除的 5 个动作键 + 从未使用的
   connecting/stop/version，避免「字典里留着但界面找不到」的腐化

## 五、剩余风险

- **electron-smoke.js 未执行**：抽屉动画期间点击、真实窗口尺寸下
  480px 抽屉与 960px 最小窗宽的遮挡关系，需真机确认（jsdom 覆盖不到布局）
- **面板内按钮在收起态仍可 Tab 聚焦**（a11y 小瑕，未加 `inert`——
  jsdom 30 对 inert 属性支持不确定，避免测试环境差异）；可发布前补
- **路由记忆只记 pathname+search+hash**：hash 路由外的查询串场景
  （前端自建 state）不归桌面壳管，属既有边界
- **WebContentsView 迁移推迟**（审查 §3 结论）：webview 标签 deprecated，
  3B/发布收尾需重做 session 迁移与事件桥，本批不做

## 六、回滚点

本包为独立 commit，仅触及 `desktop/` 下 7 个文件（4 改 + 1 增 + 锁文件），
与后端/前端零耦合。回滚 = `git revert <本包 commit>`，
不影响 2A/3A/2B/2C 任何数据层行为。
