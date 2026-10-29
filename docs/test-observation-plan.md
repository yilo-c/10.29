# 思忆集 集成 & 冒烟测试观察计划（M5 观察期）

> 目标：通过全方面集成 + 冒烟测试，确认功能稳定可用，并以**连续 7 天 `gateway-fatal=0`** 作为 M5 核心退役（撤 butler 兜底）的 S1 解锁条件（计划 §0/§7）。
> 本计划由会话评估（12 维度）改进而来，可直接用于指导测试执行。

## 0. 前置与环境

- 后端入口：`python -m backend.headless_main`（port 18765，bind 127.0.0.1 仅本机）。
- 集成测试自启：pytest 经 `tests/conftest.py` 的 `rpc` fixture 支持 `SIYI_INTEGRATION_AUTOSTART=1`（临时数据目录 + 随机端口起临时后端），无需手动常驻后端即可跑集成层。
- 观察期实例：长期常驻真实后端（用户确认启动后），由每日 cron 记录 `gateway-fatal` 计数。
- 关键环境变量：
  - `SIYI_INTEGRATION_AUTOSTART=1` / `SIYI_INTEGRATION_REQUIRED=1`
  - `SIYI_WS_MAX_MSG_SIZE` / `SIYI_RPC_TIMEOUT=60` / `SIYI_WS_CLIENT_QUEUE=256`
  - `SIYI_LOCK_TAKEOVER=0`（测试进程避免被僵死接管误杀）

## 1. 分层计划（L0–L9）

| 层 | 目标 | 已有测试 | 待补/落地 |
|----|------|----------|-----------|
| **L0 冷启动稳定** | 后端/桌面端可重复冷启动且就绪 | `scripts/cold_start_stability_check.py`（桌面端 30 轮）、`test_headless_startup.py`（headless 配置/锁/async_main）、**`test_headless_cold_start.py`（本次已落地：真实进程冷启动计时 <30s + 可重复 + kernel.status 契约）** | ✅ headless 后端冷启动计时封装（断言 <30s） |
| **L1 冒烟** | 核心入口健康 | `test_kernel_status.py`、`test_smoke_quality_gate.py`、`test_headless_startup.py`、`test_chat_smoke.py`、`test_note_smoke.py`（ping 兼容由 L8 `test_ws_version_negotiation.py` 覆盖） | — |
| **L2 功能集成** | 各域方法经 RPC 真实调用 | `test_integration_*.py`（agent/api_keys/chat_notes_ext/http_client/inbox/memory/react_consistency/reading/remaining/remote/settings/transfer）、`test_sync_handlers_integration.py`、`test_mcp_integration.py`、`test_butler_resume_integration.py`、`test_sprint5_integration.py`、`test_api_integration_hub.py`、`test_agent_gateway*.py`（gateway e2e 覆盖并入 `test_agent_gateway*.py`） | **`test_integration_skill_cards.py`（M4 技能卡，已落地）**；建议补 `test_integration_ocr_transcription.py`/`image`/`evolution`/`info_radar`/`vocab`/`timeline` |
| **L3 边界** | 空/超长/分页/16MB/cron-FREQ | `tests/test_integration_boundary.py`（本次已落地） | `limit` 边界、`WS 16MB±1`、`cron FREQ=MONTHLY/YEARLY` 断言拒绝 |
| **L4 异常/故障注入** | 畸形 RPC、WS 中断、DB 锁、`sync.failed fatal`、**gateway 停机降级** | `test_fallback_client_full_chain.py`、`test_agent_gateway_contract.py`、`test_assistant_runtime_guard.py`、`test_m3_freeze_conformance.py`、**`tests/test_fault_gateway_outage.py`（本次已落地，S1 核心）** | `test_fault_gateway_outage.py`（停 DSH→断言降级 butler→恢复翻回，S1 核心） |
| **L5 并发/性能** | N 并发 WS、流式、广播吞吐、延迟预算 | `core/test_observability.py`、事件队列单测、**`tests/test_integration_concurrency.py`（本次已落地）** | 并发客户端压测（压到 `SIYI_WS_CLIENT_QUEUE=256`）、p95 chat 首 token <3s |
| **L6 安全** | 注入/XSS、sandbox 权限边界、transfer 鉴权、限流、key 静态加密 | `test_integration_api_keys.py`（key 处理覆盖并入此）、**`tests/test_security_injection.py`、`tests/test_sandbox_permission_boundary.py`、`tests/test_transfer_auth.py`、`tests/test_rate_limit.py`（本次已落地）** | `test_security_injection.py`、`test_sandbox_permission_boundary.py`、`test_transfer_auth.py`、`test_rate_limit.py` |
| **L7 一致性** | 事务回滚、checkpoint 崩溃、profile↔settings 镜像不变 | `test_sync_conflict_resolution.py`、**`test_storage_rollback.py`、`test_checkpoint_crash_recovery.py`、`test_profile_mirror_invariant.py`（本次已落地，46 用例）** | ✅ 三个文件已落地 |
| **L8 兼容** | WS schema 版本协商、手机/桌面 schema 对齐 | `test_chat_gateway_path.py`、`test_kernel_status.py`、**`test_ws_version_negotiation.py`、`test_mobile_desktop_schema_parity.py`（本次已落地）** | ✅ 两个文件已落地；修复 `tasks.create` 手机端 `done`→`completed` 映射 |
| **L9 回归** | 关键回归集 + 全量基线 | 200+ 单测、`method_registry`、event_bridge 单测、`test_trace_observability.py`、**`scripts/l9_regression_baseline.py`（本次已落地）** | ✅ 关键回归集（16 文件）已定义并跑通（274 用例 273 过 1 跳）；全量基线经 `--full` 产出 `acceptance_output/l9_baseline.json` |

> 修正（评估反馈）：M4 技能卡**已有单元层覆盖**（`tests/test_cron_actions.py` 5 用例：注册/front-matter+profile 映射/快档网关/深档网关/缺卡跳过）。L2 新增的 `test_integration_skill_cards.py` 是**集成层**补足——在真实后端加载后校验注册表与数据文件形态，二者互补。

## 2. 执行顺序（DAG）

```
L0 冷启动 ──► L1 冒烟 ──► L2 功能集成 ──► L3 边界 ──► L4 异常/故障 ──► L5 并发/性能
                                                              │
                                                              └─► L6 安全 ──► L7 一致性 ──► L8 兼容 ──► L9 回归
```
- 生命周期：真实后端单实例常驻（观察期目标）；每套件 `reset/seed` 隔离，破坏性套件（L4/L6/L7）用独立临时实例末位执行。
- 冷启动（L0）先于一切；冒烟（L1）全绿方可进 L2。

## 3. 测试数据设计

- 种子数据集 `tests/fixtures/seed_dataset.py`：notes/tasks/memories 多样样本（含超长/特殊字符/空值）。
- 技能卡 fixtures：直接复用 `data/skills/*.md` 4 卡作为契约固定件。
- factory-boy 式构造器：`tests/fixtures/factories.py`（note/task/memory 确定性生成）。

## 4. 验收门（可衡量）

| 指标 | 阈值 | 来源 |
|------|------|------|
| 冒烟通过率 | 100% | L1 |
| 集成通过率 | ≥98% 且 P0/P1=0 | L2 |
| `gateway-fatal` 事件 | 观测窗口累计 = 0 | L4 + 每日观测 cron |
| **S1 解锁** | 连续 7 天 `kernel.status.agent_gateway=active` 且无 fatal | `test_kernel_status.py` + `docs/obs-s1-log.md` |
| 冷启动耗时 | < 30s | L0 |
| p95 chat 首 token | < 3s | L5 |

## 5. 自动化

- 编排器：`scripts/milestone_acceptance.py --layer all --obs` 跑全层并产出 `acceptance_output/report.json`。
- 每日观测：复用 `system.self_maintenance` 05:00 机制，运行 `test_kernel_status.py` + `test_fault_gateway_outage.py`，向 `docs/obs-s1-log.md` 追加 `gateway-fatal` 计数；`fatal>0` 触发告警。
- 持续集成：本计划子集（`-m "integration"` + smoke）纳入提交门禁。

## 6. 已落地项（本次）

- `docs/test-observation-plan.md`（本文件）。
- `tests/test_integration_skill_cards.py`（L2：真实后端上下文校验 M4 技能卡注册与数据形态）。
- 评估结论：原 12 维度中 9 不足 / 3 部分充分；优先补强技能卡集成测试、gateway 故障注入、可度量验收门。

### 追加（L3 + L5 落地，2026-08-16）

- `tests/test_integration_boundary.py`（L3，4 用例）：
  - `notes.list` limit 边界：1 / 0 / 100000 / -1 均不崩溃；
  - 空 payload / 空 query / 空 id 优雅返回；
  - cron 5 字段合法接受、空 name 拒绝；**偏差记录**：`FREQ=MONTHLY;BYDAY=MO` 等 RRULE 风格 schedule 当前被 `cron.add` 接受（惰性存储），但 5 字段 `_cron_matches` 永不命中 → 永不触发，待产品决策是否显式拒绝（计划原期望"拒绝"）；
  - WS 16MB 临界：`MAX-1KB` 正常收发；`MAX+1KB` 被服务端拒绝且后端存活（`ping` 复查通过）。
- `tests/test_integration_concurrency.py`（L5，4 用例）：
  - 32 并发独立 WS 连接调用 `kernel.status` 全返回 dict；
  - 单连接 200 连发 `ping`（逼近 `SIYI_WS_CLIENT_QUEUE=256`）：**无丢帧 + 响应 ID 无错乱 + 非限流响应全为 `pong`**；
  - p95 chat 首 token < 3s（opt-in，需 `DEEPSEEK_API_KEY` + 网关活跃，默认跳过）；
  - 冷启动预算：独立临时实例就绪 < 30s。
- **观测发现（限流）**：`headless_main.py` 配置 `RateLimiter(max_requests=200, window=60.0)`，且 `ws_server.dispatch()` 未传 `client_id`（默认 `"default"`）→ **所有 WS 连接共享全局 200 req/60s 配额**。L5 队列压测 200 连发 + 前置用例会触发 `RateLimitedError`（合法错误信封）。这是真实守卫行为，测试按"无丢帧/ID 无错乱/非限流响应全 pong"口径断言，并打印 `rate_limited` 计数供观测。若产品希望按连接独立配额，需在 `ws_server` 传入 per-connection `client_id`（超出本次测试范围，待决策）。

### 追加（L4 + L6 落地，2026-08-16，54 用例全通过）

- `tests/test_fault_gateway_outage.py`（L4，13 用例，S1 核心）：
  - **组件级（真实适配器，确定性）**：`DshGateway`/`NoneGateway` 停机语义——CLI 缺失（monkeypatch `shutil.which`→None）时 `health().available=False`、`submit()` 返回 `status="unavailable"` 不抛异常；停机（gateway 缺失/none）→ `degraded=True` + `butler(fallback)`；恢复（注入真实 DshGateway）→ 翻回 `gateway`；在场但 health 失败 **≠ 降级**（存在性判定只看 `name`，`details.gateway_health` 如实反映 available）；`chat_route=legacy` 强制 butler（显式配置非降级）。
  - **集成级（只读契约）**：`kernel.status` RPC 8 字段齐全、值域合法、`details` 与顶层一致、短窗口内无抖动。
  - **观测发现**：`DshGateway(cli_path=...)` 指向不存在路径时 `health().available` 仍为 True（只查路径字符串非空、不查可执行存在性），且 `submit()` 会抛未捕获 `FileNotFoundError`。当前真实停机由"无 cli_path + dsh 不在 PATH"触发（health 准确、submit 优雅降级）。建议后续在 `health()` 增加 `os.path.isfile`/可执行校验、`submit()` 捕获 `FileNotFoundError`（待决策，非本次范围）。
- `tests/test_security_injection.py`（L6-1，5 用例，独立临时后端实例）：
  - XSS payload（`<script>`/`onerror`）经 `notes.create` 原样存取、读回一致（服务端不执行/不 sanitize，转义责任在前端）；畸形参数（NoSQL 风格 `{"$ne":1}`/数组/`1';DROP TABLE`）被优雅拒绝不崩溃、ping 存活复查；超大 params（>16MB）被服务端拒绝且后端存活。
- `tests/test_transfer_auth.py`（L6-3，9 用例）：
  - 组件级：`_check_auth` 三分支（LAN 放行 / 缺 `_token`→auth required / 错 token→auth failed，精确匹配）；`_resolve_file_store_path` 穿越防护（`../` 逃逸拒绝、POSIX 绝对路径归一化进 base、Windows 盘符绝对路径拒绝、空字节剔除）。
  - 集成级（只读）：`transfer.token_info` 契约（`mode` ∈ {lan, secured}）；空参数 `transfer.send` 优雅返回错误不落盘。
- `tests/test_rate_limit.py`（L6-4，10 用例）：
  - 组件级：滑动窗口配额内放行/超配额拒绝/窗口滑动恢复/reset/per-client 独立配额/get_remaining；TokenBucket 突发与补充；全局 `get_limiter`/`reset_all_limiters`。
  - 集成级（独立实例）：205 连发 ping → **无丢帧、ID 无错乱、非限流响应全 pong、`rate_limited >= 1`**（全局 200/60s 配额强制生效）。
- `tests/test_sandbox_permission_boundary.py`（L6-2，14 用例，独立实例化避免单例污染）：
  - 破坏性操作入队即 pending、绝不立即执行；批准经 executor 执行且**剥离 dry_run 标记**、result 透传；无 executor 仅出队；拒绝/批量批准/清空；`_MAX_PENDING=200` 超限淘汰最早条目；P1-5 超时策略（`SECONDS=0` 禁用 / `reject` 静默丢弃且不触 executor / `dry_run` 以 `dry_run=True` 只读预演 / 非法策略回退 reject）。
- 配套：`tests/_isolated_backend.py`（独立临时后端辅助：随机端口 + 临时数据目录，供 L6 写入类/压测类用例隔离使用，避免污染共享后端）。
- 备注：conftest `rpc` fixture 返回**完整响应信封**（含 `id`/`jsonrpc`/`result`），集成断言需取 `["result"]`；`AgentRun` 为 dataclass 用属性访问。pytest 结束 `atexit` 清理 `.pytest-tmp-root` 的 PermissionError 为 Windows 无害告警。

### 追加（L7 + L8 + L0 + L9 落地，2026-08-16）

**L7 一致性（46 用例全通过）**：
- `tests/test_storage_rollback.py`（23 用例）：`StorageTransaction` 上下文管理器正常退出自动提交/异常自动回滚、多键原子性、手动 begin/commit/rollback、状态机守卫（重复 begin/无事务操作/重复提交）、`transactional` 装饰器（成功提交/失败回滚/tx 注入）、`BatchWriter`（flush 原子写/discard 丢弃/auto_flush 阈值/update 读暂存最新）、`get_changes` 变更记录。
- `tests/test_checkpoint_crash_recovery.py`（14 用例）：真实 `LocalDataStore` 持久化 → 新实例模拟崩溃重启；重启后 `scan_unfinished_tasks`/`recover_task` 恢复完整上下文；终端状态（completed/failed/cancelled）排除；多任务/多版本取最新；`DataStoreCheckpointAdapter.restore_and_get_remaining` 跨重启只重跑未完成步骤。
- `tests/test_profile_mirror_invariant.py`（9 用例）：settings→profile one-way 迁移（focus_topics / high_risk_require_confirm）、镜像不变（profile 有值后 settings 不反向覆盖）、profile 空时 fallback 到 settings、`save_settings_preferences` 不破坏 settings 原值、reset 后 fallback 恢复。
- **实现修复（RED→GREEN）**：
  1. `data/local_storage.py` `load_json`：已注册 LIST/DICT 键缺失或存 null 且显式 `default=None` 时，按注册默认值返回（list→`[]`、dict→`{}`），保证"从未写入"与"存 null"读到稳定形状（事务回滚恢复缺失键不再读到 None）。
  2. `core/storage_transaction.py` `commit()`：已回滚事务再提交为 noop（`is_rolled_back` 直接返回），与"commit without active 仍抛异常"的守卫语义区分。
  3. `assistant/user_profile.py` `merge_focus_topics`：显式写操作从默认空画像起步，不触发 settings 迁移（one-way 迁移仅发生在纯读 `load()`）。

**L8 兼容（8 用例全通过）**：
- `tests/test_ws_version_negotiation.py`（6 用例，模块级隔离后端）：WS 协议**无应用层版本协商**——固守信封契约 `jsonrpc:"2.0"`+id 回显；`ping`→pong；错误码 -32700/-32600/-32601；不带版本字段的客户端照常被服务；`system.info` 报告版本但无门禁。
- `tests/test_mobile_desktop_schema_parity.py`（8 用例，组件级真实仓库）：手机端 `pushNote` `{title,content,tags}` → `notes.list` 可读回全字段；手机端 `pushTask` `{text,done,priority}` → `tasks.create` 后 `completed` 映射正确、text/priority 保留；时间戳 ISO 字符串（手机端 `new Date()` 可解析）。
- **实现修复**：`backend/handlers/_tasks.py` `tasks_create` 增加 `done`→`completed` 映射——手机端 `pushTask` 发 `{text, done, priority}`，此前 `done` 不进 `is_completed`/`completed` 归一化，创建"已完成"任务拉回后丢失完成态。

**L0 冷启动稳定（3 用例全通过）**：
- `tests/test_headless_cold_start.py`：真实进程冷启动计时 <30s（计划验收门）；连续 2 轮可重复（`SIYI_COLD_START_ROUNDS` 可调）；就绪后 `kernel.status` 契约 8 字段齐全。复用 `tests/_isolated_backend.py`（随机端口 + 临时数据目录）。

**L9 回归**：
- `scripts/l9_regression_baseline.py`：定义关键回归集（16 文件：domain/schema 契约 + method_registry + providers + M4 定向 kernel/网关），跑 pytest 产出 `acceptance_output/l9_baseline.json`（逐文件 + 用例统计 + 耗时）；`--full` 追加全量基线。首跑关键回归集：274 用例 → **273 通过 / 0 失败 / 1 跳过**。
- 备注：`tests/` 现 256 个测试文件；全量基线经 `python scripts/l9_regression_baseline.py --full` 产出（默认串行，`--workers N` 可试 xdist）。**全量基线（2026-08-16 终测）**：3830 用例即使离线（SIYI_TEST_OFFLINE）也需 **>45 分钟**（套件体量 + 集成/并发/子进程类慢用例决定），脚本超时已调至 3600s（60min）。**xdist 并行实测不兼容**：集成测试依赖会话级 conftest 后端——直接 WS 测试（非 rpc fixture）不在 skip 门内、autostart 需同 worker 有 rpc 用例（`--dist loadscope` 可解）、临时后端 WS 就绪滞后 TCP、多 worker 多后端资源重；已修 conftest 隔离（AUTOSTART=1 绝不回退真实 18765，起不来即跳过）+ `test_integration_boundary._ws_url` 禁止 AUTOSTART 下回退真实后端。**真正启用 xdist 需先重构集成测试**（标记/分片后端依赖）。阻塞点记录：
  - ① `test_evolution_tech_radar.py` 原经 `TechRadar.scan()` 真实访问 pypi.org/api.github.com，慢/受限网络下单个用例数十秒且结果抖。已用 autouse fixture 打桩 `_fetch_latest_version`→`"9.9.9"`，单测 0.21s 纯本地。
  - ② 后端启动本身做**真实网络调用**（16 个 RSS 源抓取 + 调用真实 `api.deepseek.com`），本机网络慢/不稳时临时后端启动可达数分钟，使隔离集成测试与全量基线不可靠。**已实现 `SIYI_TEST_OFFLINE=1` 离线开关**（headless_main：跳过后台资讯采集 + autonomy 到期调度循环），conftest 与 `_isolated_backend` 的测试后端均默认开启；实测离线后端启动 0 次真实 HTTP 调用，隔离集成测试从 ~10s+ 降至 ~5s。conftest 另有 90s 兜底（临时后端起不来→回落真实后端或跳过集成）。
  - ③ 集成测试隔离（2026-08-16）：`SIYI_INTEGRATION_AUTOSTART=1` 时**始终**用随机端口+临时数据目录的隔离后端，不连常驻 18765 观察实例（防污染/防拖慢）；`test_integration_boundary.py` 的 `WS_URL` 改为运行时读 `os.environ["SIYI_WS_URL"]`（不再 import 时锁定）。
