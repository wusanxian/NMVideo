# 工作包 2A：数据库访问与事务边界 — 实施记录

日期：2026-10-17。基线：`0a8dc82`（审查报告提交）。回滚：`git revert` 本 commit 或回到 tag `phase2-start`。

## 1. 变更内容

### `backend/app/db.py`
- **统一访问接口**：新增 `fetch_one / fetch_all / execute / transaction() / insert_and_get`。完整语句（含游标结果读取）与提交/回滚都在同一把锁内，消除「读无锁 + 写另锁」的边界模糊（D1）。
- **`transaction()`**：`BEGIN IMMEDIATE` 单写锁，异常整体回滚；约定期务内只用 yield 出的 conn（锁不可重入）。进入前清理残留隐式事务。
- **`insert_and_get()`**：INSERT 与按 id 读回在同一事务，创建成功必返回实际记录，读不到直接抛异常（不用 null 掩盖失败）。
- **启动审计化（D2）**：`_enable_foreign_keys()` 不再 DELETE 孤儿行，只 `PRAGMA foreign_keys = ON`；启动时跑只读 `audit_orphans()`，结果存 `last_orphan_audit` 并打印告警与样例 id。无异常库启动零改写。
- **`backup()`**：SQLite Backup API 一致性备份。
- **`repair_orphans()`**：显式修复——先备份再按审计清单删除，返回 `{backup, removed, total}`；无孤儿时不做任何操作。
- **忙等策略**：连接 `timeout=10s`；`SQLITE_BUSY/LOCKED` 翻译为 `DatabaseBusyError`（可重试），不做无限重试、不重放写请求。

### `backend/app/main.py`
- 注册 `DatabaseBusyError` 异常处理 → HTTP 503 + `Retry-After: 2`，detail 含可读原因。

### `backend/app/storyboard.py` / `assets.py` / `tracks.py`
- 三个模块的 `_row/_rows/_write` 全部改走 db 统一接口，不再触摸 `db._conn/db._lock`（grep 验证零残留）。
- 创建函数（project/scene/shot/version/review_history/export/character/voice/audio_track/subtitle + `create_job`）改用 `insert_and_get`。
- `import_script` 改用 `db.transaction()`，**剧本文本更新并入同一事务**（S3 前半）：重建失败时旧场景/镜头/剧本文本一起回滚。

### `backend/scripts/db_maintenance.py`（新增）
- `audit`（只读）/ `backup`（带 sha256 边车文件）/ `repair`（先备份再清理，输出前后表行数对比）。

### `backend/tests/`（新增，pytest）
- `conftest.py`：`fresh_db` fixture——每个测试一个独立 SQLite 文件，并把 storyboard/assets/tracks 的 db 引用一起换掉，真实 `aivideo.db` 永不被测试触碰。
- `test_db_concurrency.py`：10 线程 × 20 轮混合请求（屏障同步起跑）；事务挂起时读者阻塞、看不到半完成数据；事务异常整体回滚。
- `test_transactions.py`：import_script 重建中途注入失败 → 旧场景/剧本/时间线全留；append 跳过同名场景；insert_and_get 成功返回记录、失败抛异常且不落行、无残留事务。
- `test_legacy_db_audit.py`：旧库（FK 曾关闭 + 孤儿行）启动只审计不删除、正常数据不变；repair 先备份再清理、**备份可完整恢复**（孤儿与正常数据都在）；健康库启动与 repair 均零操作。

## 2. 测试结果（真实执行）

命令：`cd backend && env -u PYTHONPATH .venv/Scripts/python.exe -m pytest tests/ -v`

```
13 passed in 38.36s
```

- 并发：10 线程混合请求无异常；项目/任务/角色写入数 = 200/200/200，id 无重复；库内行数与内存记录一致。
- 隔离：事务挂起 0.4s 期间读者未读到半完成数据，提交后读到。
- 事务：注入失败后旧场景标题、剧本文本、时间线与操作前完全一致。
- 旧库：启动后孤儿仍在（2 条）、正常数据 3 行不变、`PRAGMA foreign_keys=1`；repair 后审计为空，备份拷回原路径重开孤儿与正常数据俱在。
- 回归：TestClient 冒烟（health/项目/场景/镜头/剧本 preview+rebuild/审核 400+404/时间线/队列/引擎/ComfyUI 状态）全部通过。

注：`env -u PYTHONPATH` 仅为规避 WorkBuddy 沙箱宿主注入的 sitecustomize（pip 缓存批量删除守卫），真实用户机器不需要。

## 3. 剩余风险与后续

- `list_jobs(limit=1000)` 的恢复上限属于 2B 范围（分页恢复），本包未动。
- 版本登记 + 任务完成 + 镜头状态的单事务登记属于 2B 归档协议，本包提供了 `transaction()` 原语但未改调用点。
- 外键语义未增加新的破坏性级联；`_FK_TABLES` 清单维持原样。
- 并发验收用了 10 客户端 × 200 次混合请求 + 可控同步点；文档建议的 1000 次负载条件未全额执行（SQLite 单连接串行下吞吐非本包目标），如需要可按同参数放大。
