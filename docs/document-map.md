<!-- 模块说明：仓库级文档地图。文档已于 2026-06-18 大幅精简，过期/冗余文档已归档至 docs/archive/。 -->

# 文档地图（2026-06-18 精简版）

> 73 份文档已精简至 37 份。归档清单见 [docs/archive/](archive/)。

## 入口三件套（必读，按顺序）

1. **[/AGENTS.md](../AGENTS.md)** — 项目总览、技术栈、357+ JSON-RPC 方法表、启动方式
2. **[architecture-overview.md](architecture-overview.md)** — 桌面端 + 手机端 + 后端三方架构
3. **[project-roadmap.md](project-roadmap.md)** — 当前阶段目标和废弃口径

## 按场景路由

### 我要修 bug / 加功能
- 后端 RPC：CLAUDE.md 的方法表 → 对应 `思忆集app/backend/handlers/`
- 桌面 UI：`desk-notes/CLAUDE.md`
- 手机 UI：`思忆集手机端app/CLAUDE.md`
- 安全相关：[security-model-overview.md](security-model-overview.md) + [privacy-data-boundary.md](privacy-data-boundary.md)

### 我要做架构决策
- **[automation-hub-disposition.md](automation-hub-disposition.md)** — 🔴 **现行真源（2026-08-17）**：automation-hub 处置方案，声明旧文档定位错误，取代"唯一后端底座 / 待收敛原型"的旧说法
- [companion-agent-design.md](companion-agent-design.md) — 项目设计根本（小问人格 + 三层）
- [architecture-audit-2026-06-22.md](archive/architecture-audit-2026-06-22.md) — 🔴 全架构审查报告（第一部分：架构总览 + P0-P2 + 子系统裁决）
- [architecture-audit-2026-06-22-part2.md](archive/architecture-audit-2026-06-22-part2.md) — 🔴 全架构审查报告（第二部分：前端/手机端/RPC/数据库/错误处理/CI/CD/测试/性能）
- [unified-execution-roadmap-2026-06-22.md](unified-execution-roadmap-2026-06-22.md) — ✅ 统一执行路线图（三份审查合并：12 P0 + 22 P1 + 26 P2 + 12 P3 + 子系统裁决 + 测试路线图 + 四阶段计划）
- [next-phase-product-boundary.md](next-phase-product-boundary.md) — 当前阶段做什么不做什么
- [app-platform-boundary-design.md](app-platform-boundary-design.md) — 客户端 / 本地自治层 / 后端边界
- [tech-stack.md](tech-stack.md) — 技术栈基线

### 我要查具体子系统
| 子系统 | 文档 |
|---|---|
| AI Provider 配置 | [ai-provider-reference.md](ai-provider-reference.md) |
| 网络扩展安全 | [ai-networked-extension-rules.md](ai-networked-extension-rules.md) + [ai-networked-extension-security-design.md](ai-networked-extension-security-design.md) |
| API 总参考 | [api-reference.md](api-reference.md) |
| 知识图谱 | [knowledge-base-rag-design.md](knowledge-base-rag-design.md) + [knowledge-graph-enhancement.md](knowledge-graph-enhancement.md) |
| 记忆系统 | [memory-system-design.md](memory-system-design.md) |
| 跨设备同步 | [automation-hub-disposition.md](automation-hub-disposition.md)（现行真源）→ [sync-protocol-design.md](sync-protocol-design.md) + [cross-device-sync-spec.md](cross-device-sync-spec.md) + [cloud-sync-roadmap.md](archive/cloud-sync-roadmap.md) |
| 浏览器扩展 | [browser-extension-floating-assistant-status.md](browser-extension-floating-assistant-status.md) + [browser-extension-manual-regression-checklist.md](browser-extension-manual-regression-checklist.md) |
| 桌面 Web 桥接 | [desktop-web-bridge-spec.md](desktop-web-bridge-spec.md) |
| 词汇功能 | [vocabulary-feature-upgrade.md](vocabulary-feature-upgrade.md) |
| 视觉/UI 规范 | [visual-spec.md](visual-spec.md) |
| 插件 SDK | [plugin-sdk-spec.md](plugin-sdk-spec.md) |
| 离线/会话续接 ADR | [offline-fallback-and-session-continuation-adr.md](offline-fallback-and-session-continuation-adr.md) |
| 稳定性 | [stability-implementation-guide.md](stability-implementation-guide.md) |
| 助手执行框架 | [assistant-execution-framework.md](assistant-execution-framework.md) |
| 部署 | [cloud-deployment-guide.md](cloud-deployment-guide.md) |
| 落地执行计划 | [landing-plan-2026-08-09.md](archive/landing-plan-2026-08-09.md) — 阶段 1/2 已执行完成（P0×4 + P1-1/P1-2/P1-5/P1-6） |
| 前后端对齐规划 | [frontend-backend-alignment-plan-2026-08-09.md](archive/frontend-backend-alignment-plan-2026-08-09.md) — 后端新能力前端接入审计 + 阶段 A-D 规划（配套 [frontend-status-report-2026-08-09.md](archive/frontend-status-report-2026-08-09.md)） |
| 源码丢失重建方法论 | [PYC_REBUILD_RUNBOOK.md](PYC_REBUILD_RUNBOOK.md) — ✅ 已完工（82 模块全部重建），保留作事故应急手册 |

### 历史路线图（仅作参考）
- [project-roadmap.md](project-roadmap.md) — 当前主路线图
- [roadmap-next.md](archive/roadmap-next.md) — 下一阶段候选项
- [deep-development-roadmap.md](archive/deep-development-roadmap.md) — 深度功能规划
- [todo-unimplemented-features.md](archive/todo-unimplemented-features.md) — 待办功能清单
- [feature-health-checklist.md](feature-health-checklist.md) — 功能健康度检查表

### ADR（架构决策记录）
- [adr/](adr/) — 重大决策的 ADR
- [whitelist-core-events.md](whitelist-core-events.md) — 核心事件白名单

## 重大变更记录

### 2026-08-09：前后端对齐规划阶段 D 完成（扩展功能，证据驱动）
- 起因：用户与助手的真实失败链对话（查桌面：文件越界→守卫拦截→能力名错→截图挂起→WebSocket closed）
- D2 运行观测面板：TracePanel（run 列表+步骤时间线+统计）挂 AgentMonitor 观测 tab；toolErrorHints 将 broker/guard 结构化 reason 映射为原因+建议（a372ba46）
- D3 资讯管理：InfoLibraryView（条目已读/收藏/存笔记 + 板块增删，默认板块保护）挂 InfoModule 资讯库 tab（4547375a）
- D1 命令面板 RPC 动作：QuickSearchModal 动作区（立即同步/通知全部已读），B2 地基（032e6149）
- D4 聊天流韧性：错误卡片（友好化+重试上一条）+ 会话导出 Markdown（1c55ad99）
- 见 [frontend-backend-alignment-plan-2026-08-09.md](archive/frontend-backend-alignment-plan-2026-08-09.md)

### 2026-08-09：前后端对齐规划阶段 C 完成（体验深化）
- C3 datetime 工具层（now 注入防时间炸弹）：修复聊天跨天消息缺日期（昨天 HH:mm / MM-DD HH:mm）；ChatMessage/ChatView 迁移
- C4 硬编码色清理：新增 overlay/scrim/blue/purple/green 语义令牌（4 主题）；InfoFeedView 12 处 + NoteEditor 3 处迁移
- C1 待办右栏详情面板（TodoDetailPanel：优先级/截止日期/提醒/子任务/元信息，三栏范式）
- C5 Dashboard 资讯卡/收件箱卡骨架补齐（替代裸 spinner）
- C2 虚拟滚动暂缓（需新依赖且无数据量证据，证据优先原则）
- 见 [frontend-backend-alignment-plan-2026-08-09.md](archive/frontend-backend-alignment-plan-2026-08-09.md)

### 2026-08-09：前后端对齐规划阶段 B 完成（架构债清偿）
- B1 backgroundJobs 通用层：独立 store + runTrackedJob 包装 + rehydrateBackgroundJobs（system.active_tasks 恢复，幂等）；URL 学习流示范迁移；App.tsx 连接建立时自动恢复
- B2 统一快捷键注册中心：shortcutRegistry（冲突检测/引用计数监听/可发现列表）；App+MainWindow 全部迁入；ShortcutsModal 改注册中心驱动（修复展示与实际不符）；顺带修复 MainWindow Rules of Hooks 违规
- B3 事件订阅层转正：backendEventBus 统一分发注册表；CloudSyncPanel/useSandbox/StatusBar 三处散落订阅迁移，主窗口零散落
- 见 [frontend-backend-alignment-plan-2026-08-09.md](archive/frontend-backend-alignment-plan-2026-08-09.md)

### 2026-08-09：前后端对齐规划阶段 A 完成（新能力前端可见化）
- A1 事件接入层：backendEventsStore 收编六类新事件，getWsClient 自动安装；shared 测试纳入管线（43→54 文件）
- A2 同步冲突裁决 UI（SyncConflictResolver）；A3 断点续跑横幅（ResumableTasksBanner）；A4 批量审批+超时提示（SandboxTimeoutHints）；A5 晨报卡（MorningBriefCard）
- 构建修复：workbox globIgnores 排除桌宠参考图（2.5MB 超 PWA 预缓存上限）；web_dist 镜像清理孤儿产物
- 见 [frontend-backend-alignment-plan-2026-08-09.md](archive/frontend-backend-alignment-plan-2026-08-09.md)

### 2026-08-09：前后端对齐审计与规划落盘
- 审计结论：后端阶段 1–4 新增 RPC（trace/续跑/冲突裁决/批量审批/自维护提案）与 6 个新事件前端全部零接入；info.items/info.sections 资讯管理能力未接入
- 规划：阶段 A（新能力可见化）→ B（架构债：backgroundJobs/快捷键/事件层）→ C（体验深化）→ D（扩展）；A1 事件接入层为公共前置，A2 冲突裁决因数据安全建议优先
- 见 [frontend-backend-alignment-plan-2026-08-09.md](archive/frontend-backend-alignment-plan-2026-08-09.md)

### 2026-08-09：落地计划阶段 4 批次（P1-3/P1-4/P2-4/P2-7/P2-8）
- P1-3 多轨引用体检："五轨"实为两产品面（聊天面 butler+smolagents、自治面 autonomy）；生产零引用的 agent_loop 归档至 archive/dead-exec-paths-20260809/；体检报告 docs/archive/exec-path-inventory-2026-08-09.md
- P1-4 AppContext 领域子门面（chat/memory/sync/automation，薄视图+惰性属性）；P2-4 SubAgent 共享黑板；P2-7 监督式自优化（巡检→提案→审批→白名单执行，maintenance.proposals.* RPC）；P2-8 晨报卡（日报默认启用 07:00 + proactive.triggered 双通道）
- P2-5（chain DAG 并行）暂缓，待并行任务真实需求证据；RPC 方法数 334 → 337

### 2026-08-09：落地计划阶段 1/2 执行 + 架构文档重写
- 阶段 1（稳定性兜底）：僵死锁接管（instance_lock）、EventBridge 每客户端队列、cron 连续失败告警+记忆巡检、同步冲突可见可恢复
- 阶段 2（复杂任务闭环）：TraceRecorder 统一观测 + 任务契约、trace/续跑 RPC、启动 task.resumable 扫描
- 阶段 3 部分：审批超时策略+批量审批（P1-5）；[architecture-overview.md](architecture-overview.md) 重写为现行架构真源（旧 PyQt6 版淘汰，原指向的 complete-architecture-design.md 为死链）
- RPC 方法数 322 → 334；测试基线见 AGENTS.md

### 2026-08-08：源码丢失重建完工 + 项目整理 + 测试基线
- 82 个丢失源码的模块全部按字节码反汇编重建完成（零裸 pyc），方法论见 [PYC_REBUILD_RUNBOOK.md](PYC_REBUILD_RUNBOOK.md)
- 守卫脚本 `scripts/check_no_sourceless_pyc.py` 接入 CI；conftest 支持后端不可达时自动跳过 WS 集成测试
- 项目整理：根目录临时文件清理、web_dist 与最新构建对齐（818→105 产物）
- 测试基线（见 AGENTS.md「测试基线」章节）：根级 pytest 3450 过零失败、automation_hub 179/179、前端 43 文件全过、WS 冒烟 16/16、覆盖率 20.02%

### 2026-06-22：全架构审查（合并版 Part 1 + 深度领域 Part 2）
- Part 1: [architecture-audit-2026-06-22.md](archive/architecture-audit-2026-06-22.md) — 7 P0 + 9 P1 + 12 P2 + 12 子项目裁决
- Part 2: [architecture-audit-2026-06-22-part2.md](archive/architecture-audit-2026-06-22-part2.md) — 前端/手机端/RPC/数据库/错误处理/CI/测试/性能 8 领域深度审查
- 新增 P0-8~11（沙箱禁用、AccessibilityService 暴露、事件循环阻塞、JSON O(n)）、P1-10~19（token 明文、Tab 状态丢失、29 空 catch、CI 缺安全扫描等）
- 8 领域评分：前端 B- / 手机端 B- / RPC B+ / 存储 C+ / 错误处理 C / CI/CD C / 测试 B- / 性能 C-

### 2026-06-18：文档大整理 + P0 修复 11 项
- 删除根目录临时文件 26 个（`_brief_output*.md`、`_fix_*.py`、`*.log` 等）
- docs/ 从 73 份精简到 37 份；过期 `code-review-2026-06.md`/`audit-2026-06-17.md`/各种 `execution-plan-*.md` 等归档至 `docs/archive/`
- 完成 P0 6 项桌面端 + P0 5 项手机端修复（详见 `docs/archive/audit-2026-06-18.md`）
- 归档 `android/` 至 `archive/android-deprecated-202606/`（manifest 残缺无法启动）

### 2026-06-17：Agent / 双端协同升级
- smolagents 接入；手机原生桥接（SiyiAccessibilityService 等）；新增 18 个 @tool

### 2026-06-15：单词功能升级 (Sprint 1-3B)
- 小问 × 单词联动；FSRS 双写；听写模式

### 2026-06-03：双端修复
- H1 文件上传、H6 粘贴图片、原子写入、ChatModule 草稿

## 归档
[docs/archive/](archive/) — 所有过期文档（约 70 份），不再维护。

## 编辑规则
1. 新内容先找现有专题，避免新建平行文档
2. 加日期戳的执行计划 / 审查报告写完即归档
3. CLAUDE.md 的 RPC 方法表是源真值，不要在其他文档复述
4. 文档过期请直接 `mv` 到 `archive/`，不要删
