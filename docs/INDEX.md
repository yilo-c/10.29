<!-- 模块说明：仓库级文档真源索引。唯一导航入口（三态标记 + 合并路线图）。
     三态约定：✅ 现行（照此维护）/ 🔴 过期（参考用或待归档）/ 📌 待写（缺口） -->

# 思忆集 文档真源索引（INDEX）

> 本文档是**唯一导航入口**。进仓库看文档，先看这里。
>
> 状态标记（三态真源机制）：
> - ✅ **现行** — 内容与代码一致，按此维护
> - 🔴 **过期** — 内容已与代码脱节，仅作参考；要么修正为现行，要么 `mv` 到 `docs/archive/`
> - 📌 **待写** — 尚未存在的文档缺口，是当前最需要补的东西
>
> 旧导航入口 [document-map.md](document-map.md) 已降级为历史模块地图（其内部状态标记口径已被本文档取代）。

## 使用流程

1. **先查本文档**：找目标文档的状态标记，不要直接改 🔴 文档。
2. **改文档前**：判断该 🔴 文档是「修正为现行」还是「归档」。归档一律 `mv docs/xxx.md docs/archive/`，不要删除。
3. **新建文档**：任何新 `docs/*.md` 必须先在本文档登记一条（标题 / 目的 / 状态），否则视为孤儿文档。
4. **roadmap 唯一化**：当前执行计划只有一个真源（见[三、路线图](#三路线图--执行计划)），不要在多个文件重复维护同一事项。
5. **改完代码顺手更新**：架构/接口类文档随代码变更同步修订，避免二次漂移。

---

## 一、入口必读（✅ 现行）

按顺序读：

1. **[AGENTS.md](../AGENTS.md)** — 项目总览、最终架构、技术栈、启动方式、环境变量、测试基线、文档索引指引；内含「WebSocket JSON-RPC 协议」章节，即 **RPC 方法表（源真值，其他文档不复述）**
2. **[architecture-overview.md](architecture-overview.md)** — 现行架构真源（2026-08-09 重写，取代旧 PyQt6 版）
3. **[三、路线图](#三路线图--执行计划)** — 当前执行计划与废弃口径
4. **README.md（仓库根）** — 仓库总览与待办入口

## 二、现行文档（✅）按场景路由

### 我要修 bug / 加功能
- 后端 RPC：`AGENTS.md` / `CLAUDE.md` 方法表 → `思忆集app/backend/handlers/`
- 桌面 UI：`desk-notes/CLAUDE.md`；手机 UI：`思忆集手机端app/CLAUDE.md`
- 安全相关：[security-model-overview.md](security-model-overview.md) + [privacy-data-boundary.md](privacy-data-boundary.md)
- 开发环境：[DEVELOPMENT.md](DEVELOPMENT.md)

### 我要做架构决策
| 文档 | 说明 |
|---|---|
| [automation-hub-disposition.md](automation-hub-disposition.md) | ✅ **现行真源**：automation_hub 处置方案（已边缘化，取代"唯一后端底座"旧说法） |
| [companion-agent-design.md](companion-agent-design.md) | 项目设计根本（小问人格 + 三层） |
| [app-platform-boundary-design.md](app-platform-boundary-design.md) | 客户端 / 本地自治层 / 后端边界 |
| [architecture-execution-engines.md](architecture-execution-engines.md) | ✅ **现行纠正**：执行相关模块真实分层（executor/engine/orchestrator/shim）+ hub 裁剪的隐藏 worker 依赖，避免把活跃模块当死代码 |
| [hub-worker-decoupling.md](hub-worker-decoupling.md) | ✅ **现行**：阶段二 W2 收口——API 与 worker 软解耦（延迟导入桥接），解耦后可安全裁 deploy/intel/repos |
| [assistant-organization-map.md](assistant-organization-map.md) | ✅ **现行**：W4 杂物箱收口地图（实测 77 顶层模块 / 69 零内部耦合 / 活跃文件禁区），分批迁移计划 |
| [tech-stack.md](tech-stack.md) | 技术栈基线 |
| [next-phase-product-boundary.md](next-phase-product-boundary.md) | 当前阶段做什么 / 不做什么 |
| [pluginization-boundary-2026-08-22.md](pluginization-boundary-2026-08-22.md) | 插件化边界清单（T4.2 交付物） |
| [plugin-sdk-spec.md](plugin-sdk-spec.md) | 插件 SDK 规范（含"不开放无边界自治"约束） |
| [whitelist-core-events.md](whitelist-core-events.md) | 核心事件白名单 |
| [adr/](adr/) | ADR 架构决策记录 |

### DSH kernel 系列（✅ 现行，2026-08 新增主线）
| 文档 | 说明 |
|---|---|
| [kernel-layering-design-2026-08-15.md](kernel-layering-design-2026-08-15.md) | DSH kernel 分层设计 |
| [kernel-contract.md](kernel-contract.md) | kernel 对外契约 |
| [kernel-execution-guide.md](kernel-execution-guide.md) | kernel 执行指南 |
| [kernel-migration-plan.md](kernel-migration-plan.md) | 迁移计划 |
| [kernel-verification-2026-08-20.md](kernel-verification-2026-08-20.md) | 验证报告 |
| [kernel-journal.md](kernel-journal.md) | 演进日志（活文档） |
| [dsh-integration-report-2026-08-15.md](dsh-integration-report-2026-08-15.md) | DSH 集成报告 |
| [agent改进方案-借鉴ClaudeCode与DSH.md](agent改进方案-借鉴ClaudeCode与DSH.md) | Agent 改进方案（A/B 两条主线） |

### 我要查具体子系统
| 子系统 | 文档 |
|---|---|
| AI Provider 配置 | [ai-provider-reference.md](ai-provider-reference.md) |
| 网络扩展安全 | [ai-networked-extension-rules.md](ai-networked-extension-rules.md) + [ai-networked-extension-security-design.md](ai-networked-extension-security-design.md) |
| API 参考（automation_hub） | [api-reference.md](api-reference.md) |
| 知识体系 | [personal-knowledge-base-architecture.md](personal-knowledge-base-architecture.md) + [knowledge-base-rag-design.md](knowledge-base-rag-design.md) + [knowledge-graph-enhancement.md](knowledge-graph-enhancement.md) + [project-knowledge-graph.md](project-knowledge-graph.md) |
| 记忆系统 | [memory-system-design.md](memory-system-design.md) |
| 跨设备同步 | [sync-protocol-design.md](sync-protocol-design.md) + [cross-device-sync-spec.md](cross-device-sync-spec.md)（现行真源见 automation-hub-disposition.md） |
| 浏览器扩展 | [browser-extension-floating-assistant-status.md](browser-extension-floating-assistant-status.md) + [browser-extension-manual-regression-checklist.md](browser-extension-manual-regression-checklist.md) |
| 桌面 Web 桥接 | [desktop-web-bridge-spec.md](desktop-web-bridge-spec.md) |
| 词汇功能 | [vocabulary-feature-upgrade.md](vocabulary-feature-upgrade.md) |
| 视觉/UI 规范 | [visual-spec.md](visual-spec.md) |
| 离线/会话续接 ADR | [offline-fallback-and-session-continuation-adr.md](offline-fallback-and-session-continuation-adr.md) |
| 稳定性 | [stability-implementation-guide.md](stability-implementation-guide.md) |
| 助手执行框架 | [assistant-execution-framework.md](assistant-execution-framework.md) + [butler任务生命周期设计.md](butler任务生命周期设计.md) |
| 部署 | [cloud-deployment-guide.md](cloud-deployment-guide.md) |
| 功能健康 | [feature-health-checklist.md](feature-health-checklist.md) |
| 源码重建应急手册 | [PYC_REBUILD_RUNBOOK.md](PYC_REBUILD_RUNBOOK.md)（✅ 已完工，保留作事故手册） |
| 微信导出 | [wechat-export-guide.md](wechat-export-guide.md) |

### 工程 / 治理（✅ 现行）
- [DEVELOPMENT.md](DEVELOPMENT.md) — 开发环境与工作流
- [verified-facts.md](verified-facts.md) — **已核验事实登记册**（V2.1 批次0；防审计误报，每条带失效条件）
- [backup-system-boundary.md](backup-system-boundary.md) — **备份系统边界决策**（V2.1 批次0；语义备份 vs 快照角色划分）
- [security/secret-management.md](security/secret-management.md) — 密钥管理
- [security/sync-key-rotation-runbook.md](security/sync-key-rotation-runbook.md) — 同步口令轮换手册（V3-04）
- [release/](release/) — 发布流程（模板 / 冻结清单 / 回滚计划）
- [mypy-debt-paydown-plan-2026-08-22.md](mypy-debt-paydown-plan-2026-08-22.md) — mypy 债务清偿计划
- [test-observation-plan.md](test-observation-plan.md) — 集成/冒烟测试观察计划（M5 观察期）
- [inventory.md](inventory.md) - **自动生成产物，勿手改**：RPC 方法 + SIYI_* 变量权威清单（scripts/gen_inventory.py 生成，CI drift check 守护，debt-v2 V1-03/M3）
- [debt-sprint-execution-plan.md](debt-sprint-execution-plan.md) — 债务清零·补设计·执行清单 v2.0（2026-08-23 重写；v1 已废，判定表内置 §0；与 gstack 计划互补）

## 三、路线图 & 执行计划

### ✅ 现行真源（唯一，照此维护）
| 文档 | 说明 |
|---|---|
| project-status-and-execution-plan-2026-08-22.md（原 `deliverables/gstack/`，已随 2026-10-29 仓库快照清理移除） | **当前执行计划唯一真源**（v1.1）：八维审查 23 项发现 + 16 条行动项 B1–B16 + 阶段四任务表（T1–T4），经深度验证修订 |
| [project-roadmap.md](project-roadmap.md) | 当前主路线图（废弃口径）。⚠️ 内含过时段落（complete-architecture-design.md 死链、PyQt6 阶段 6a），修订方向以执行计划为准 |
| [unified-execution-roadmap-2026-06-22.md](unified-execution-roadmap-2026-06-22.md) | 统一执行路线图（三份审查合并：12 P0 + 22 P1 + 26 P2 + 12 P3 + 子系统裁决 + 测试路线图 + 四阶段计划） |

### 🔴 历史路线图（原归档于 `docs/archive/`，该目录已随 2026-10-29 快照清理移除）
> 以下文档于 2026-08-23 按本文档 🔴 标记归档，内容已被当前执行计划 / 现行文档取代：
> `roadmap-next.md`（Phase A–D 已覆盖）、`todo-unimplemented-features.md`（剩余项并入执行计划）、
> `deep-development-roadmap.md`（深度功能规划，已转码恢复）、`cloud-sync-roadmap.md`（由 AES-GCM v4 实现取代）、
> `landing-plan-2026-08-09.md`、`frontend-backend-alignment-plan-2026-08-09.md`、`frontend-status-report-2026-08-09.md`、
> `frontend-followup-roadmap-2026-08-09.md`、`feature-alignment-plan.md`、`frontend-production-alignment-plan.md`

## 四、审查 / 评估报告

### ✅ 现行（修复执行前有效，收口后归档）
| 文档 | 说明 |
|---|---|
| [architecture-audit-2026-09-23.md](architecture-audit-2026-09-23.md) | **全栈架构深度审计**（5 线并行只读调查）：3 Critical + 7 High + 9 Medium 全量 finding（文件:行号证据）+ 六波整合执行次序 + 勿误伤负面清单 + 待证清单；修复进度在其 §10 勾选 |

### 🔴 历史报告（原归档于 `docs/archive/`，该目录已随 2026-10-29 快照清理移除，仅作历史参考）
> 以下报告于 2026-08-23 按本文档 🔴 标记归档：
> `architecture-audit-2026-06-22.md` / `architecture-audit-2026-06-22-part2.md`（已被 unified-execution-roadmap 合并）、
> `audit-2026-06-18.md`、`code-review-fix-plan-2026-06-18.md`、`remediation-plan-2026-08-07.md`、
> `工作台模块体检报告-2026-08-20.md`、`p1-6-local-storage-performance-evaluation.md`、
> `full-integration-test-plan-2026-08-17.md`、`PROJECT_HANDBOOK.md`（早期全景参考）

## 五、观测日志与跟进（活文档）

| 文档 | 说明 |
|---|---|
| [obs-s1-log.md](obs-s1-log.md) | S1 观察日志（连续 7 天 gateway-fatal=0 退役条件） |
| [obs-gray-isolation-log.md](obs-gray-isolation-log.md) | 子进程隔离灰度观察日志 |
| [followups/](followups/) | 各 C 轮跟进记录 |
| [document-map.md](document-map.md) | 历史模块地图（导航职责移交本文档，重大变更记录仍有参考价值） |

## 六、待写（📌 缺口）

| 待写文档 | 目的 |
|---|---|
| `docs/architecture-overview.md` 的云端架构补充 | 补充 cloudflare/worker AES-GCM v4 同步的具体结构（当前架构图仅含概览） |
| `docs/knowledge/` 知识库目录规范 | 知识系统 MD 单真源（`knowledge/YYYY/YYYY-MM/YYYY-MM-DD_<类别>.md`）的操作指引落地为独立文档 |

## 七、归档

`docs/archive/` — 所有过期文档原归档于此（2026-08-23 已归档 19 份：10 份历史路线图 + 9 份审查/评估报告）。该目录已随 2026-10-29 仓库快照清理移除，仅保留此说明。
> 新文档过期后 `mv` 到这里，不要删除。

## 八、维护规则

1. **唯一导航入口**：新增阅读入口一律先进 `docs/INDEX.md`，不要绕过。
2. **三态必标**：本文档中每条文档必须带有 ✅ / 🔴 / 📌 之一；状态变更时同步更新。
3. **roadmap 唯一化**：只在一个现行文档维护同一事项；旧 roadmap 标 🔴 或归档。
4. **改代码必同步**：架构 / RPC / 子系统文档随代码变更同步修订；接口契约以 `CLAUDE.md` 方法表为源真值，不重复维护。
5. **过期即归档**：文档过期直接 `mv docs/xxx.md docs/archive/`，保留历史、不占导航位。
6. **新文档必登记**：任何新 `docs/*.md` 未在本文档登记即视为孤儿文档。
