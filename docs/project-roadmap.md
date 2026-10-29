<!-- 模块说明：仓库级项目路线图与阶段目标。 -->

# 思忆集仓库项目路线图

本文档用于把仓库当前状态、下一阶段目标和完成定义收敛为一份长期维护的正式路线图。

> **2026-05-20 架构更新：** 桌面端已完成”合并统一”Phase 1-3：
> - **Phase 1** ✅ AI 执行路径统一 — 所有前端 AI 调用走后端 RPC
> - **Phase 2** ✅ 数据存储统一 — notes/tasks 前后端双向同步
> - **Phase 3** ✅ PyQt6 清理 — 默认启动路径改为无头后端 + Electron
> - `method_handlers.py` → `backend/handlers/` (9 领域模块)
> - `useStore.ts` (1087 行) → 4 个 Zustand slice
>
> 后续路线图见 `docs/archive/todo-unimplemented-features.md`、`docs/archive/deep-development-roadmap.md`；当前执行计划唯一真源为 `deliverables/gstack/project-status-and-execution-plan-2026-08-22.md`。

最高架构裁决以 [architecture-overview.md](architecture-overview.md) 为准。本文档只维护阶段目标、优先级顺序和完成定义，不重新定义”小问”的核心定位。

根目录 [README.md](../README.md) 负责仓库总览、当前待办和文档入口；本文档负责回答：

1. 当前阶段已经完成了什么。
2. 下一阶段真正要关闭的缺口是什么。
3. 后续能力扩展应该按什么顺序推进。

## 适用范围

本文档适用于整个仓库；当前唯一后端真源应用仍是 [automation-hub](../automation-hub)，而产品边界、客户端主线和执行口径以本目录专题文档栈为准。

- [automation-hub](../automation-hub)：主应用与交付对象
- [思忆集app](../思忆集app)：当前桌面增强客户端主线，处于产品收口与结构重构阶段，不作为第二套后端平行演进
- 根级 [docs](.)：跨应用的产品、客户端、治理、监控和路线图文档入口

说明：历史下划线包 `automation_hub` 兼容导入层已移除；后续工作不得再以兼容桥、下划线别名或影子包的形式重新引入。

## 2026-05-20 当前执行状态

当前按 [complete-architecture-design.md](complete-architecture-design.md) 第 13 节实施阶段推进，已完成阶段 0-5、阶段 6、合并统一 Phase 1-3。

### 已完成阶段

| 阶段 | 状态 | 核心输出 |
|------|------|---------|
| 阶段 0：总纲收束与现状复原 | ✅ 完成 | document-map 更新、旧文档冲突标注、项目健康检查、测试收集修复 |
| 阶段 1：核心对象合同冻结 | ✅ 完成 | assistant_task / capability_descriptor / memory_entry / inbox_item / user_preference 等主对象合同对齐与单元测试 |
| 阶段 2：Action Broker 与 Action Guard 强制落地 | ✅ 完成 | ActionBroker.execute() + execute_sync()、风险策略（R1-R4）、Action Guard 确认链、执行回执生成 |
| 阶段 3：Capability Registry 全量接管工具 | ✅ 完成 | CapabilityRegistry + CapabilityRouter 注册体系、动态 MCP 分发、Planner registry-first 读模型、高风险操作确认级别 |
| 阶段 4：模型网关与任务规划闭环 | ✅ 完成 | Local-first routing（简单查询本地处理）、Privacy egress guard（local_only 跳过云端）、ModelInvocationReceipt 全路径接入、CostTracker + BudgetManager 联动、task_lifecycle.execute_and_record() 闭环 |
| 阶段 5：记忆、知识库与私人数据主权 | ✅ 完成 | MEMORY_TYPE_* / PRIVACY_* 常量体系、MemoryCandidate.privacy_level 全链路透传、PrivacyFilter 自动标记、index_memory() 持久化提取结果、revoke_memory() 软删除与审计日志、get_memories_by_type() 类型化召回 |

### 当前阶段

**阶段 6：桌面端工作台与执行可视化** — ✅ 完成

对应 architecture-design 第 13.7 节，已完成内容：
1. 任务面板 — assistant_task 状态/步骤/风险/确认项
2. 执行面板 — step start/end/blocked/error/need_approval
3. 审批面板 — 批准/拒绝/修改参数/转手动
4. 回执面板 — 执行结果/影响文件/外发数据/回滚方式
5. 记忆面板 — 候选/已确认/冲突记忆和来源
6. 命令面板 — 创建任务或调用 capability
7. 毛玻璃 UI redesign — 亚克力/纯白/纯黑三档背景模式、紧凑间距、半透明面板、桌面渐变

### 当前阶段

**阶段 6a：主窗口 Web UI 迁移** — 进行中

与阶段 7 并行推进。

**背景**：原 Stage 6 完成了桌面工作台的面板（审批/执行/回执/记忆/命令），但主窗口本身仍是 PyQt6 原生。阶段 6a 将主窗口全面迁移至 desk-notes React Web UI，通过 QWebEngineView + file:// 协议嵌入。

**Sub-phase 1：桌面特有功能补全**（高优先级）
- 仪表盘/工作台 DashboardModule → 从 workbench/controller.py 迁移
- 命令面板 CommandPalette → 从 command_palette.py 迁移
- 内联面板框架 SlidePanel → 6 个面板按需加载（审批/回执/执行/建议流/信息雷达/记忆）
- 状态栏 StatusBar → AI 状态/天气/计数/同步
- 全局搜索 → 整合进 CommandPalette

**Sub-phase 2：嵌入式模块迁移**（中优先级）
- NotesModule → 从占位符改为内嵌三栏布局（FolderTree + NotesBrowser + NoteEditor）
- TodoModule → 从占位符改为双栏布局（CategoryPills + TodoList）
- NewsModule → 从"即将支持"改为真实新闻浏览
- NavSidebar → 扩展至全量导航项

**Sub-phase 3：独立对话框迁移**（按需）
- 逐个创建 StandaloneNewsView / StandaloneTranslateView / StandaloneWordBankView / StandaloneURLLearningView / StandaloneMCPView / StandaloneKBView / StandaloneWorkflowsView / StandalonePluginsView

**Sub-phase 4：桌面生态系统**
- 系统托盘 Electron 原生整合
- 全局快捷键
- ProactiveTriggerEngine → React 端 useEffect + 轮询
- 桌宠集成（Nana/Neuro 状态监控）

### 当前阶段

**阶段 7：外部 App 与设备能力适配** — 进行中

对应 architecture-design 第 13.8 节，主要工作：
1. 文件系统能力：个人目录索引、搜索、归档、预写入快照
2. 浏览器能力：当前页面理解、网页保存、链接整理
3. 笔记工具：导入/导出或 API 集成
4. 待办/日历：读取日程、创建草稿、生成提醒
5. 通信入口：消息草稿和上下文组织

后续阶段：阶段 8（双端协作与语义同步）、阶段 9（长期恢复与自托管）

---

## 以下为历史内容（保留用于上下文参考）

## 当前基线

截至 2026-03-11，项目已经完成以下收口：

- API、Worker、审批、审计、tool-runs、proposals、repos、metrics 已形成主线代码骨架
- `release_check.py`、VS Code 任务和 GitHub Actions 已形成固定质量门禁
- Windows 本地、Docker Desktop 本地和本机自部署 Docker 三容器栈均已完成 live 或 worker live 复验
- 本地 Docker Desktop Kubernetes 的 host / docker-executor 联调入口已标准化到 runbook 和任务面板
- 历史下划线包 `automation_hub` 兼容导入层已完成无引用核对并整体移除，Python 导入路径统一收敛到 `automation-hub` 下的模块
- 仓库内已存在 [思忆集app](../思忆集app) 桌面原型，覆盖笔记、个人任务、新闻、翻译、单词本等个人助手场景，但当前仍主要依赖 PyQt + 本地文件存储，尚未完成服务化与多端化抽象

针对两个项目的最新整合判断如下：

- `automation-hub` 继续作为唯一后端底座，承接认证、同步、审计、AI 能力、外部工具执行和跨端统一 API
- `思忆集app` 不直接并入现有 `task` / `run` 模型，而是定位为 PC 桌面客户端的产品原型
- 个人任务、笔记、单词本、新闻收藏等能力后续应新增独立领域模型，不复用当前自动化作业表结构
- 近期客户端路线已从“先 PC，后 Android”调整为“先冻结统一数据合同，再按手机优先、桌面增强推进双端自治”；当前阶段不追求 UI 跨端复用，而优先追求领域模型、同步协议和后端接口复用

桌面端当前新增三条收口子路线，后续细节不再堆在本路线图正文，而分别维护在：

- [desktop-doctor-debug-assets.md](desktop-doctor-debug-assets.md)
- [assistant-execution-framework.md](assistant-execution-framework.md)
- [main-window-decoupling-plan.md](main-window-decoupling-plan.md)

部署线历史判断：真实部署环境放行证据仍未补齐：

- [automation-hub/docs/live-acceptance-2026-03-11-remote.md](../automation-hub/docs/live-acceptance-2026-03-11-remote.md)
- [automation-hub/docs/worker-live-acceptance-2026-03-11-remote.md](../automation-hub/docs/worker-live-acceptance-2026-03-11-remote.md)

在这两项完成前，automation-hub 部署线可以视为“本地和自部署环境已验证”，但还不应视为“真实部署环境已放行”。不过对“小问个人 AI 管家”主线而言，当前最高优先级已调整为本文 2026-05-11 节所述的阶段 0：总纲收束与现状复原。

## 路线图原则

后续推进遵循以下原则：

1. 先关闭放行缺口，再扩展产品能力。
2. 先固化生产运行基线，再扩大执行器和接入端矩阵。
3. Agent 与 Intel 只建立在可审计、可回滚、可审批的执行链路之上。
4. 不再新增平行说明文档；路线图长期维护在本文档，README 只保留摘要和入口。
5. 客户端路线优先冻结统一数据合同，再按“手机优先、桌面增强”推进；当前不为界面跨端牺牲交付速度。
6. `思忆集app` 与 `automation-hub` 的整合以“客户端 + 统一后端”方式推进，而不是继续演进两套平行应用。
7. 个人知识管理域与自动化执行域分开建模；`automation-hub` 现有 `tasks` / `runs` 不直接承接个人待办与笔记。
8. 个人云路线先做“垂直个人数据云”，再扩到“个人内容云”和“个人云工作台”；百度网盘类外部服务只作为备份、归档或迁移辅助通道，不作为核心能力依赖。
9. 产品主线优先回答“如何筛选信息、收束工具、沉淀习惯”，而不是继续堆叠零散功能模块。
10. app 与底座分离：没有后端底座时客户端也必须可独立工作；接入底座后再获得同步、备份、插件分发、跨设备和审计增强能力。
11. `automation-hub` 后续面向企业化扩展时，采用“控制面 + 本地代理/边缘代理”双层架构；后端继续负责策略、审批、审计和编排，本地代理负责本机交互、上下文采集和受控执行。
12. 参考 `nanobot` 的本地常驻形态、provider 抽象、channel 管理和事件总线思路，但不复用其偏个人助手的权限模型；企业场景始终坚持“建议与执行分离、权限与执行分离”。
13. 桌面端的“自升级”只允许三类安全动作：规则更新、配置调整、插件安装；不允许助手直接改核心 Python 文件。
14. 对个人助手新增能力时优先走插件化或工作流扩展，而不是继续把新逻辑堆进主窗口和核心运行时。
15. 设备端可以自治，长期资产语义不能自治；模型入口允许双活，但记忆、任务、收件箱、执行回执和偏好必须保持单合同、单状态机、可同步。
16. 小问是个人 AI 管家和统一执行中枢；Judge 只是复杂任务中的路由器和决策辅助，只在本地 AI 无法胜任、低置信度、模型选择、成本评估或高风险决策时介入。
17. 跨端协作而非隔离：会话可续接、审批可跨端推送、附件可跨端共享、任务可按能力路由。
18. 同步不依赖第三方服务：用户数据直接同步到用户指定的自有目录。
19. 升级必须受控：TechRadar 扫描 → 晨报报告 → 用户确认 → 沙箱测试 → 执行 → 可回滚。不允许 AI 自主修改核心代码。

## 2026-04-20 重规划：手机优先、双端自治、语义单合同

本节优先级高于本文中仍保留的旧客户端排序描述。凡涉及“先 PC、后 Android”“Android 第二客户端”或“桌面主脑”的表述，均以本节为准。
本文中 2026-04-20 之前的客户端排序只作历史背景，不再单独作为当前开发授权或排期依据。

### 新定位

思忆集当前正式重定位为：本地优先、双端深度协作的个人 AI 管家系统。小问是其中的个人 AI 管家，桌面端和手机端协作，能力随设备而适配。

核心架构保留 Judge 调度员，但 Judge 不是唯一大脑；它用聪明且成本可控的方式，帮助小问在复杂任务中选择最优执行层。

这一定义固定六条长期约束：

- 桌面端和手机端都是个人助手，深度协作而非隔离。会话可跨端续接，审批可跨端推送，附件可跨端共享。
- 手机和桌面都各自拥有本地数据库、本地模型入口和本地工作流。
- automation-hub 继续作为身份、同步、审计、备份和跨设备能力的统一脊柱，而不是唯一认知主脑。
- 长期记忆、任务状态、收件箱、执行回执、偏好和设备记录必须共用同一套对象合同与同步层，不允许双真源分叉。
- 第三方模型会话线程、向量索引、摘要缓存和临时 planner 结果都只视为派生层或外部引用，不承担长期真源职责。
- 默认 90%+ 查询由本地 AI（Ollama + Qwen2.5）处理，Judge（DeepSeek V4 Pro/Flash）只在本地无法胜任时介入路由。

### 未来 12 个月执行路线

#### 阶段 A：冻结长期资产合同（1 到 2 个月）

- 目标：先冻结长期资产的对象模型、状态机和同步语义。
- 核心输出：`assistant_task`、`memory_entry`、`inbox_item`、`execution_receipt`、`user_preference`（业务别名 `user_profile`）、`device_record`（业务别名 `device`）、`sync_envelope` 七类主合同。
- **sync_envelope JSON Schema 冻结**：作为阶段 A 的独立输出物，统一双端同步邮包格式（envelope_id、device_id、resource_type、resource_id、action、payload、version、cursor、created_at、checksum），为后续所有同步链路提供唯一数据合同。
- 非目标：不先铺 Android 大页面，不先重做桌面 UI，不先扩插件市场。
- 完成定义：手机与桌面任一入口产生的长期结果，都能落成同构对象；provider thread id 只保留为外部引用；sync_envelope JSON Schema 已冻结为文档化合同，桌面端和 Android 端可据此独立实现同步调度器。

#### 阶段 B：Judge 上线 + 手机独立可用 MVP（2 到 4 个月）

- 目标：Judge 调度员上线；手机在电脑关机时仍可完成高频主流程。
- **Judge 核心输出**：Judge 调度服务（DeepSeek V4 Pro/Flash），智能任务分类与路由，本地 AI 默认处理（Ollama + Qwen2.5），ChromaDB 决策知识库初始化。
- **手机核心输出**：直接对话、快捷收件、轻任务处理、通知接住、审批跨端确认、本地缓存、结果回写和同步状态页。
- **跨端协作**：会话续接 + 审批推送的 API 契约冻结。
- 非目标：不把手机做成第二套平台，不让手机承担高风险自动执行。
- 完成定义：电脑关闭 24 小时内，手机仍可稳定完成问答、记录、收件和继续处理；结果会回写到思忆集自己的体系；Judge 正确路由至少 chat 类请求，90%+ 查询由本地 AI 处理。
- **移动端直连模型例外策略**：阶段 B 至阶段 D 期间，手机可通过独立移动专用密钥直连第三方模型获取对话与认知能力，作为加速体验的受控过渡方案；该通道只承担认知层（对话、摘要、建议），不承担长期记忆真源、执行控制面或同步主链路职责；一旦阶段 D 模型网关双活路由上线，手机端应优先走网关而非继续直连。

#### 阶段 C：同步脊柱 + 跨端协作 + 桌面功能（4 到 7 个月）

- 目标：把"双端都能用"推进到"状态能续上"；桌面端功能面板全面上线。
- **同步核心输出**：device pairing、cursor 增量同步、tombstone、冲突策略、自有同步目录实现、文件级同步（直接文件复制 + 内容哈希增量）。
- **跨端协作核心输出**：会话续接上线（跨设备上下文迁移）、审批跨端推送（WebSocket实时推送 + 生物识别确认）、附件共享层（手机采集→桌面处理→纳入知识库）、能力路由（根据设备能力矩阵自动路由任务）。
- **桌面端面板**：可视化工作流编辑器（Vue Flow）、记忆时间线、知识库管理、审计面板、Judge & 模型管理、同步仪表盘、自升级控制台。
- **WebSocket 实时推送**：参考 syncthing 的事件系统和 XiaoAi 的通知推送体验，在 automation-hub 与客户端之间建立 WebSocket 长连接通道，实现跨端通知实时中继（新任务、冲突提醒、同步完成、收件箱更新等），避免纯轮询带来的延迟和电量消耗。
- **设备配对 UX 升级**：阶段 B 至阶段 C 初期采用 token 配对（桌面端生成配对码、手机端输入确认），阶段 C 后期演进为二维码扫描配对（桌面端展示 QR、手机扫码即完成），同时保留手动输入 token 作为降级通道；配对完成后双方持久化设备身份，后续同步自动进行无需重复授权。
- 非目标：不做强一致协同编辑，不做全量文件云。
- 完成定义：手机创建的记忆、任务、收件和偏好变化，桌面都能稳定增量拿到；复杂冲突有明确回桌面处理路径；会话可在手机和桌面之间无缝续接；审批可跨端推送和确认。

#### 阶段 D：统一任务闭环 + 自升级生态（7 到 10 个月）

- 目标：让小问成为跨端统一代理入口；受控自升级体系完整上线。
- **任务闭环核心输出**：正式的 `Task API`、`Delivery Route`、`Action Broker`、`Capability Registry` 和 `Model Gateway` 双活路由。
- **任务决策优化**：六维分析（成本/时间/精力/质量/风险/可持续），消费预算与预警，决策知识库持续积累。
- **受控自升级**：TechRadar 每日扫描 → 每日晨报「技术动态」板块 → 用户确认 → Docker/venv 沙箱测试 → 自动快照 → 执行升级 → 验证报告 → 一键回滚。
- **Model Gateway 双活路由详解**：桌面端通过 automation-hub 的 Model Gateway 统一访问所有 LLM 厂商（provider 抽象层负责路由与计费），手机端默认也走网关，但在弱网或紧急场景下允许降级为直连模型（使用独立移动密钥，行为受 Capability Registry 约束）；网关负责统一 token 管理、速率限制、成本追踪和审计记录，厂商 thread id 只保留为外部引用不进入业务主模型。
- 非目标：不开放无边界自治，不让 automation-hub 现有 `tasks` / `runs` 直接承接个人任务真源。不允许 AI 直接修改核心代码。
- 完成定义：任务可在手机发起、桌面继续、底座执行，并统一回到同一状态机与执行回执。TechRadar 每日运行，升级报告进入晨报。

#### 阶段 E：自托管与长期恢复（10 到 12 个月）

- 目标：把“未来可迁移”变成真实产品能力。
- 核心输出：自托管部署选项、快照与备份、恢复演练、附件层接入、导出与重导入。
- 非目标：不做中心化 SaaS，不做大而全云盘。
- 完成定义：切换到自托管、云设备或更强本地硬件时，替换的是算力与部署位置，不是长期资产语义。

## 2026-04 执行顺序校正

在不改变既有阶段定义的前提下，当前建议把桌面端与个人助手相关工作按下面顺序推进，避免再次回到“并行铺功能、主线不闭合”的状态。

1. 先完成阶段 0 和阶段 1，关闭远端放行与生产基线缺口。
2. 再把阶段 3 中的“桌面端日常可用化”提到最前，优先收口设置治理、启动稳定性、工作台基础布局和桌面 Doctor。
3. 然后把阶段 3 的产品主线固定为“个人动态收件箱 -> 私人空间整理器 -> 当前内容整理 -> 低打扰建议”，不先追求更多零散模块。
4. 在收件与整理闭环稳定后，再推进工作台化首页和悬浮助手最小闭环，让桌面端先成为稳定入口。
5. 之后再推进小问角色运行时，顺序固定为“统一事件入口 -> 字幕层 -> 主动说话策略 -> 工具注册表 -> 表现层升级”。
6. 最后再扩大同步可靠性、设备概念和监督下自优化工作流，避免角色层和自动化层过早放大底层噪音。

补充约束：

- 不把 [思忆集app](../思忆集app) 做成 `openclaw` 或 `nanobot` 那样的多渠道消息平台；消息平台化不属于当前主线。
- 不把浏览器扩展升级为高权限下载解析器；其当前定位仍是“结构化网页上下文采集器 + 本地桥接入口”。
- 不把角色化桌宠表现层排在统一事件入口、字幕层和工具注册表之前；否则只会得到更好看的壳，而不是更稳定的角色能力。

## 2026-04 至 2026-09 六个月执行地图

在不新增新阶段定义的前提下，当前建议把后续 6 个月压成四个明确里程碑，避免又回到“每条线都推进一点，但没有一条真正收口”的状态。

对应的实施层清单与桌面 Sprint 拆解已落到： [siyi-m0-m3-implementation-checklist.md](siyi-m0-m3-implementation-checklist.md) 。

### 里程碑 M0：远端放行闭环（4 周）

- 目标：关闭真实部署环境 `live` / `live-worker` 放行缺口。
- 核心输出：两份远端验收留档、固定 `live_api_base`、固定 token 来源、固定 values 选择策略。
- 若 M0 未完成，后续所有桌面端同步、Agent 与多端能力都只能算“本地原型推进”，不能视为产品主线已经放行。

### 里程碑 M1：桌面日用入口稳定化（4 到 6 周）

- 目标：把桌面端从“功能原型”推进成“稳定日用入口”。
- 核心输出：配置模型统一、启动链路稳定、托盘与悬浮入口稳定、Doctor 结构化资产复用、主窗口第一刀解耦完成。
- 默认承接文档： [siyi-desktop-delivery-plan-2026-04.md](siyi-desktop-delivery-plan-2026-04.md) 、 [desktop-doctor-debug-assets.md](desktop-doctor-debug-assets.md) 、 [main-window-decoupling-plan.md](main-window-decoupling-plan.md) 。

### 里程碑 M2：产品主线闭环（6 周）

- 目标：把“收件 -> 私人空间整理 -> 当前内容整理 -> 低打扰建议 -> 工作台继续入口”做成每天都能跑通的闭环。
- 核心输出：统一收件模型、私人空间 `inbox/daily/insights` 三层落盘、统一“整理当前内容”动作、上下文建议助手、工作台首页与悬浮入口承接继续事项。
- 默认承接文档： [next-phase-product-boundary.md](next-phase-product-boundary.md) 、 [app-platform-boundary-design.md](app-platform-boundary-design.md) 、 [app-client-structure-draft.md](app-client-structure-draft.md) 。

### 里程碑 M3：小问统一任务入口（6 到 8 周）

- 目标：让小问从聊天入口升级为统一任务入口。
- 核心输出：`assistant_task`、统一交付通道、最小用户画像、字幕层、受控执行闭环、能力注册表读模型。
- Android 在本里程碑内只冻结 API 与同步合同，不启动全量客户端复制。
- Android 当前接口、当前页面集合和当前允许开发切片，以 [android-client-technical-design.md](android-client-technical-design.md) 的当前矩阵为准；[android-client-ui-layout.md](android-client-ui-layout.md) 只作为交互母版，不作为当前页面清单真源。
- 默认承接文档： [xiaowen-agent-layer-roadmap.md](xiaowen-agent-layer-roadmap.md) 、 [xiaowen-character-assistant-plan.md](xiaowen-character-assistant-plan.md) 、 [assistant-execution-framework.md](assistant-execution-framework.md) 、 [android-client-technical-design.md](android-client-technical-design.md) 。

### 串行门槛

1. M0 未完成前，不把主资源切去做角色层、Android 或联网扩展生态。
2. M1 未稳定前，不继续向 [../思忆集app/ui_main_window.py](../思忆集app/ui_main_window.py) 堆业务入口。
3. M2 未稳定前，不把小问推向主动打断、角色表现层和更重的自治能力。
4. M3 未完成前，不启动 Android 正式产品化开发，只允许合同整理、配对方案和离线缓存原型。

### 可并行工作流

- M0 内：远端环境归属确认、放行清单预填、release_check 演练和 Helm 基线梳理可以并行。
- M1 内：Doctor widget 化与 JSON 导出可以和主窗口第一刀解耦并行，但前提是 `debug_assets` 结构先稳定。
- M2 内：收件模型、私人空间整理器与工作台容器可以并行推进，但前提是收件 schema 与建议状态模型先固定。
- M3 内：用户画像最小模型和 capability registry 读模型可以并行，但前提是 `assistant_task` 与 `delivery_route` 已冻结。

当前已启动项： [../scripts/desktop_doctor.py](../scripts/desktop_doctor.py) 已补 `--debug-assets-json` 导出，作为 M1 内“Doctor 结构化资产 JSON 导出”的第一刀。

## 阶段 0：远端放行闭环

这是当前最高优先级阶段。

### 目标

把“本机已通过”升级为“真实部署环境已留档”。

### 主要工作

- 按 [automation-hub/docs/remote-acceptance-plan.md](../automation-hub/docs/remote-acceptance-plan.md) 完成一次远端 `live`
- 按同一计划完成一次远端 `live-worker --live-worker-executor docker`
- 执行时使用 [automation-hub/docs/remote-acceptance-checklist.md](../automation-hub/docs/remote-acceptance-checklist.md) 逐项收集证据与回填留档
- 回填两份远端验收记录，并在根 README 中关闭对应待办
- 确认远端环境的 token、worker、Redis、执行器节点约束都与留档一致

### 完成定义

- `live` 真实环境执行成功且留档完成
- `live-worker` 真实环境执行成功且留档完成
- 根目录 [README.md](../README.md) 中这两项待办被关闭或替换为下一阶段任务

### 不在本阶段做的事

- 不新增大功能模块
- 不重写架构设计文档
- 不扩大执行器种类和客户端接入面

## 阶段 1：生产基线固化

该阶段在远端放行闭环之后推进。

### 目标

让部署行为可预测、配置边界明确、运行约束可以被团队复用。

### 主要工作

- 固化 staging / production 的 Helm values 选择策略
- 明确 Docker executor 与 containerd-only 集群的适用边界
- 将 Redis、Secret、ServiceMonitor、worker 调度约束纳入部署基线
- 明确 SQLite 单副本限制，并给出未来切换外部数据库的迁移条件
- 把生产前检查、live 验收、产物清理 dry-run 串成一条标准运维路径

### 交付物

- 更新后的部署模板与生产 values 基线
- 收敛后的部署说明和运行手册
- 一套可复用的环境检查与放行清单

### 完成定义

- 团队可以不依赖口头说明完成一次标准化部署
- 生产约束能在文档和配置层同时体现
- 部署后验收、回滚、清理都有固定入口

## 阶段 2：安全工作流闭环

### 目标

把当前已有的审批、提案、审计和重试能力，收敛成一套完整的可运营安全工作流。

### 主要工作

- 补齐 proposal 从创建、审批、执行、失败、重提的全生命周期约束
- 继续完善高风险 tool-run 的重发、取消、过期和终态一致性
- 增强 run / proposal 产物保留、清理与审计追溯策略
- 为关键路径补齐更稳定的回归用例和异常路径覆盖

### 交付物

- 更完整的审批与提案状态机约束
- 更稳定的审计事件模型
- 更清晰的清理策略与运维脚本说明

### 完成定义

- 高风险流程从申请到终态都有明确定义
- 审批与提案不会长期悬挂或进入不可解释状态
- 排障时可以从审计和产物中完整还原关键动作

## 阶段 2A：本地代理与边缘执行器

### 目标

在不削弱 `automation-hub` 控制面定位的前提下，补齐一个可长期演进的本地代理层，使系统既能保持企业级安全治理，又能获得接近本地助手的交互效率和边缘执行能力。

### 设计定位

- `automation-hub` 继续作为控制面，承接认证、工具白名单、审批、审计、编排、策略下发和统一 API。
- 新增本地代理作为边缘面，运行在用户电脑、跳板机、运维节点或目标服务器上，负责本地交互、上下文采集、策略缓存和受控执行。
- 本地代理参考 `nanobot` 的轻量 loop、provider 抽象、channel 管理和会话隔离思路，但不允许直接绕过 `tool-runs`、审批和审计链路执行高风险动作。

### 主要工作

- 设计独立的 `local_agent` 运行时，而不是继续把本地代理能力堆进现有 `/agent` API 路由。
- 建立本地代理与控制面之间的节点注册、心跳、策略拉取、授权申请和结果回传协议。
- 抽象本地执行适配层：shell、PowerShell、filesystem、process、docker、k8s、ssh、log 等统一经 adapter 暴露，而不是让 Agent 直接拼接执行细节。
- 为本地代理增加离线缓冲、断线重连、临时授权缓存和审计补传能力，确保弱网条件下仍能保持最小可用。
- 明确高风险动作的 proposal / approval / run / artifact / audit 串联关系，做到本地执行也有完整中心留痕。

### 第一版范围

- 本地守护进程与 CLI 双入口
- 节点注册、心跳、配置与策略同步
- 流式输出与本地会话管理
- 只读能力优先：目录浏览、文件读取、日志查看、进程与系统信息查看
- 高风险动作默认 proposal 化，获得中心一次性授权后才执行

### 企业化预留能力

- 设备身份证书、mTLS、OIDC / SSO
- 节点分组、策略下发、版本与插件签名治理
- 与 CMDB、ITSM、SIEM、告警平台的对接
- 批量执行、灰度执行、自动回滚和统一观测

### 完成定义

- 控制面与边缘面的职责边界在文档和代码结构中都清晰可见
- 本地代理已能在不破坏现有 API / worker 主线的前提下独立运行
- 至少一条“本地 CLI -> 控制面授权 -> 本地执行 -> 中心审计”的闭环链路跑通
- 具备继续扩展到企业节点管理和多终端接入的架构余量

## 阶段 3：PC 客户端整合

### 目标

把 [思忆集app](../思忆集app) 从“本地原型”升级为“本地优先、可云端增强的 PC 个人信息筛选与工作台客户端”，同时为后续 Android 接入预留统一的数据与同步边界。

### 主要工作

- 梳理思忆集现有功能边界，明确哪些保留在桌面端本地，哪些迁移到后端服务
- 抽出个人知识管理域模型：`notes`、`personal_tasks`、`wordbooks`、`saved_articles`、`translation_history`、`user_preferences`
- 已先以 `notes` 作为第一类迁移资源落地同步骨架：本地笔记已补齐 `version`、`device_id`、`sync_state`、`last_synced_at` 等字段，并已有独立同步载荷转换模块
- 停止继续扩大 [思忆集app/DatabaseHandler.py](../思忆集app/DatabaseHandler.py) 的职责，逐步拆分为领域仓储接口、本地存储实现和远端 API 实现
- 将新闻抓取、翻译代理、同步、身份接入优先迁到 `automation-hub`
- 保留桌面端本地优先能力：离线编辑、本地缓存、本地提醒、快速输入
- 为每类核心资源补齐 `updated_at`、`version`、`deleted_at`、`device_id`、`sync_state` 等同步字段约束

### 交付物

- 一版明确的 PC 客户端与后端边界说明
- 初始的个人知识管理 API 设计稿或骨架实现
- 思忆集桌面端的本地仓储 / 远端仓储双实现骨架
- 一套可演进到 Android 的同步模型约定
- 一版明确的产品模块边界，区分“信息筛选层、工具收束层、习惯记忆层、可布局工作台、悬浮助手层”和底座增强能力

### 完成定义

- PC 端继续可独立运行，但核心数据模型不再绑死本地 JSON 文件
- 桌面端已能接入统一身份体系与至少一项后端能力，例如笔记同步或翻译代理
- 团队对“什么属于客户端，什么属于底座”已有明确边界，不再继续堆叠平行实现
- 产品定位已从“多功能原型集合”收敛为“信息筛选 + 工具收束 + 个人工作台”的统一方向

### 当前进展（2026-03-12）

- 桌面端五类核心个人数据已经形成统一同步基线：`notes`、`personal_tasks`、`saved_articles`、`translation_history`、`wordbooks/words`
- 上述资源都已具备同步元数据、手动同步入口、设置页自动同步开关，以及桌面端 smoke 回归覆盖
- 桌面端已补齐 `api_sync_orchestrator`，统一串联 pull/merge/conflict/push/cursor/audit，避免各模块各自维护同步主链路
- 自动同步策略已统一为“默认关闭、120 秒周期轮询、本地变更后 8 秒去抖同步”

### 当前进展补充（2026-04-07）

- 已新增本地统一记忆索引，覆盖笔记、任务、收件箱、助手决策和执行记录，供助手检索与周报复用
- 已把一轮联网增强升级为“本地记忆召回 + 多轮联网搜索”的组合检索链路，并增加本地缓存与去重
- 已新增正式配置 `assistant_focus_topics`，作为技术追踪和周报生成的统一关注主题来源
- 已新增 `weekly_tech_digest` 子代理工作流，并挂入默认规则中心，作为独立于 weekly notes digest 的技术周报链路
- 任务、新闻收藏、翻译历史、词库已具备可见冲突面板和人工处理动作；笔记仍保留较早期冲突路径，尚未完全统一
- automation-hub agent 已补齐 `/agent/execute/{plan_id}` 和 `/agent/sessions/{session_id}`，worker 审计已写入 `duration_sec`，回滚不再是空实现
- 因此阶段 3 的主要矛盾，已经从“有没有同步能力”转为“同步体验是否统一、客户端边界是否清晰、历史兼容路径是否继续收口”

### 阶段 3 下一步收口重点

1. 统一桌面端同步体验：状态栏文案、最近一次同步时间、冲突摘要和手动/自动同步反馈应在各对话框收敛为同一套规则。
2. 继续清理历史兼容路径：减少本地 mock cloud 回退逻辑、旧入口和重复实现，避免后续重构时同时维护新旧两套行为。
3. 为结构重构做准备：把同步调度、领域服务和对话框 UI 进一步拆开，避免继续放大 [思忆集app/ui_tasks_dialog.py](../思忆集app/ui_tasks_dialog.py)、[思忆集app/ui_news_dialog.py](../思忆集app/ui_news_dialog.py)、[思忆集app/ui_translate_dialog.py](../思忆集app/ui_translate_dialog.py) 的文件级耦合。
4. 在体验和边界收口之后，再推进更重的底层改造，例如本地主存从 JSON 向 SQLite 过渡、正式身份接入和远端仓储分层。

### 阶段 3 当前建议的实际执行顺序

为避免阶段 3 继续膨胀，当前建议把这部分工作固定为五个连续工作包：

1. 桌面稳定性与设置治理：围绕 [思忆集app/settings_schema.py](../思忆集app/settings_schema.py)、[思忆集app/ui_settings_dialog.py](../思忆集app/ui_settings_dialog.py)、[思忆集app/app_context.py](../思忆集app/app_context.py) 收口配置模型、迁移、快照、诊断和启动链路。
2. 收件与整理主线：优先打磨浏览器扩展、当前内容整理、私人空间落盘和上下文建议，使“收进来 -> 整理掉 -> 继续推进”形成稳定闭环。
3. 工作台与悬浮入口：在主线稳定后，再强化工作台卡片、布局持久化、同步状态摘要和悬浮助手高频动作入口。
4. 角色型小问：把工作台助手能力统一收口到角色运行时，而不是继续把逻辑散落在主窗口、桥接服务和定时器里。
5. 同步可靠性与监督下自优化：最后才补设备概念、同步可靠性、提案执行闭环和更重的自动化能力。

### 阶段 3 当前暴露的高优先级工程问题

从当前代码现实看，后续若不先处理以下问题，阶段 3 的新功能会持续放大维护成本：

- [思忆集app/ui_main_window.py](../思忆集app/ui_main_window.py) 仍是主耦合点，后续应继续把工作台卡片、事件路由和布局管理拆出去。
- [思忆集app/assistant_autonomy.py](../思忆集app/assistant_autonomy.py) 过重，建议拆成应用启动解析、规划、执行、Windows 环境探测等更清晰的子模块。
- [思忆集app/ui_translate_dialog.py](../思忆集app/ui_translate_dialog.py) 已同时承载翻译、历史、单词本和同步行为，继续扩展前应先分离职责。
- [思忆集app/DatabaseHandler.py](../思忆集app/DatabaseHandler.py) 应继续降级为兼容层，不再作为新功能入口。
- 测试优先级应优先补仓储层、浏览器桥端到端、设置/主窗口关键交互、OAuth/Token 刷新和 assistant autonomy 规则匹配，而不是只继续堆 smoke。

### 阶段 3A：桌面端日常可用化

在同步基线已经成形之后，阶段 3 的下一个直接目标不是继续扩面，而是把 [思忆集app](../思忆集app) 从“功能原型”推进到“可以稳定日常使用的 PC 客户端”。

#### 子阶段目标

- 把整体 UI 收敛为统一、简约、大气的桌面风格，而不是继续维持各窗口各自为战的原型界面
- 优先把新闻、笔记、翻译/单词本三个高频模块补齐到可长期使用，而不是只满足演示链路
- 在不破坏当前同步能力的前提下，先提升桌面端交互完成度、信息组织能力和长时间使用体验
- 把“关闭即退出”的桌面程序心智升级为“关闭后退到陪伴态”的个人助手心智：先托盘和悬浮窗，再逐步演进到卡通人物或宠物形态

#### 产品定位

- 思忆集的核心不是“再做一个什么都想做的助手”，而是做用户自己的信息筛选层与个人工作台
- 第一价值是筛选信息：判断哪些信息值得看、哪些来源值得保留、哪些内容应转成任务、笔记、收藏或稍后处理
- 第二价值是收束工具：帮助用户整理哪些 app、网站、工具和流程真正高频有效，而不是继续沉没在分散入口里
- 第三价值是沉淀习惯：逐步记住用户的常用模块、常用时段、常见操作和低价值来源，为后续个性化排序与提醒提供依据
- 第四价值是形成工作台：把高频模块收敛到一个可自定义布局的主界面里，而不是继续在多个孤立窗口和外部 app 之间来回切换

#### 模块规划

1. 信息筛选层

- 负责汇聚新闻、收藏、剪贴板内容、手动记录、后续可扩展的数据源，并做去重、摘要、重要性判断和转存
- 输出结果不是简单列表，而是“待看、已收束、可转任务、可转笔记、可忽略”的决策结果

2. 工具收束层

- 负责整理用户常用 app、网站、功能入口和操作链路
- 长期目标不是做应用商店，而是帮助用户明确哪些工具真正管用、哪些入口应常驻、哪些应用低频冗余

3. 习惯与记忆层

- 负责记录用户的使用偏好、常看模块、常见时间段和高频动作
- 必须坚持本地优先、可关闭、可删除、可导出，避免把“个性化”做成不可控的黑盒记录

4. 可布局工作台层

- 主界面应演进为模块化卡片工作台，允许用户控制显示哪些模块、顺序、尺寸和布局模板
- 初期先支持显示/隐藏、排序、尺寸和预设布局，不直接走到无限自由摆放的桌面设计器模式

5. 常驻陪伴层

- 悬浮助手先承接提醒、快速记录、同步状态和主界面恢复
- 后续再向个性化桌宠、卡通角色、宠物形态扩展，但表现层升级不能破坏现有交互模型

6. 插件扩展层

- 插件方向先做受控的内部插件制，而不是立刻做开放市场
- 首批只需要支持首页卡片插件、工具面板插件、数据源插件、动作插件和悬浮助手插件
- 所有插件都应通过明确接口访问数据和能力，不直接耦合到底层存储和窗口对象

补充入口：桌面端关于“可布局工作台、悬浮助手、插件体系”的可执行需求清单，统一维护在 [思忆集app/README.md](../思忆集app/README.md)。关于“下一阶段必须做、可延期、明确不做什么”的产品边界，统一维护在 [next-phase-product-boundary.md](next-phase-product-boundary.md)。路线图只保留阶段方向与边界，不重复维护细化产品清单。

#### 主要工作

1. 整体 UI 主题升级

- 为 [思忆集app/ui_main_window.py](../思忆集app/ui_main_window.py) 和各对话框建立统一主题、颜色、字号、间距和按钮层级规范
- 收敛列表、表单、详情区、工具栏和状态栏的视觉语言，减少当前大量内联样式导致的割裂感
- 把高频窗口改成更稳定的分栏和留白结构，优先优化桌面端长时间阅读、编辑和切换体验

2. 新闻模块完善

- 聚焦 [思忆集app/ui_news_dialog.py](../思忆集app/ui_news_dialog.py)，把“抓取、阅读、收藏、翻译、转笔记、转任务”这几条主链路做成稳定闭环
- 补齐新闻列表过滤、关键词检索、来源与分类筛选、收藏视图、详情加载反馈与正文阅读体验
- 让同步状态、收藏状态和转存动作对用户可见，避免新闻模块继续停留在“能用但不顺手”的原型状态

3. 笔记模块功能补齐

- 聚焦 [思忆集app/ui_notes_dialog.py](../思忆集app/ui_notes_dialog.py)，补齐文件夹、标签、检索、收藏、删除恢复、移动归档等日常管理动作
- 继续完善编辑器、列表区和详情区的联动，让笔记模块从“能编辑”升级到“能整理、能沉淀、能回找”
- 把笔记同步和冲突处理路径统一到当前其他模块的体验标准上

4. 翻译与单词本完善

- 聚焦 [思忆集app/ui_translate_dialog.py](../思忆集app/ui_translate_dialog.py)，继续优化翻译输入、结果、历史、同步状态和单词本之间的高频路径
- 将单词本从“可同步的附属功能”提升到“可长期维护的词汇系统”，补齐单词本管理、单词检索筛选、详情编辑、批量整理与状态反馈
- 优先保证“翻译结果进入单词本”足够顺手，再考虑复习、统计等增强功能

5. 悬浮助手产品化

- 第一阶段先把主窗口关闭行为改为“隐藏到托盘 + 保留一个可拖动、可置顶、可恢复主窗口的小型悬浮助手”，并保留明确的真正退出入口
- 第二阶段让悬浮助手承接高频动作：快速记笔记、快速加任务、提醒提示、同步状态提示、恢复主窗口
- 第三阶段再把悬浮助手外观升级成卡通人物、宠物或二次元角色；此时只替换表现层，不重做生命周期、提醒、快捷入口和同步状态逻辑
- 悬浮助手长期定位不是装饰物，而是思忆集个人云的常驻入口，后续还应承担最近项目、跨设备继续编辑、冲突待处理等云侧提示

6. 主界面可布局化

- 把主界面从固定首页逐步演进为模块化工作台，允许用户自主决定显示哪些模块、模块顺序、模块尺寸和布局模板
- 优先实现卡片式布局、拖拽排序、显示/隐藏和预设模板；更高自由度的任意摆放放到后续阶段

7. app 与底座分离

- 坚持“本地优先，云端增强”的产品边界：没有底座时客户端照常可用，有底座时再开启同步、备份、跨设备、统一身份和插件分发
- app 负责本地体验、本地提醒、本地缓存、工作台布局和悬浮助手；底座负责身份、同步、备份、审计、搜索和跨端能力

#### 推荐顺序

1. 先统一整体 UI 风格和主窗口/对话框布局规则。
2. 再完善新闻模块，因为它是当前功能最杂、最容易影响日常可用性的高频入口。
3. 然后补齐笔记模块的管理与编辑闭环。
4. 最后完善翻译与单词本，把它从工具入口升级为长期积累入口。

#### 完成定义

- 用户可以在桌面端连续完成“读新闻、存笔记、查任务、做翻译、积累词汇”的高频日常路径，而不会频繁遇到明显的原型级缺口
- 主要窗口的视觉风格、操作层级和状态反馈保持一致，不再出现明显的拼装感
- 新闻、笔记、翻译/单词本三类高频模块都具备长期使用所需的基础管理能力，而不是只覆盖演示链路
- 主窗口关闭后不会直接把用户从工作流里踢出，而是能平滑退到托盘或悬浮助手，并在需要时一键回到完整界面

## 阶段 4：个人云平台化

### 目标

把当前“本地优先 + 结构化同步”的思忆集，逐步演进为一个由 `automation-hub` 承接后端底座的个人云平台。

这里的“个人云平台”不等于立刻做一个完整的百度网盘替代品，而是按三段路线推进：

1. 先做个人数据云，统一结构化数据与跨设备同步。
2. 再做个人内容云，把附件、图片、导出包和备份包纳入统一存储。
3. 最后做个人云工作台，把搜索、版本、分享、设备管理和回收站收束成平台能力。

### 产品定位

- 平台主线是“个人信息筛选、工具收束与生活管理云”，不是一开始就做大而全的公网文件分发平台
- `automation-hub` 继续作为唯一后端底座，负责身份、同步、审计、任务调度、API 和后续云能力承载
- [思忆集app](../思忆集app) 继续作为 PC 客户端主入口，悬浮助手作为常驻入口和提醒入口，主界面逐步演进为可布局工作台
- 百度网盘及类似外部服务只承担备份、归档、导出和迁移辅助角色，不作为主数据源、主同步引擎或核心存储协议
- 平台不以“所有能力都必须依赖云端”为前提，而是坚持 app 可单独工作、底座负责增强能力的分层关系

### 长期数据、隐私与时代更迭原则

- 数据资产优先于模型资产：`notes`、`personal_tasks`、`saved_articles`、`translation_history`、记忆、附件、审计记录、用户偏好和设备状态才是项目真正需要长期保存的核心资产；模型 provider 只是可替换算力来源。
- 统一记忆与任务真源必须掌握在项目自身：第三方模型会话线程、外部向量库、手机本地缓存都只能视为派生层或缓存层，不能成为 `memory_id`、`task_id`、`device_id`、`artifact_id` 的真源。
- 结构化数据与内容资产分层存储：结构化资源维持显式 schema、版本号和迁移历史；附件、图片、导出包和备份包走统一对象存储抽象；摘要、索引、向量和缓存都必须可重建。
- 隐私策略默认前置：数据至少区分“仅本地”“可同步”“可分享”“允许送第三方模型”四个等级；高敏感内容默认不出端，只有显式授权或自托管推理可见。
- 所有外部能力都通过稳定接口接入：业务层只依赖 Memory API、Task API、Action Broker、Capability Registry 和 Model Gateway，不把厂商 thread id、SDK 类型或供应商特有字段写成主数据模型。
- 备份、导出、迁移和恢复必须长期可验证：周期性快照、迁移清单、恢复演练和开放导出格式属于平台能力，不依赖某个时代流行的单一外部服务。
- 移动端直连第三方模型可作为加速体验的受控例外，但只能承担认知层，不能演化成长期记忆真源、执行控制面或企业能力主链路。

### 三阶段产品路线

#### 阶段 4A：个人数据云

目标：先把当前已经形成雏形的结构化个人数据体系真正收口为“统一身份、统一同步、统一状态反馈”的个人数据云。

重点范围：

- 笔记、个人任务、提醒、翻译历史、单词本、新闻收藏、用户设置
- 常用应用清单、工具入口偏好、主界面布局配置、插件配置、信息来源偏好
- 设备身份、登录态、同步中心、冲突处理、审计日志
- 悬浮助手的云状态提示，例如同步中、冲突待处理、提醒到期、备份完成

完成定义：

- 用户在多设备之间同步结构化数据时，不再依赖文件级同步工具
- 桌面端具备明确的“本地优先 + 可恢复同步”体验
- 平台已经形成“个人云”的第一层价值，而不是单纯的本地桌面应用

#### 阶段 4B：个人内容云

目标：在个人数据云稳定后，把附件和文件内容纳入统一的云存储体系，形成真正接近云盘能力的第二层。

重点范围：

- 笔记附件、图片、导出包、备份包、音频、文档等非结构化内容
- 文件元数据、对象存储抽象、上传下载、断点续传、版本历史、回收站
- 以资源引用关系把“文件”挂接到笔记、新闻收藏、翻译结果和后续更多业务对象

完成定义：

- 平台已不只是同步字段和 JSON 数据，而是能稳定承接用户真实内容资产
- 文件和结构化数据之间形成统一的元数据管理，而不是彼此割裂
- 外部服务如百度网盘可作为备份目的地，但不是主存储核心

#### 阶段 4C：个人云工作台

目标：把数据云和内容云进一步整合成有平台感的个人云工作台，而不是功能集合。

重点范围：

- 全文检索、标签检索、最近使用、设备管理、空间配额、版本恢复、分享能力
- 统一通知中心、回收站、历史记录、审计视图、数据导入导出
- 悬浮助手、桌面主界面、未来移动端和 Web 入口之间的统一状态与入口模型
- 插件管理、工作台模板、常用工具编排和跨设备偏好同步

完成定义：

- 平台可以承接个人长期知识沉淀、生活记录和文件资产管理
- 用户能从任一入口持续回到相同的数据视图、版本体系和设备体系
- 思忆集完成从“桌面原型”到“个人云平台入口”的产品升级

### 不在阶段 4 初期做的事

- 不把自己直接定义为百度网盘替代品
- 不优先投入大规模公网分享、超大文件分发和通用企业网盘能力
- 不让百度网盘类外部平台反向决定本项目的数据模型和同步边界

### 当前代码暴露出的主要缺口

在把 [思忆集app](../思忆集app) 作为 PC 客户端继续推进前，当前代码已经暴露出几类必须先处理的问题：

#### 1. UI 层职责过重且风格分散

- [思忆集app/ui_main_window.py](../思忆集app/ui_main_window.py) 仍同时承担窗口入口、共享状态和存储初始化职责
- 多个对话框大量内联样式、窗口尺寸固定，缺少统一的主题、组件层和响应式约束
- [思忆集app/ui_alarm_dialog.py](../思忆集app/ui_alarm_dialog.py) 仍是原型级界面，只有设置列表，没有完整提醒调度、编辑、删除和持久化闭环

改良要求：

- 为 PC 端补一层统一的 UI 主题与通用组件，不再继续在各窗口里散落大量样式字符串
- 把主窗口收口为导航壳，不再继续累积全局共享状态
- 把提醒能力从“界面占位”升级为真正的本地提醒子系统

#### 2. 窗口逻辑与业务逻辑强耦合

- [思忆集app/ui_tasks_dialog.py](../思忆集app/ui_tasks_dialog.py) 同时存在历史遗留方法和新方法，出现同名方法重复定义、旧路径残留、行为被后定义方法覆盖的问题
- [思忆集app/ui_news_dialog.py](../思忆集app/ui_news_dialog.py) 同时承担新闻抓取、正文解析、图片加载、翻译、任务联动和展示，属于典型的巨型对话框
- [思忆集app/ui_translate_dialog.py](../思忆集app/ui_translate_dialog.py) 中把单词本、翻译器实例查找、历史记录和窗口控制绑在同一文件里，模块边界不清晰

改良要求：

- UI 文件只负责交互和状态展示，业务规则迁到 service / controller / viewmodel 层
- 任务、新闻、翻译、单词本都要拆出独立服务对象，避免对话框继续膨胀
- 清理重复方法和历史残留逻辑，先保证行为一致性，再做后续重构

#### 3. 本地数据层已经超出原型可承受范围

- [思忆集app/DatabaseHandler.py](../思忆集app/DatabaseHandler.py) 已经同时承接任务、笔记、新闻、翻译、单词本、设置、同步模拟等多种职责
- 当前主存仍以 JSON 文件为主，缺少事务、并发保护、迁移机制和资源级版本控制
- `cloud_notes` 仍是模拟同步而不是正式同步协议，无法支撑 PC 到 Android 的真实演进

改良要求：

- 将 `DatabaseHandler` 按领域拆解，停止继续新增方法
- 本地主存切换到 SQLite，JSON 仅作为迁移输入或缓存补充
- 所有核心资源统一建立版本号、软删除和同步状态字段

#### 4. 同步与第三方能力还停留在原型阶段

- 翻译和新闻能力仍由客户端直接调用第三方或直接抓取网络资源
- 多个网络调用是同步请求，当前实现会阻塞 UI 线程，影响桌面端体验
- 第三方凭据仍出现在客户端代码中，不符合后续多端与统一后端模型

改良要求：

- 新闻抓取、翻译代理、缓存和清洗逐步迁回 `automation-hub`
- 桌面端网络操作必须异步化或移入后台任务，不再阻塞主线程
- 长期目标是所有第三方密钥和可变配置从客户端移除，统一由后端或模型网关管理；若为移动体验临时启用手机直连模型，也必须使用独立移动专用密钥，并明确为受控过渡方案

#### 5. 质量与可维护性不足

- 当前 UI 和数据层缺少清晰的测试边界，难以对单个模块做稳定回归
- 对话框之间依赖 `parent_ref`、`open_dialogs`、`alarms` 等隐式共享状态，替换实现成本高
- 当前还没有形成“客户端本地仓储 / 远端仓储 / 同步调度”的清晰分层，导致每新增一个功能都会继续加重耦合

改良要求：

- 为领域层和仓储层建立最小可测试边界，避免测试只能从 UI 手工点击开始
- 显式化客户端依赖注入，逐步摆脱 `parent_ref` 级联穿透
- 优先收敛架构，再做 UI 美化与能力扩张

### 不在本阶段做的事

- 不追求 Android 客户端同时落地
- 不追求桌面端与未来移动端共享 UI 代码
- 不把个人任务直接塞进当前自动化 `tasks` / `runs` 表

### 执行拆分顺序

为避免阶段 3 继续停留在概念层，建议按下面顺序推进，并且每个工作包都要求可单独验证。

#### 工作包 A：桌面端入口收口

目标：先把当前桌面原型的“主窗口直接持有所有状态和存储对象”的结构收口，避免后续继续扩大耦合面。

- 入口文件： [思忆集app/思忆集test1.py](../思忆集app/思忆集test1.py)
- 归档备份： [思忆集app/思忆集test1_backup.py](../思忆集app/思忆集test1_backup.py) 仅作历史对照，不再作为运行入口或整改目标
- 主窗口： [思忆集app/ui_main_window.py](../思忆集app/ui_main_window.py)
- 基础对话框： [思忆集app/ui_base_dialogs.py](../思忆集app/ui_base_dialogs.py)

具体动作：

- 新增桌面端应用上下文对象，例如 `AppContext` 或 `DesktopServices`，替代当前由 `MainWindow` 直接创建 `DatabaseHandler` 的方式
- `MainWindow` 只负责窗口入口、对话框注册和共享 UI 状态，不再承担业务资源初始化
- 对话框改为从统一上下文获取仓储和服务，而不是继续依赖 `parent_ref.db_handler`、`parent_ref.db`、`parent_ref.alarms` 等临时共享字段
- 补一个统一的主题与组件入口，避免后续继续在窗口里散落样式和控件装配细节

完成标志：

- [思忆集app/ui_main_window.py](../思忆集app/ui_main_window.py) 不再直接 new 出所有后续能力依赖
- 新窗口接入方式可统一复用，不需要继续堆叠新的全局字段

#### 工作包 B：本地存储层拆分

目标：停止继续扩张 [思忆集app/DatabaseHandler.py](../思忆集app/DatabaseHandler.py)，把当前“大而全”的本地存储类拆成领域仓储接口。

核心文件：

- [思忆集app/DatabaseHandler.py](../思忆集app/DatabaseHandler.py)
- [思忆集app/ui_notes_dialog.py](../思忆集app/ui_notes_dialog.py)
- [思忆集app/ui_tasks_dialog.py](../思忆集app/ui_tasks_dialog.py)
- [思忆集app/ui_alarm_dialog.py](../思忆集app/ui_alarm_dialog.py)
- [思忆集app/ui_translate_dialog.py](../思忆集app/ui_translate_dialog.py)
- [思忆集app/ui_news_dialog.py](../思忆集app/ui_news_dialog.py)

建议拆分出的仓储接口：

- `NoteRepository`
- `PersonalTaskRepository`
- `ReminderRepository`
- `NewsRepository`
- `WordbookRepository`
- `TranslationRepository`
- `SettingsRepository`

具体动作：

- 先清理 [思忆集app/ui_tasks_dialog.py](../思忆集app/ui_tasks_dialog.py) 中旧的 `self.parent_ref.db` 路径，只保留一套明确接口
- 修复 [思忆集app/ui_tasks_dialog.py](../思忆集app/ui_tasks_dialog.py) 中同名方法重复定义和历史逻辑残留，先保证行为可预测
- 把笔记、任务、提醒、单词本、新闻缓存、翻译历史的读写方法从 [思忆集app/DatabaseHandler.py](../思忆集app/DatabaseHandler.py) 拆到独立模块
- 对 UI 层暴露统一的领域方法，而不是继续暴露底层 JSON 读写细节

完成标志：

- UI 文件不再依赖一个超大 `DatabaseHandler`
- 各领域都能单独替换本地实现与远端实现

#### 工作包 C：桌面端本地数据从 JSON 过渡到 SQLite

目标：把 PC 客户端从“原型级本地文件存储”升级到“可同步、可迁移、可扩展”的本地数据层。

当前问题文件： [思忆集app/DatabaseHandler.py](../思忆集app/DatabaseHandler.py)

具体动作：

- 为桌面端新增本地 SQLite 数据库文件，承接笔记、个人任务、提醒、单词本、新闻收藏和翻译历史
- 保留 JSON 兼容迁移脚本或首次启动导入逻辑，避免现有数据直接失效
- 每类核心资源统一补齐 `id`、`created_at`、`updated_at`、`version`、`deleted_at`、`device_id`、`sync_state`
- 为提醒、收藏、阅读历史等高频场景预留索引与查询路径，避免 SQLite 只是把 JSON 原样搬进去

完成标志：

- 新增本地数据不再以 JSON 为主存
- 旧数据可以被导入，不需要手工重录

#### 工作包 D：个人知识管理域后端建模

目标：在 `automation-hub` 中新增与桌面端对应的独立资源模型，而不是误用现有自动化执行域。

后端入口： [automation-hub/api/main.py](../automation-hub/api/main.py)

禁止直接复用的现有模型：

- [automation-hub/api/models.py](../automation-hub/api/models.py)
- [automation-hub/api/routes/tasks.py](../automation-hub/api/routes/tasks.py)
- [automation-hub/api/schema.sql](../automation-hub/api/schema.sql)

建议新增的后端资源：

- `notes`
- `note_versions`
- `note_folders`
- `personal_tasks`
- `reminders`
- `wordbooks`
- `words`
- `saved_articles`
- `translation_history`
- `user_preferences`

具体动作：

- 在 `automation-hub/api` 下新增个人知识管理相关 router、service、model 模块
- 在 [automation-hub/api/main.py](../automation-hub/api/main.py) 注册新路由
- 保持与 `tools`、`tool-runs`、`approvals`、`audit` 平行，而不是塞进现有自动化任务路由
- 首批后端接口优先只覆盖笔记、个人任务、翻译代理和新闻源，不在第一批把所有客户端能力一次性搬完

完成标志：

- 后端已存在一套与 PC 客户端一一对应的资源接口
- 个人任务与自动化作业在表结构和 API 语义上完全分离

#### 工作包 E：身份、同步与远端仓储接入

目标：让思忆集 PC 客户端开始接入统一身份与远端数据，而不丢掉本地优先体验。

可直接复用的后端能力：

- [automation-hub/api/auth/router.py](../automation-hub/api/auth/router.py)
- [automation-hub/api/audit/router.py](../automation-hub/api/audit/router.py)

具体动作：

- 桌面端先接入登录与设备身份，不再以“只有本地匿名用户”为默认长期方案
- 为仓储层增加 remote repository，实现笔记或翻译等至少一类资源的远端读写
- 引入增量同步游标和冲突策略，优先覆盖笔记与个人任务
- 关键同步动作接入审计，保证后续跨端排障有证据链
- 同步实现必须支持断网恢复和本地脏数据重试，避免桌面端体验退化为“联网才能用”

完成标志：

- PC 客户端可在“本地优先 + 可同步”模式下运行
- 至少一类核心资源已经不是纯本地闭环

#### 工作包 F：高风险与第三方能力后端化

目标：把目前散落在桌面端、未来不适合继续留在客户端的能力迁回后端。

重点文件：

- [思忆集app/translate_service.py](../思忆集app/translate_service.py)
- [思忆集app/news_api_handler.py](../思忆集app/news_api_handler.py)
- [思忆集app/ui_news_dialog.py](../思忆集app/ui_news_dialog.py)
- [思忆集app/ui_translate_dialog.py](../思忆集app/ui_translate_dialog.py)

具体动作：

- 移除桌面端中第三方翻译密钥的直接使用，统一改为后端代理
- 将 RSS 抓取、清洗、缓存和分类逐步迁到后端定时或服务化入口
- 客户端保留展示、筛选、收藏、转笔记等交互能力
- 把当前同步网络调用逐步迁到后台线程或任务机制，避免新闻和翻译请求阻塞 UI

完成标志：

- 第三方密钥不再出现在桌面 UI 代码中
- 新闻与翻译成为可复用的服务端能力，而不是 PC 端独占逻辑

### 里程碑建议

如果按最小风险路径推进，建议把阶段 3 再细分为三个里程碑：

1. 里程碑 M1：完成工作包 A 和 B，先把 PC 原型从“继续恶化”变为“可收敛”
2. 里程碑 M2：完成工作包 C 和 D，建立本地 SQLite 与后端知识管理域骨架
3. 里程碑 M3：完成工作包 E 和 F，让 PC 端真正接上统一后端，并为 Android 端预留稳定协议

### M1 文件级执行清单

里程碑 M1 不追求功能扩张，只追求让当前桌面原型从“继续堆功能会越来越乱”变为“可以稳定重构”。建议按下面顺序执行。

#### 步骤 1：冻结入口与依赖注入方式

目标：先停止从主窗口继续向外扩散隐式共享状态。

- 文件： [思忆集app/思忆集test1.py](../思忆集app/思忆集test1.py)
- 文件： [思忆集app/ui_main_window.py](../思忆集app/ui_main_window.py)

动作：

- 在入口层新增统一上下文对象，例如 `AppContext`、`DesktopServices` 或同类命名
- 由入口创建上下文，再将上下文注入 `MainWindow`
- 停止由 [思忆集app/ui_main_window.py](../思忆集app/ui_main_window.py) 直接创建 `DatabaseHandler`
- 保留 `open_dialogs` 这种纯窗口级状态，但把 `db_handler`、`alarms` 等业务状态从主窗口字段中抽离

验收点：

- 新对话框打开时，不再默认通过 `parent_ref` 逐级查找业务依赖
- `MainWindow` 看起来像导航壳，而不是全局服务容器

#### 步骤 2：收口基础对话框基类

目标：统一所有窗口的依赖获取与基础行为，减少后续各个页面重复修补。

- 文件： [思忆集app/ui_base_dialogs.py](../思忆集app/ui_base_dialogs.py)

动作：

- 为 `BaseIndependentDialog` 增加统一访问上下文的方法，例如 `self.app_context`
- 避免后续子窗口继续直接依赖 `parent_ref` 上的业务字段
- 把窗口置顶、关闭回收、公共样式等保留在基类，把业务初始化排除在基类之外

验收点：

- 子窗口不需要知道主窗口内部有哪些属性才可工作
- 后续替换主窗口结构时，窗口基类无需继续改动

#### 步骤 3：优先清理任务模块的历史残留

目标：先解决当前最明显的行为不一致来源。

- 文件： [思忆集app/ui_tasks_dialog.py](../思忆集app/ui_tasks_dialog.py)

动作：

- 删除重复定义的 `load_tasks`、`add_task`、`toggle_task_completion` 等历史残留实现，只保留一套逻辑
- 清理旧的 `self.parent_ref.db` 路径，统一为新接口来源
- 把任务列表展示、任务编辑、提醒联动拆成清晰的小方法，避免继续堆大函数
- 让任务模块只依赖 `PersonalTaskRepository` 和 `ReminderService` 之类的抽象，不再直接依赖超大存储类

验收点：

- 任务模块的方法不再被同名后定义覆盖
- 任务新增、编辑、删除、完成切换行为可预测
- 任务模块可以单独作为第一个重构样板

#### 步骤 4：定义 M1 期的最小仓储接口

目标：先把 UI 层和存储层之间插入一层稳定边界，不等 SQLite 完全落地后再开始拆。

- 文件： [思忆集app/DatabaseHandler.py](../思忆集app/DatabaseHandler.py)
- 文件： [思忆集app/ui_tasks_dialog.py](../思忆集app/ui_tasks_dialog.py)
- 文件： [思忆集app/ui_notes_dialog.py](../思忆集app/ui_notes_dialog.py)
- 文件： [思忆集app/ui_alarm_dialog.py](../思忆集app/ui_alarm_dialog.py)

动作：

- 先定义最小接口：`NoteRepository`、`PersonalTaskRepository`、`ReminderRepository`、`SettingsRepository`
- 用适配方式让当前 `DatabaseHandler` 暂时实现这些接口，而不是一次性删光旧实现
- UI 改为依赖接口，不再依赖 `DatabaseHandler` 具体方法全集

验收点：

- 至少任务和设置模块已经不需要感知 `DatabaseHandler` 的全部方法
- 后续接 SQLite 或远端仓储时，不需要再改 UI 事件逻辑

#### 步骤 5：把提醒能力从共享列表中抽出来

目标：解决当前提醒能力只是挂在主窗口 `alarms` 列表上的临时方案。

- 文件： [思忆集app/ui_alarm_dialog.py](../思忆集app/ui_alarm_dialog.py)
- 文件： [思忆集app/ui_tasks_dialog.py](../思忆集app/ui_tasks_dialog.py)
- 文件： [思忆集app/ui_main_window.py](../思忆集app/ui_main_window.py)

动作：

- 引入 `ReminderService` 或最小提醒管理对象，替代 `parent_ref.alarms`
- 任务模块通过提醒服务注册提醒，而不是直接往主窗口列表 append
- 闹钟窗口只展示和编辑提醒，不再承担提醒数据的实际存放职责

验收点：

- 主窗口不再持有业务级提醒列表作为系统真源
- 任务和提醒之间的联动可以独立测试

#### 步骤 6：切分 DatabaseHandler 的第一刀

目标：不是一次性重写存储，而是先把最容易继续膨胀的职责抽出来。

- 文件： [思忆集app/DatabaseHandler.py](../思忆集app/DatabaseHandler.py)

动作：

- 第一优先拆出任务、设置、提醒相关逻辑，因为它们已经影响多个窗口
- 第二优先拆出笔记相关逻辑，因为它是后续知识管理域的核心
- 保留新闻、翻译、单词本逻辑暂时继续挂在旧实现中，但停止再往里加新职责

验收点：

- `DatabaseHandler` 体积开始下降，职责边界开始清晰
- 后续 M2 进入 SQLite 改造时，不需要在一个超大类上直接动大手术

#### 步骤 7：补 M1 最小验证路径

目标：保证 M1 不是只改结构、不验证行为。

- 文件： [思忆集app/ui_tasks_dialog.py](../思忆集app/ui_tasks_dialog.py)
- 文件： [思忆集app/ui_alarm_dialog.py](../思忆集app/ui_alarm_dialog.py)
- 文件： [思忆集app/ui_main_window.py](../思忆集app/ui_main_window.py)

建议最小验证链路：

1. 启动主窗口
2. 打开任务窗口并新增一条带提醒时间的任务
3. 关闭并重新打开任务窗口，确认任务仍存在
4. 打开提醒窗口，确认提醒通过服务层而不是主窗口临时列表展示
5. 编辑、完成、删除任务，确认行为不回退

当前固定回归入口：

- 脚本： [思忆集app/smoke_check.py](../思忆集app/smoke_check.py)
- 运行方式： `.venv3.11/Scripts/python.exe 思忆集app/smoke_check.py` 或 VS Code 任务 `思忆集app: smoke-check`
- 验证范围：主窗口创建、任务/提醒、笔记、新闻离线收藏、翻译历史、单词本仓储绑定

完成定义：

- 入口、基类、任务模块、提醒模块已经不再依赖脆弱的隐式共享状态
- M1 之后可以开始 M2，而不需要再反复回头修基础结构

## 阶段 4：平台产品化

### 目标

把“工程可用”提升为“团队可用、可维护、可运营”。

### 主要工作

- 统一平台级入口与常用运维视图
- 提升 approvals、audit、proposals、repos 的可运营性和可观测性
- 完善报告、指标、状态检查和日常维护工具
- 明确 API 与 CLI/任务入口之间的职责边界

### 交付物

- 更稳定的平台操作入口
- 更适合日常维护的状态与报表视图
- 更清晰的 API / Worker / 运维脚本职责划分

### 完成定义

- 日常运维不再依赖记忆零散命令
- 常见排障可以通过固定入口定位
- 平台具备被持续使用而不是一次性验证的条件

## 阶段 5：Agent 与 Intel 产品化

该阶段必须建立在前面四阶段稳定完成的前提上。

### 目标

在不破坏安全边界的前提下，逐步把 Agent、仓库理解和 Intel 能力产品化。

### 主要工作

- 正式化 Agent 的规划、执行、上下文持久化与多端接入能力
- 继续增强 repo search、上下文注入、影响范围分析和证据快照能力
- 推进 Intel 相关的 RSS 入库、日报、证据链和仪表盘
- 明确哪些 AI 能力只提供建议，哪些能力允许进入审批执行链路

### 交付物

- 受控的 Agent 工作流
- 更可解释的代码理解与检索入口
- 更稳定的 Intel 采集和展示能力

### 完成定义

- Agent 不是绕过平台约束的捷径，而是建立在平台约束上的受控能力
- 所有 AI 相关执行仍然可审计、可审批、可回滚
- 新能力的接入不会破坏现有质量门禁和运维路径

## 年度推进顺序建议

如果按 2026 年当前状态继续推进，建议顺序如下：

1. 先完成远端 live / live-worker 留档，关闭当前放行缺口。
2. 再固化生产基线与部署手册，降低环境差异带来的不确定性。
3. 然后完善安全工作流与产物治理，把平台变成真正可运营的底座。
4. 在此基础上推进思忆集 PC 客户端整合，建立个人知识管理域与统一后端边界。
5. 最后再把 Agent、Intel 和 Android 等多端能力推向产品化，而不是提前扩面。

## 参考项目对标分析与借鉴（2026-04-06 全量审视）

基于对 openclaw、nanobot、syncthing、七奈桌宠（Shimeji-EE）和 My-Neuro（小问桌宠文件）五个参考项目的深度调研，下面记录可直接为思忆集所用的模式和实施建议。

### 对标总览

| 领域 | 参考项目 | 可借鉴模式 | 思忆集适配方式 | 优先级 |
|------|--------|--------|--------------|------|
| 插件/扩展体系 | openclaw extensions/* | npm-packaged + local override | Python `importlib` + YAML manifest，先内部卡片插件再开放 | ⭐⭐⭐ |
| LLM 厂商适配 | nanobot providers/ | Native SDK + adapter pattern | `ai_service.py` fallback chain 进一步抽象为 Provider Protocol | ⭐⭐⭐ |
| 角色人格系统 | My-Neuro full-hub | 情绪向量 + 记忆层 + Live2D 驱动 | mood 向量扩展，字幕气泡驱动，QWebEngineView 嵌入 Live2D | ⭐⭐⭐ |
| 统一事件总线 | openclaw gateway + nanobot channels | 发布-订阅中心 | EventBus 统一广播用户动作，角色运行时订阅 | ⭐⭐⭐ |
| 同步一致性 | syncthing | "Safe → Secure → Easy → Automatic" | 同步引擎改动必须先证明不丢数据 | ⭐⭐ |
| 频道/入口抽象 | openclaw channels（23+ 渠道） | 不做多渠道平台，但用相同模式抽象入口 | "浏览器桥接 / 系统托盘 / 悬浮窗 / CLI" 统一 Channel 接口 | ⭐⭐ |
| 安全路径保护 | openclaw CODEOWNERS | 关键安全文件受限编辑 | 对 `action_guard.py`、`api_oauth_security.py` 加 CODEOWNERS | ⭐⭐ |
| 测试分层 | openclaw vitest scoped | 按模块分 scope 测试 | pytest markers + conftest 按层分组 | ⭐⭐ |

### 从 openclaw 借鉴

1. **Extension 架构**：核心保持精简，功能通过独立包发布。思忆集应以 Python `importlib` + YAML manifest 落地内部插件制，先验证接口稳定性，再开放外部扩展。
2. **Feature-based 文件组织**：按功能分组而非按类型分组，与 [app-client-structure-draft.md](app-client-structure-draft.md) 中的 `modules/` 设计方向一致。
3. **Build gates**：构建时强制 lint + type check + 无 `@ts-nocheck`，思忆集应在 PostToolUse hook 中补齐 ruff + mypy 检查。

### 从 nanobot 借鉴

1. **Provider 轻量适配**：新增 LLM 厂商只需 2 步。思忆集 `ai_service.py` 应进一步抽象为 `LLMProvider` Protocol，每个厂商实现 `complete()` 方法即可接入。
2. **Token-based 内存管理**：SubagentManager 应增加 token 预算控制，避免单次会话上下文无限膨胀。
3. **频道统一消息对象**：`Message` 标准化，思忆集事件总线中应统一 `Event` 数据结构。

### 从 syncthing 借鉴

1. **优先级排序**："Safe From Data Loss → Security Against Attackers → Easy to Use → Automatic → Universally Available"。思忆集同步引擎后续改动必须先证明不丢数据，再做易用性优化。
2. **离线优先**：已在执行（本地优先），需持续坚持。

### 从 My-Neuro 借鉴

1. **情绪向量存储**：`[valence, arousal, dominance]` 三维向量，思忆集角色运行时应在现有 mood 映射基础上扩展为情绪向量。
2. **记忆层结构**：事件记忆 + 情感权重，`assistant_autonomy.py` 的 MemoryLayer 已有雏形，需结构化为 `(timestamp, context, emotional_weight)` 格式。
3. **Live2D 集成**：已有 `NEURO_PET_LIVE2D_DIR` 引用，后续通过 QWebEngineView 嵌入而非引入整套 My-Neuro 平台。
4. **MCP 工具支持**：思忆集已有更完整的 tool_runs 审计链路，这是相比 My-Neuro 的优势所在。

### 2026-04 公开竞品补充结论

本轮额外补充了对 Raycast、Fabric、Mem、Readwise Reader、Limitless 的公开产品信息对标。结论不是“跟谁更像”，而是进一步收紧“该借什么、不该借什么”。

| 产品 | 当前可借鉴机制 | 当前不应照搬的方向 |
|------|--------------|------------------|
| Raycast | 统一入口、键盘优先、高频动作、常驻入口心智 | 扩展商店竞赛路线、平台先行的生态扩张 |
| Fabric | 知识库即上下文、多模态搜索、跨工具连接 | 过早平台化的 MCP/CLI/API 表面积 |
| Mem | 低摩擦 capture、自动组织建议、related context | 过度黑盒化的自动组织与不可解释推荐 |
| Readwise Reader | 收件箱 -> 阅读/整理 -> Daily Review 的复盘闭环 | 先扩来源广度、后补主线闭环的节奏 |
| Limitless | 数据导出、删除与权限边界意识 | 持续被动采集、强监控式产品路线 |

当前落地结论：

1. 思忆集应借 Raycast 的“统一入口”和 Readwise 的“收件箱闭环”，而不是先借它们的生态表面积。
2. 思忆集应借 Fabric 和 Mem 的“知识库即上下文”“低摩擦 capture”，但仍保持本地优先、可解释建议和可关闭主动性。
3. openclaw / nanobot 仍只借 runtime 结构，不借多渠道消息平台方向。

### 三波执行优先级建议

```text
┌─ 第一波：稳定基础（1-2 周）───────────────────────────┐
│ 1. 完成阶段 0 远端放行                                 │
│ 2. ui_main_window.py 首次拆分（提取工作台/事件路由）    │
│ 3. 建立统一 UI theme token 体系                        │
│ 4. 补齐仓储层 + OAuth 刷新的单元测试                   │
└─────────────────────────────────────────────────────┘
         ↓
┌─ 第二波：日常可用（2-4 周）───────────────────────────┐
│ 5. 新闻模块完善（筛选/检索/收藏/详情阅读体验）         │
│ 6. 笔记模块管理闭环（文件夹/标签/检索/归档）           │
│ 7. 同步体验统一（状态栏/冲突面板/自动同步反馈）        │
│ 8. 悬浮助手阶段 1（托盘 + 小型浮窗 + 恢复主窗口）      │
└─────────────────────────────────────────────────────┘
         ↓
┌─ 第三波：角色与平台（4-8 周）─────────────────────────┐
│ 9. 统一事件总线                                        │
│ 10. 字幕层 + Output Sink                               │
│ 11. 插件体系第一版（内部卡片插件）                     │
│ 12. 工作台可布局化                                     │
│ 13. 小问角色运行时整合                                 │
└─────────────────────────────────────────────────────┘
```

### 需要避免的陷阱

1. **不要做多渠道消息平台**——路线图已明确这不是思忆集的方向，保持克制。
2. **不要过早追求 Live2D 效果**——先把统一事件入口/字幕层/工具注册表做稳，再升级表现层。
3. **不要让 automation-hub 的 tasks/runs 表承接个人待办**——路线图已规避。
4. **不要把 `assistant_autonomy.py` 继续膨胀**——路线图建议拆分，应尽快执行。
5. **不要跳过测试直接堆功能**——当前最大风险不是功能不够，而是现有功能的可靠性不够。
6. **不要在 PowerShell 5.1 中用 Set-Content/Out-File 写含中文的 UTF-8 文件**——默认编码 GB2312 会损坏内容。

### 总判断

思忆集的规划质量和架构方向都很好。当前最大的杠杆不是加新功能，而是把已有骨架变稳固——先拆分过重文件、补齐测试、统一 UI，再推进角色化和平台化。路线图中"2026-04 执行顺序校正"的判断完全正确，坚持这个节奏即可。

## 当前优先文档入口

推进路线图时，优先参考以下文档：

- 架构总纲： [complete-architecture-design.md](complete-architecture-design.md)
- 仓库总览： [README.md](../README.md)
- 主应用说明： [automation-hub/README.md](../automation-hub/README.md)
- app 与底座分离设计： [app-platform-boundary-design.md](app-platform-boundary-design.md)
- 架构设计： [automation-hub/docs/design/ARCHITECTURE_DESIGN.md](../automation-hub/docs/design/ARCHITECTURE_DESIGN.md)
- 真实环境部署命令： [automation-hub/docs/real-env-deployment-commands.md](../automation-hub/docs/real-env-deployment-commands.md)
- 真实环境验收计划： [automation-hub/docs/remote-acceptance-plan.md](../automation-hub/docs/remote-acceptance-plan.md)
- 本地 Kubernetes 手册： [automation-hub/docs/local-k8s-runbook.md](../automation-hub/docs/local-k8s-runbook.md)
- Helm 部署说明： [helm-deployment.md](helm-deployment.md)
- 观测与治理： [observability.md](observability.md)、[token-management.md](token-management.md)

## 维护约定

- 本文档只维护阶段目标、优先级顺序和完成定义，不覆盖 [complete-architecture-design.md](complete-architecture-design.md) 的总纲裁决。
- 具体接口、部署步骤、验收命令和权限规则，继续维护在对应专题文档中。
- 如果某一阶段完成，应优先更新本文档和根 README，而不是新增新的阶段总结文件。
