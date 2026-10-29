# 思忆集 统一执行路线图

> 融合来源：
> - 架构审查 Part 1（架构级 P0-P2 + 子系统裁决）
> - 架构审查 Part 2（前端/手机端/RPC/数据库/错误处理/CI/测试/性能 8 领域）
> - 代码审查报告（行级 30 bug + 33 建议，backend/ai/memory/sync/handlers/tests）
>
> 审查日期：2026-06-22
> 状态：三份审查已去重合并，无矛盾

---

## 目录

- [P0 — 立即修复（本周）](#p0--立即修复本周)
- [P1 — 尽快修复（本月）](#p1--尽快修复本月)
- [P2 — 计划修复（下季度）](#p2--计划修复下季度)
- [P3 — 长期优化](#p3--长期优化)
- [子系统裁决执行清单](#子系统裁决执行清单)
- [测试补充路线图](#测试补充路线图)
- [四阶段执行计划](#四阶段执行计划)

---

## P0 — 立即修复（本周）

共 **12 项**。影响安全/数据正确性/启动/可维护性。

### 安全 (5项)

| # | 问题 | 位置 | 来源 | 修复方案 |
|:---:|------|------|:---:|------|
| P0-1 | JudgeAwareClient v2 缺少 `inject_xiaowen_persona` / `_sanitize_mcp_name` / `_is_explicit_user_action`，v1 反而完整 | `ai/client/_judge_aware_client.py` | 代码审查 B1 | 从 v1 迁移三个方法到 v2；v1 功能验证后删除 `_deprecated/` |
| P0-2 | 云同步 XOR 降级加密 + PBKDF2 100K 迭代不足 (OWASP 2023 ≥600K) + v1 固定 salt | `sync/cloud_sync_client.py` | 代码审查 B3 | 提升 PBKDF2 至 ≥600K；确保 PyCryptodome 为必需依赖；废弃 v1 固定 salt |
| P0-3 | CircuitBreakerRegistry `self._lock` 已定义未使用 → 并发创建分裂 | `ai/circuit_breaker.py:191-195` | 代码审查 B2 | 在 `get_or_create()` 中使用 `self._lock` 加锁 |
| P0-4 | AccessibilityService `exported="true"` — 任意应用可读屏/模拟点击 | 手机端 AndroidManifest.xml:43-52 | Part 2 P0-9 | 改为 `exported="false"`，或 `onStartCommand` 校验调用方包名 |
| P0-5 | Chromium 沙箱已禁用 `no-sandbox` | `desk-notes/electron/main.cjs:12-13` | Part 2 P0-8 | 评估移除 `no-sandbox` 的可行性；至少加固 CSP（移除 `'unsafe-inline'` scripts） |

### 数据正确性 (4项)

| # | 问题 | 位置 | 来源 | 修复方案 |
|:---:|------|------|:---:|------|
| P0-6 | `build_fn(item.get("id"))` 只传 ID，但 `build_note_sync_payload` 等期望整个实体字典 → 同步 payload 不完整 | `sync/generic_sync_service.py` + `sync/orchestrator.py` | 代码审查 B5 | 传递完整 `item` 实体而非仅 `item.get("id")` |
| P0-7 | `_generate_entry_id` 用 `id(datetime)` 作唯一标识（进程级不变）；`_load()/_save()` 无锁 → 并发写丢数据 | `memory/structured_store.py` | 代码审查 B6 | `_generate_entry_id` 改用 `uuid.uuid4()`；添加 `threading.Lock` |
| P0-8 | `sync/__init__.py` `_MODULE_NAMES` 含不存在的 `"news_sync"` → 每次懒加载抛 ImportError | `sync/__init__.py` | 代码审查 B4 | 从 `_MODULE_NAMES` 中移除 `"news_sync"` |
| P0-9 | 轮换 DeepSeek API key + 清理 60 份历史备份中的明文 key | `data/data/settings.json` | Part 1 P1-3.10 | 立即轮换；`scan_secrets.py` 接入 CI 阻止复发 |

### 架构 (2项)

| # | 问题 | 位置 | 来源 | 修复方案 |
|:---:|------|------|:---:|------|
| P0-10 | 上帝对象 AppContext (770行, 40+ 服务在 `__init__` 中创建) | `app_runtime/app_context.py` | Part 1 P0-1 | Builder 模式；按领域拆为 ChatContext / MemoryContext / SyncContext |
| P0-11 | 上帝对象 AutonomousAssistantManager (1000行, 5引擎混用) | `assistant/autonomy.py` | Part 1 P0-2 | 定义 ExecutionEngine 接口；路由集中到 ExecutionRouter |
| P0-12 | 跨层依赖违规 `assistant/` → `backend/` | `multi_agent_orchestrator.py` | Part 1 P0-3 | 提取 REACT_TOOL_CATALOG 到共享层；修复 `_ocr.py` 反向 import |

---

## P1 — 尽快修复（本月）

共 **22 项**。影响功能正确性/稳定性/可扩展性。

### 安全与加密 (3项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P1-1 | 同步 token 明文 localStorage（`secureStore.ts` 声明为敏感 key 但被绕过） | 手机端 `SettingsView.tsx:50-52` | Part 2 P1-10 |
| P1-2 | WS 认证 token 明文传输 (`ws://`) | 手机端 `chatSocket.ts:47` | Part 2 P1-11 |
| P1-3 | `_transfer.py` token 持久化失败仅 warning → 重启后已配对手机端失联 | `backend/handlers/_transfer.py:82-113` | 代码审查 B13 |

### 数据一致性与同步 (5项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P1-4 | 云同步 `_cursor` 秒级 vs Worker 毫秒级 → 增量同步游标错乱 | `sync/cloud_sync_client.py` | 代码审查 B11 |
| P1-5 | ChangeTracker 纯内存，重启后变更记录全部丢失 → 触发全量同步 | `sync/delta_sync.py` | 代码审查 B12 |
| P1-6 | 71 个 JSON 文件全量 O(n) 加载 + 无跨文件事务 → 万级数据性能崩溃 | `data/local_storage.py` | Part 2 P0-11 |
| P1-7 | `mark_dirty` 覆盖 `SYNC_STATE_CONFLICT` 状态 | `sync/note_sync.py` + `sync/task_sync.py` | 代码审查 B29 |
| P1-8 | LWW 同步无冲突解决 → 双设备同时编辑静默丢数据 | `sync/cloud_sync_client.py` | Part 1 P0-4 |

### 功能 Bug (6项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P1-9 | `_chat_memory.py` stream+tools 路径 ~200 行嵌套 5-6 层，`_fut.done()` 后 `await _fut` 存在竞态 | `backend/handlers/_chat_memory.py:519-711` | 代码审查 B8 |
| P1-10 | `_agent_cache` 模块级全局变量永不清理，`id(context)` 作缓存键 → 内存泄漏 + 可能返回过期 agent | `backend/siyi_tools.py:493` | 代码审查 B9 |
| P1-11 | EventBridge `broadcast()` 串行发送 → 一个慢客户端阻塞所有其他客户端 | `backend/event_bridge.py:93` | 代码审查 B10 |
| P1-12 | MemoryScheduler `_today_cache` 在 `_save_all()` 后未重新计算 → 返回过时到期记忆 | `memory/scheduler.py` | 代码审查 B14 |
| P1-13 | `base.py` `_request` 中 `last_error` 类型不一致 → `raise ... from last_error` 在 `last_error=None` 时静默丢根因 | `ai/client/base.py:131-143` | 代码审查 B7 |
| P1-14 | `settings.set` 清空 flat key 后无回滚，如果 provider sync 失败数据丢失 | `backend/handlers/_system_settings.py:73` | Part 2 RPC 审计 |

### 路由与人格 (3项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P1-15 | 路由逻辑三重重复：`JudgeAwareClient._classify_privacy()` + `JudgeScheduler._route_local_if_simple()` + `_route_by_rules()` | 三处 | Part 1 P1-1 |
| P1-16 | 人格注入不完整：桌面宠固定对话 vs AI 对话线分离；`persona/` 子包未创建 | 多处 | Part 1 P1-7 |
| P1-17 | 纯委托层 `MemoryManager` 30+ 方法纯透传无业务逻辑 | `app_runtime/memory_manager.py` | Part 1 P1-2 |

### 手机端 (2项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P1-18 | 8 个 Tab 全部条件渲染 → 每次切换状态全丢失 | 手机端 `App.tsx:160-168` | Part 2 P1-12 |
| P1-19 | `contentSync` 任务 `updatedAt: Date.now()` 覆盖远程时间戳 + 翻译去重只取前 30 字符 | 手机端 `contentSync.ts:125,148` | Part 2 手机端审计 |

### 错误处理 (1项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P1-20 | 29 个空 catch 块（8 桌面 + 21 手机）→ RPC 失败用户完全无感知 | 全局 | Part 2 错误处理 |

### 测试 (2项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P1-21 | `app_context.py` 完全无测试 — DI 根容器，修改频繁但零覆盖 | 测试空白 | Part 2 测试 + 代码审查 S24 |
| P1-22 | 统一 pytest 配置：两个 `pyproject.toml` 中 pytest 配置分裂 | 根 + 思忆集app | 代码审查 S23 |

---

## P2 — 计划修复（下季度）

共 **26 项**。影响代码质量/可维护性/性能。

### 安全与隐私 (4项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P2-1 | `_info_proxy.py` 硬编码知乎 Referer → 抓取其他站点可能被反爬 | `backend/handlers/_info_proxy.py:56-59` | 代码审查 B15 |
| P2-2 | 银行卡号正则 `\d{10,}` 误匹配订单号；身份证号误匹配 18 位纯数字 | `memory/extractor/_privacy.py` | 代码审查 B28 |
| P2-3 | 敏感词 "token" 过于宽泛；中文敏感正则无否定语境检测 | `memory/extractor/_privacy.py` | 代码审查 B28 |
| P2-4 | WS 调试模式 `traceback.format_exc()` 泄露给客户端 | `backend/ws_server.py:504` | Part 2 RPC 审计 |

### 类型安全 (1项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P2-5 | 大量 `Any`/`object`/`hasattr`/字符串类型比较代替正式接口 | 全局 | Part 1 P1-3 |

### 编码与孤儿代码 (3项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P2-6 | 全项目 UTF-8 乱码扫描：`judge.py` `脳`、`multi_agent_orchestrator.py` `馃搵`、`_chat_memory.py` 全文件 | 多处 | Part 1 P1-4 |
| P2-7 | 删除孤儿文件：`_autonomy_codex.py` (53行)、`_autonomy_governance.py` (45行)、`task_planner.py` (122行) | `assistant/` | Part 1 P2 |
| P2-8 | 清理 `ai/client/_deprecated/` 目录 → v1 有价值功能迁移到 v2 后删除 | `ai/client/_deprecated/` | 代码审查 S30 |

### 前端 (5项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P2-9 | 7 个组件硬编码 Ollama URL `http://localhost:11434` + `qwen2.5-7b:latest` 绕过用户 AI 配置 | `desk-notes/` 7 文件 | Part 2 P1-13 |
| P2-10 | WS 客户端无心跳/ping-pong → TCP 静默断连时 UI 仍显示"已连接" | `ws-client/ws-client.ts` | Part 2 P1-14 |
| P2-11 | ChatModule.tsx 964 行需拆分 | `desk-notes/` | Part 2 前端 |
| P2-12 | i18n 系统存在但 20+ 处组件硬编码中文字符串 | `desk-notes/` | Part 2 前端 |
| P2-13 | 无障碍严重不足：仅 3 文件有 `aria-*`，模态框无焦点捕获 | `desk-notes/` | Part 2 前端 |

### 性能 (5项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P2-14 | WS 流取消使用 50ms 轮询而非 `asyncio.Event` | `backend/ws_server.py:563` | Part 2 性能 |
| P2-15 | AppContext `__init__` ~410 行全量初始化（仅 5 服务懒加载） | `app_runtime/app_context.py` | Part 2 性能 |
| P2-16 | `AiResponseCache` TTL 24h 偏长 | `ai/client/fallback.py` | Part 1 P2-1 |
| P2-17 | 翻译任务路由到 DeepSeek 而非优先本地 qwen | `assistant/judge.py` | Part 1 P2-2 |
| P2-18 | `LocalDataStore` 100+ 独立 JSON 文件考虑迁移 SQLite | `data/local_storage.py` | Part 1 P2-4 |

### 代码质量 (4项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P2-19 | 情绪分类"无聊"→"neutral"（应为 negative）；"我不开心"中"开心"误匹配 positive | `memory/emotion_tracker.py` | 代码审查 B21 |
| P2-20 | `decision.py` 相似度用 Jaccard+单字切分而非 embedding 向量 | `memory/decision.py` | 代码审查 B27 |
| P2-21 | `_SEARCH_URL_PATTERNS` 定义但未使用 | `memory/behavior_extractor.py` | 代码审查 B22 |
| P2-22 | `asyncio.get_event_loop()` 在 Python 3.10+ 已弃用 | `sync/transport.py` | 代码审查 B23 |

### CI/CD (3项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P2-23 | `scan_secrets.py` / `coverage_gate.py` / `dod_gate.py` / `check-layer-boundaries.py` 已写好但未接入 CI | `scripts/` | Part 2 CI/CD |
| P2-24 | CI 无 bandit 安全扫描 / 无 `pip audit` / 无覆盖率报告 | `.github/workflows/` | Part 2 CI/CD |
| P2-25 | 测试覆盖率阈值 18%（极低），实际覆盖率 19.45% | `scripts/coverage_gate.py` | Part 2 测试 + 代码审查 S29 |

### 模块拆分 (1项)

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P2-26 | `memory/context.py` `__init__` 构造函数 20+ 服务 → Builder 模式或延迟初始化 | `memory/context.py` | 代码审查 S31 |

---

## P3 — 长期优化

共 **12 项**。

| # | 问题 | 位置 | 来源 |
|:---:|------|------|:---:|
| P3-1 | `_reading.py` `io_lock` 在函数内创建 → 多次调用产生多锁 | `backend/handlers/_reading.py:55` | 代码审查 B16 |
| P3-2 | Kiro 客户端每次重建 + HTTP 健康检查，无缓存 | `ai/client/_judge_aware_client.py:273-274` | 代码审查 B17 |
| P3-3 | `cache_ttl_seconds` 和 `cache_max_entries` 重复赋值 | `ai/client/fallback.py:72-78` | 代码审查 B18 |
| P3-4 | `set_sync` 用 `asyncio.ensure_future` fire-and-forget | `ai/client/_cache.py:128` | 代码审查 B19 |
| P3-5 | `get_monthly_stats` 用 `months * 31` 近似（2月边界数据遗漏） | `ai/usage_stats.py:76` | 代码审查 B20 |
| P3-6 | `_behavior_extractor._SEARCH_URL_PATTERNS` 定义未使用 | `memory/behavior_extractor.py` | 代码审查 B22 |
| P3-7 | `supertag_search.py` 正则匹配 `https://example.com` 中的 `https:` 为 field:value | `memory/supertag_search.py` | 代码审查 B24 |
| P3-8 | 全局 `_sandbox_manager` 单例，`set_tool_executor` 覆盖之前设置 | `backend/react_executor.py:88` | 代码审查 B25 |
| P3-9 | 周报 UTC 01:00-02:00 窗口内重启错过本周周报 | `backend/weekly_report_scheduler.py:214` | 代码审查 B26 |
| P3-10 | `summary_view.py` `is_stale` 注释说"超过24h"但只检查 `is None` | `sync/summary_view.py` | 代码审查 B30 |
| P3-11 | `MultiAgentOrchestrator` SubAgent 结果无缓存 | `multi_agent_orchestrator.py` | Part 1 P2-3 |
| P3-12 | `chat_ui.py` 位于 `assistant` 包 → 确认是否 PyQt6 时代死代码 | `assistant/chat_ui.py` | Part 1 P2-6 |

---

## 子系统裁决执行清单

| # | 组件 | 裁决 | 行动 | 预计清理 |
|:---:|------|:---:|------|------:|
| 1 | `web-ui/` | ❌ 删除 | 直接删除 | 0 MB |
| 2 | `desktop-pet-qinai/` | ❌ 删除 | 直接删除 | ~9 MB |
| 3 | `desk-notes/packages/mobile/` | ❌ 删除 | 确认 `shared` 包不依赖后删除 | ~2 MB |
| 4 | `desk-notes/FloatingBubble.bak.*/` | ❌ 删除 | 直接删除 | 若干 MB |
| 5 | `assistant/_autonomy_codex.py` | ❌ 删除 | 零导入，功能已在 codex.py | 53 行 |
| 6 | `assistant/_autonomy_governance.py` | ❌ 删除 | 零导入 | 45 行 |
| 7 | `assistant/task_planner.py` | ⚠️ 迁移后删除 | `is_chat_only` 迁到 planner.py | 122 行 |
| 8 | `tests/` (根目录) | ⚠️ 合并 | 审计独有覆盖→移入 `思忆集app/tests/` | 17 文件 |
| 9 | `automation_hub/` | ⚠️ 提取后废弃 | 工具注册+审批→`core/tool_registry/` | 230 文件/30K行 |
| 10 | `ai/client/_deprecated/` | ⚠️ 清理 | v1 功能迁移至 v2 后删除 | — |
| 11 | `docs/archive/reports/` 90+ 篇 | 📦 压缩归档 | 保留索引 | 大量 |
| 12 | `sync/__init__.py` `news_sync` 引用 | ❌ 删除 | 从 `_MODULE_NAMES` 移除 | 1 行 |

---

## 测试补充路线图

### 当前状态
- 测试文件 ~220，用例 ~2,500+
- 覆盖率 **19.45%**（阈值 18%）
- 110 个已知失败 + 3 个文件 collection broken

### 目标

| 阶段 | 目标覆盖率 | 新增测试模块 |
|:---:|:---:|------|
| 第 1 月 | 25% | `app_context.py` + `sync/` 核心 (channels, delta_sync, orchestrator, transport) |
| 第 3 月 | 35% | `core/` 补 22 文件 (permission_matrix, settings_migration, rwlock 等) + `backend/` 补 7 文件 |
| 第 6 月 | 50% | `browser/` + `plugins/` + `evolution/` + `knowledge/` |

### 立即修复的测试问题

| # | 行动 | 参考 |
|:---:|------|:---:|
| 1 | 修复 3 个 collection broken 文件 | 代码审查 S28 |
| 2 | 统一两个 `pyproject.toml` 的 pytest 配置 | 代码审查 S23 |
| 3 | `conftest.py` 硬编码 D: 盘路径改为 `tempfile` | Part 2 测试审计 |
| 4 | 33 处 `time.sleep()` 逐步替换为 `mock`/`asyncio` | Part 2 测试审计 |
| 5 | 为所有测试添加 `@pytest.mark` 分类 (unit/integration/slow) | Part 2 测试审计 |
| 6 | 覆盖率门禁阈值提升至 25%（当前 18%） | 代码审查 S29 |

---

## 四阶段执行计划

### 第一阶段：止损（第 1 周）

```
P0-1  JudgeAwareClient v2 安全功能补全      🔴 安全
P0-2  云同步加密强度提升                    🔴 安全
P0-3  CircuitBreakerRegistry 加锁            🔴 安全
P0-4  AccessibilityService exported→false    🔴 安全
P0-5  Chromium 沙箱评估 + CSP 加固           🔴 安全
P0-6  build_fn 签名不匹配修复                🔴 数据
P0-7  StructuredMemoryStore 锁 + UUID       🔴 数据
P0-8  sync news_sync 幽灵引用删除            🟡 启动
P0-9  DeepSeek key 轮换 + 60 备份清理        🔴 安全
```

+ **子系统清理:** 删除 web-ui/、desktop-pet-qinai/、desk-notes/packages/mobile/、FloatingBubble.bak/、2 个孤儿 .py

### 第二阶段：结构化（第 2-4 周）

```
P0-10 AppContext 拆分（Builder 模式）         🟡 架构
P0-11 AAM 执行引擎统一                       🟡 架构
P0-12 跨层依赖修复 + ruff 边界规则激活        🟡 架构
P1-4  云同步游标时间单位统一                  🔴 数据
P1-5  ChangeTracker 持久化                    🔴 数据
P1-6  JSON 存储性能优化（索引/分片/SQLite）    🔴 性能
P1-7  mark_dirty 覆盖冲突状态修复              🔴 数据
P1-8  同步冲突解决（vector clock / CRDT）      🔴 数据
P1-9  chat_memory 巨型函数重构                🟡 稳定性
P1-10 _agent_cache TTL 过期机制               🟡 稳定性
P1-11 EventBridge 并发广播                    🟡 稳定性
P1-12 MemoryScheduler 缓存修复                🟡 稳定性
P1-21 app_context.py 测试补充                 🟡 测试
P1-22 pytest 配置统一                         🟡 测试
```

+ **automation_hub:** 提取核心到 `core/tool_registry/` → 放 DEPRECATED.md

### 第三阶段：补齐核心（第 5-8 周）

```
P1-1  手机端 token 加密存储                   🔴 安全
P1-2  WS token 加密传输                       🔴 安全
P1-3  transfer token 持久化重试               🟡 稳定性
P1-13 base.py last_error 类型统一             🟡 稳定性
P1-14 settings.set 回滚机制                   🟡 稳定性
P1-15 路由逻辑统一 → PrivacyPipeline          🟡 质量
P1-16 人格注入 persona/ 子包创建              🟡 质量
P1-17 MemoryManager 删除/精简                 🟡 质量
P1-18 手机端 Tab keep-alive                   🟡 体验
P1-19 contentSync 时间戳/去重修复             🟡 数据
P1-20 29 空 catch 块补全错误处理              🟡 质量
```

+ **domain/models 补齐** → 每实体加入字段校验、隐私级别、序列化方法
+ **行为日志收集器** → `services/behavior_log/` 8 模块
+ **冻结 SyncEnvelope JSON Schema** → `sync/schema.py`
+ 覆盖率目标 25%

### 第四阶段：债务清偿（第 9-16 周）

```
P2-1~4    安全/隐私正则修复
P2-5      类型安全 Protocol/ABC 接口
P2-6~8    编码修复 + 孤儿代码清理
P2-9~13   前端硬编码 URL/心跳/i18n/无障碍
P2-14~18  性能（WS轮询/启动懒加载/缓存/SQLite）
P2-19~22  代码质量（情绪分类/相似度/弃用API）
P2-23~25  CI/CD（接入 5 个 script + bandit + coverage）
P2-26     memory/context.py Builder 模式
```

+ **RAG 管道 Phase 0-1**
+ **记忆提取器 + 遗忘曲线**
+ **Pydantic v1→v2 + SQLAlchemy 2.0**
+ 覆盖率目标 35%
+ 彻底删除 `automation_hub/` 目录

### 长期（第 4-6 月）

- 记忆叙事引擎 (`memory/narrative/` 5 模块)
- RAG Phase 2-3 (混合搜索 + 重排序 + PDF 摄入)
- 文件同步 (Mode A/B) + WebSocket 实时推送
- 设备配对 QR 码
- SQLite → PostgreSQL
- PyInstaller/Nuitka 打包
- 插件沙箱 Phase 2 (子进程隔离)
- CRDT 同步冲突解决
- 覆盖率目标 50%

---

> **文档版本:** v1.0 (三份审查统一合并版)
> **来源:** 架构审查 Part 1 + Part 2 + 代码审查报告
> **下一步:** 按第一阶段立即执行，每阶段结束后更新本文档状态
