# 插件化边界清单

> 本文是阶段四（插件化深化）T4.2 的交付物：明确"该 plugin 化"与"不该动"的边界清单。
> 决策依据为 T3.6 能力路由遥测（`plugins/capability_router.py` 的
> `plugin_hits` / `fallback_hits` / `plugin_errors`，经 `system.health` RPC 的
> `capability_routing` 子系统透出，`services/system_health.py` 第 17 个子系统）。
> 依据：《项目综合状态报告与执行计划》阶段四 T4.2。

---

## 1. 核心边界原则

**铁律（不可逾越）**：T4.1 子进程隔离灰度转正、且遥测确认插件零回退之前，
**不删除任何 fallback、不动有状态能力、不触碰 LLM 核心**。插件化只做"套壳接线"
（新增 plugin 优先分支），原生实现始终保留为兜底。

判定标准二分：

| 维度 | 该 plugin 化 ✅ | 不该动 ❌ |
|------|----------------|-----------|
| 状态性 | **纯函数**（无外部状态、幂等、确定性） | 有状态（读写本地文件/DB/缓存/会话） |
| I/O | 无 I/O 或仅只读、无副作用 | 写操作、网络请求、进程/文件副作用 |
| 依赖面 | 与 AI 核心无关，独立算法 | 依赖 LLM、AI 网关、模型推理 |
| 安全敏感度 | 低风险（只读分类、纯计算） | 高权限（文件写、shell、网络） |
| 调用频率与实时性 | 可接受子进程/沙箱开销 | 超高频热路径或超低延迟硬要求 |

---

## 2. 当前已 plugin 化清单（现状基准）

截至本文，插件归属 `plugins/installed/` 下，全部为 `tool` 类型、网页上经
`capability_router` 以"plugin 优先、原生兜底"接入。

| capability_id | 插件 | 动作 | 原生兜底 | 判定 |
|---------------|------|------|----------|------|
| `info.classify` | infoclassifier | `classify_item` | `services.info_categorizer.categorize_item` | ✅ 该 plugin 化 |
| `info.has_cjk` | infoclassifier | `has_cjk` | `services.info_constants.has_cjk` | ✅ 该 plugin 化 |
| `info.section_keywords` | infoclassifier | `section_keywords` | `services.info_collector_service.get_section_keywords` | ✅ 该 plugin 化 |
| `info.dedup` | dedup | `dedup_items` | `services.info_collector_service.dedup_items` | ✅ 该 plugin 化 |
| `info.title_similarity` | dedup | `title_similarity` | `services.info_collector_service.title_similarity` | ✅ 该 plugin 化 |
| `text.stats` | textstats | 文本统计 | 无（独立新能力） | ✅ 该 plugin 化 |

> 这些能力共同特征：**纯函数、无状态、只读计算**，符合第一节"该 plugin 化"全部
> 判据。接线点：`assistant/capability/_plugin_tools.py` 的 `register_plugin_tools`/
> `_invoker`/`_make_plugin_tool_handler`。

---

## 3. "不该动"清单（保留原生实现，禁止 plugin 化或需特殊豁免）

以下类别**默认禁止** plugin 化，除非走豁免流程（需遥测数据支撑 + 明确验收）：

### 3.1 有状态 / I/O
- `notes.*` 笔记 CRUD 与存储（读写 SQLite/本地文件）
- `tasks.*` 待办持久化
- `files.*` 文件读写
- `memory` 记忆读写、索引、向量检索（ChromaDB）
- 任何 `create_subprocess_exec` / `os.remove` / `rename` / 网络请求

### 3.2 LLM 核心
- `chat.*` 对话、`ai.*` 模型调用、`translate.*` 翻译
- AI 网关、fallback 链、熔断、配额管理（`assistant/` 下的 AI 编排）
- `react_executor` 工具链编排（依赖 LLM 决策）

### 3.3 跨能力编排与生命周期
- `copilot.execute` / 自主管家（proactive / autonomy）——属有状态编排
- 插件系统自身的注册 / 生命周期 / 权限管理（防递归 / 自举）

### 3.4 安全敏感
- 白名单 / 权限校验 / 审计写入（`audit_rollback` / `audit_writer`）
- 命令执行边界（`_tool_management`、`_validate_llm_tool_spec`）

---

## 4. 数据驱动的决策流程（引用遥测）

对任一候选能力，先回答三个量化问题（数据取自 `capability_routing` 子系统）：

1. **plugin 命中率** `plugin_hits / (plugin_hits + fallback_hits)`：
   期望接近 100%（默认应走 plugin）。若长期显著 <100%，说明套壳接线未生效或
   plugin 频繁报错 → 排查而非继续扩大。
2. **fallback 命中率** `fallback_hits`：观测"plugin 优先没吃到"的比例，反推接线正确性。
3. **故障回退次数** `plugin_errors`：评估插件故障对业务的实际影响面。

**接纳标准**（新能力申请 plugin 化时）：
- 命中该能力 getAll 判据（纯函数/只读/无状态/低风险）；
- 灰度运行 ≥1 周，`plugin_errors` 稳定为 0，`plugin_hits` 接近 100%；
- 保持原生兜底不动，评审时引用上述三项遥测数值。

**回流标准**（已 plugin 化能力出现下列任一情况 → 回退到仅原生）：
- `plugin_errors` 持续 >0 且修复成本高于收益；
- 命中率显著下降且无法定位（套壳未生效）。

---

## 5. 豁免与例外

- 特例裁决仅可由项目负责人作出，且**必须**在 docs/ 记录理由与遥测依据。
- 当前无豁免案例；`文本统计`（textstats）为新增能力，暂无原生实现，但仍符合
  纯函数判据，故归类"该 plugin 化"（其兜底可在未来需要时补充）。

---

## 关联文档

- [插件 SDK 规范](plugin-sdk-spec.md)
- [自动化底座边界](automation-hub-disposition.md)
- 《项目综合状态报告与执行计划》阶段四 T4.1 / T4.2
- 命令执行边界审计：`docs/command-execution-boundary-audit-2026-08-22.md`

> 本文由软件工坊 AI 协作起草；边界裁定与豁免由项目负责人最终拍板。