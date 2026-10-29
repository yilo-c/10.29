# 思忆集 · 发布说明与回滚预案（2026-08-22）

> 覆盖阶段一 M1 – 阶段四 M4 期间合入的后端关键改动。
> 单人核心系统原则：结构改动必须配可执行的回滚点；回滚优先回退代码，数据迁移幂等可重放。
> 文档模板沿用 `docs/release/release_notes_template.md`；本手册补充按改动点细分的回滚路径。

---

## 1. 本次涉及的关键改动清单

| 改动点 | 涉及模块 | 数据影响 |
|--------|----------|----------|
| 审计写路径根治（独立 DB + 单写线程） | `core/audit_writer.py`、`core/audit_rollback.py`、`api/integration_hub.py` | 新增 `data/audit_trail.db`；旧 `audit_trail`/`integration_audit_logs` KV 键做成启时迁移（幂等） |
| SQLite 写放大优化 | `data/local_storage.py`、`data/sqlite_store.py`（`append_to_list`） | 无 schema 变更；原地 `json_insert` 追加 |
| 子进程隔离（opt-in） | `plugins/subprocess_host.py`、`plugins/sandbox.py` | 默认关闭（`SIYI_PLUGIN_ISOLATION` 未设=进程内沙箱），无数据影响 |
| 能力路由遥测 | `plugins/capability_router.py`、`services/system_health.py` | 内存计数器，透出 `capability_routing` 子系统，无持久化 |
| 高系统风险工具收紧 | `services/tooling/tool_policy.py` | `backup_snapshot`/`delete` 等需沙箱审批，行为级 |

---

## 2. 回滚触发条件（闭环反证，防正向自证）

触发优先级从高到低，命中即按 §4 对应路径回退：

1. `notes.update` 报 `database is locked` 复发（P0-1 类）→ 审计路径改动（§4.1）
2. 回滚查询 `list_rollbackable` 命中不到刚完成写 → 审计 pending 内存合并失效（§4.1）
3. 启动后 `audit_trail.db` 无法打开 / 迁移报错未跳过（fail-fast 拦下）→ §4.1
4. 开启 `SIYI_PLUGIN_ISOLATION=subprocess` 灰度期 info 链路功能回退（T3.6 遥测 `fallback_hits` 激增或 `plugin_errors`>0）→ 回进程内模式（§4.3）
5. 写放大基准回退（新路径不优于旧路径）→ §4.2 回退到 `load→append→save_json`

---

## 3. 回滚前状态保存（通用，最高优先）

```powershell
# 位置：MCP/后端数据目录下的 backups/snapshots/
# 备份经 mcp_server.store.backup_snapshot 每次最多保留最近 10 份
# 手动强制触发（需沙箱审批，high 级）：
#   工具 siyi_backup_snapshot(reason="pre-rollback-2026-08-22")
```

- 快照文件命名 `snapshot_<UTC时间戳>.json`，内容为 SQLite 各核心 KV 键全量 JSON（与后端 `load_json` 同源）。
- 回滚前必做：取最新快照 `backup_id`，写入本手册 §7 回滚记录，便于事后对账不回滚错版本。

---

## 4. 按改动点的回滚路径

### 4.1 审计写路径（audit_writer 独立 DB）

**判断**：锁竞争复发（`database is locked`）或回滚查询不可见。

**回退步骤**：
1. `git revert` 涉及 commit：`core/audit_writer.py` → `core/audit_rollback.py` → `api/integration_hub.py` 的迁移与投递改动。
2. 删除迁移副作用：`data/audit_trail.db`（新库）保留数据不删；但**禁用启时迁移**（删迁移触发开关或回退代码后再启动，旧 KV 键已导入则幂等重放不会重复）。
3. 启动自检：`AppContext` fail-fast 关键属性白名单通过；`system.health` / WS 就绪。
4. 验证：50 线程并发 `notes.update` 零 `database is locked`（回退后回归既有路径）。

**为什么可安全回滚**：迁移是幂等可重放的（启动时检测旧键→导入→删旧键），`audit_trail.db` 与 `kv_store` 物理隔离，删除新库不影响既有笔记数据。

### 4.2 写放大优化（append_to_list）

**判断**：基准脚本 `scripts/bench_write_amplification.py` 新路径不优于旧路径（正常应 34.9MB/383x → 91KB/1.0x）。

**回退**：
1. `git revert` `local_storage.py`/`sqlite_store.py` 的 `append_to_list`；
2. `feedback_service.record_feedback` 改回 `load→append→save_json`；
3. 删除 `tests/test_append_to_list.py`（可选，保留则标记 skip）。

**数据影响**：无 schema 变更，数据形态不变，纯实现替换，回退零迁移。

### 4.3 子进程隔离（subprocess_host）

**判断**：灰度期 info 链路功能回退 / worker 频繁崩溃 / 遥测异常。

**回退**：
1. 环境变量 `SIYI_PLUGIN_ISOLATION` 取消或置空（`sandbox.choose_isolation_mode` 在启用子进程隔离前即读该变量，改动即时生效，无需重启仅需重启进程）；
2. 若已改默认值：`git revert` 改默认值那一次 commit；
3. 观察 `capability_routing` 子系统 `plugin_hits`/`fallback_hits` 归位。

**为什么低风险**：默认 off，灰度期为 opt-in 通道，回退即关通道，不影响业务。

### 4.4 工具收紧 + 能力路由遥测

**回退**：`git revert` 各自 commit；内存计数器无持久化，回退立即归零。无数据影响。

---

## 5. 前端（desk-notes）回滚

> 前端代码在独立 git repo（父仓库为损坏 gitlink，见执行计划 §5.7 注，未经本手册撬动）。
> 前端改动回滚在 desk-notes repo 内 `git revert` + `pnpm test`/`tsc` 验证后，在父仓库 `git add desk-notes` 刷新 gitlink hash。

---

## 6. 建议的执行节奏（单人）

1. 每批后端结构改动（审计/写放大/拆模块）合入前：先落 `tests/` 行为/golden 测试 + 契约测试兜底；
2. 上线即取一次 `siyi_backup_snapshot`（备份点 §3）；
3. 灰度项（子进程隔离）设观察窗口（≥1 周）并盯 T3.6 遥测；
4. 每完成一个改动点，在本手册 §7 记一行，形成可追溯发布史。

---

## 7. 回滚记录（执行后填写）

| 日期 | 改动点 | 回滚原因 | 回滚前 backup_id | 结果 |
|------|--------|----------|------------------|------|
| — | — | — | — | — |

---

*模板：`docs/release/release_notes_template.md`；本文为其按 2026-08-22 后端改动的实例化回滚手册。*