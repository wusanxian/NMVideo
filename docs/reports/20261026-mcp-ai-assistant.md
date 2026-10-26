# NMVideo 内置 MCP 服务（AI 助手接入）

日期：2026-10-26 ｜ 状态：后端全链路实测通过，UI 已加，**未打包含未真机连 WorkBuddy 验证**

## 这是什么

对齐 FreeVideo v0.4.0 的「AI Agent 独立生成视频」形态：NMVideo 在本机暴露一个
MCP Streamable HTTP 端点，用户把连接地址复制粘贴到 WorkBuddy / Claude Code 等
AI 客户端，之后在对话里直接说「帮我把××生成一段 8 秒视频」，助手即可全自动完成
查状态 → 出片 → 轮询进度 → 汇报成片链接。

## 接入方法

1. 「模型服务」页 → 「🤖 AI 助手接入 · MCP」→ 点「开启」
2. 点「复制连接」，得到形如 `http://127.0.0.1:8000/mcp?token=<32位hex>` 的地址
3. 粘贴到客户端：
   - WorkBuddy：连接器管理 → 自定义 MCP → `{ "mcpServers": { "nmvideo": { "url": "<连接>" } } }`
   - Claude Code：`claude mcp add --transport http nmvideo "<连接>"`

## 安全模型

- 端点仅监听本机（应用主端口），token 鉴权（首次开启自动生成，可「换新令牌」）
- 未开启时端点一律 403；token 错误 401（query `?token=` 或 `Authorization: Bearer` 均可）
- token 在 GET /api/services/mcp 打码返回，完整值仅 POST /api/services/mcp/reveal

## 首批工具（出片闭环）

| 工具 | 说明 |
|---|---|
| `app_status` | 引擎可用性 / 队列概况 / LLM 配置状态 |
| `create_video` | prompt（必填）+ duration_seconds/preset/engine/尺寸/seed/preview；8s@16fps=128 帧超 REST 上限时钳到 121，引擎内部重采样 |
| `list_jobs` | 最近任务列表（prompt 截 80 字符） |
| `get_job` | 进度 + 成片 video_url + 分状态 hint |
| `cancel_job` | 取消（H3 原生去噪阶段无法即时中断，工具里已注明） |

工具直接复用 REST 路由函数（`routers/jobs.py` 的 create/get/list/cancel），
参数校验、入队冻结、引擎吸附与 UI 完全同源。

## 实现要点（踩过的坑）

- **mcp SDK 2.x**：`FastMCP` 改名 `MCPServer`（`mcp.server.mcpserver`），客户端 `streamablehttp_client` 改名 `streamable_http_client`，`InitializeResult.serverInfo` → `server_info`
- **内层应用路由在 `/mcp`**：`streamable_http_app()` 自带路由 `/mcp`，不能用 `Mount("/mcp")`（Mount 不匹配裸路径 `/mcp`，会被 SPA catch-all 抢走返回 index.html）；改用 `Route("/mcp", guard, methods=[GET,POST,DELETE])` 精确插入
- **lifespan 必须手动链入**：Starlette 不运行挂载子应用的 lifespan，缺了会报 "Task group is not initialized"；在 main.py lifespan 里 `async with mcp_lifespan()`
- **工具函数与路由函数重名会自我递归**（`get_job` 调 `get_job`），路由函数全部 `_api_` 前缀别名导入
- **单例 task group 不可反复进出 lifespan** → 测试的 TestClient fixture 必须 module 级共享

## 测试

- `tests/test_mcp_server.py` 13 个用例：守卫（403/401/放行）、开关与令牌轮换、
  五个工具行为（含错误路径、duration→帧数换算与钳制）
- 开发期端到端：uvicorn 起真服务 + mcp 客户端 SDK 完成握手/列工具/五个工具调用/
  真实建任务并取消（测试数据已从真实库清理）

## 未验证

- 未打包进桌面版（打 exe 时 payload 需含 `mcp/` 依赖，requirements 已加 `mcp>=2.0`）
- 未在真实 WorkBuddy / Claude Code 里连过（协议层用官方 SDK 客户端验证过，理论兼容）
- 令牌在 URL query 明文暴露给本机进程列表（`?token=` 会出现在命令行/日志）——本机威胁模型下可接受；后续可只用 Bearer 头
