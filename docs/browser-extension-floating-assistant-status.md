# 浏览器扩展与悬浮辅助当前开发状态

> **⚠️ 2026-09-12 核验标注（superseded 部分）**：本文档写于 08-22，以下锚点已死，读到请以后继报告为准：
> ① §二"悬浮实现"引用的 `ui_main_window.py`/`ui/assistant/floating_chat_bubble.py`/`main_window_proactive.py`/`floating_handlers.py`/`assistant/message_renderer.py` **主线全部不存在**——悬浮真身=Electron 套（`desk-notes/packages/desktop/electron/` + `src/FloatingBubble/`），详见 `deliverables/floating-assistant-browser-bridge-unification-2026-09-12.md`；
> ② 桥端口 18765→实为 **18766**；扩展版本 0.3.0→现 **0.4.1**；pattern_actions"6 内置操作"→实 **13**（`services/pattern_actions.py`）；
> ③ 插件侧三断链（add-word 无路由 / BrowserAdapter token 误用 / capture 空 payload）现状与修复单见 `deliverables/browser-bridge-status-review-2026-09-11.md` §七；
> ④ 本地 ASR（faster-whisper）与截图 OCR 链已落地，本文"还缺什么"清单中相应项已过时。
> 本文"AI 开工边界"三节（可做范围/不应扩张方向）仍然有效，继续作为治理文档使用。

本文档用于收敛当前两条产品线的实际开发程度：

1. 浏览器扩展
2. 桌面端悬浮辅助

目标不是写长期规划，而是回答三个问题：

- 现在已经做到哪一步
- 当前还缺什么
- 接下来应该先补什么

## 文档职责与 AI 开工边界

- 本文档负责“浏览器扩展 + 悬浮辅助”两条线的当前成熟度判断、缺口排序和近阶段实施建议，不单独替代产品边界或客户端分层真源。
- 这两条线是否进入当前主线、做到什么范围，以 [next-phase-product-boundary.md](next-phase-product-boundary.md) 和 [app-platform-boundary-design.md](app-platform-boundary-design.md) 为准。
- 触及小问代理任务、动作治理、交付路由或能力市场时，必须同时对齐 [xiaowen-agent-layer-roadmap.md](xiaowen-agent-layer-roadmap.md) 与 [assistant-execution-framework.md](assistant-execution-framework.md)。

当前 AI 可直接按本文开工的范围，只限于：

- 结构化网页上下文采集与低风险桥接增强，例如 `contentKind`、`rootHint`、`mediaHints`、`actionHints`。
- 浏览器桥接的状态检测、失败诊断、恢复提示和最小设置页。
- 悬浮辅助作为桌面继续入口、状态反馈入口和高频动作入口的收口。

当前不应仅凭本文直接扩成的方向：

- 高权限浏览器产品、下载器或大规模站点规则集合。
- 脱离桌面主闭环单独扩张的浏览器内 AI 套件。
- 绕开主窗口/治理链路的独立自治悬浮助手运行时。

## 一、浏览器扩展

### 当前结论

当前已经达到可运行 MVP，不是空壳，也不只是界面原型。

它已经具备从浏览器页面采集上下文，到调用桌面端本地桥接，再到沉淀为收件、笔记、任务的完整最小链路。

但它还没有进入正式产品化阶段，当前更像一个内部可用的首版扩展。

### 已实现能力

1. 已有独立扩展目录与 Manifest V3 结构。
2. 已支持侧边栏形态，而不是只做浏览器按钮入口。
3. 已能读取当前页面标题、URL、选中文本和可见正文。
4. 已能请求桌面端本地桥接接口进行网页总结。
5. 已能把结果写入动态收件箱。
6. 已能把结果转成笔记。
7. 已能把结果转成任务。
8. 已有桌面端本地桥接服务状态检查能力。
9. 桌面端启动时会自动拉起浏览器桥接服务。
10. 已有桥接服务单元测试，说明并非纯演示链路。

### 关键落地点

- 扩展说明与当前能力入口：browser-extension/siyi-browser-assistant/README.md
- 扩展配置：browser-extension/siyi-browser-assistant/manifest.json
- 页面内容采集：browser-extension/siyi-browser-assistant/content-script.js
- 侧边栏交互：browser-extension/siyi-browser-assistant/sidepanel.js
- 桌面端桥接服务：思忆集app/browser_assistant_bridge.py
- 启动时自动拉起桥接：思忆集app/思忆集test1.py
- AppContext 注入桥接能力：思忆集app/app_context.py
- 桥接测试：思忆集app/tests/test_browser_assistant_bridge.py

### 当前缺口

1. 仍是手动加载的解压扩展，没有构建、打包、发布和版本治理链路。
2. 当前连接方式强依赖本机桌面端和本地回环地址，不具备远程设备访问能力。
3. 没有账号、权限分级、设备信任或更细的授权确认。
4. 没有更完整的失败恢复机制，例如桥接断开后的统一重试和引导。
5. 目前能力集中在网页总结与沉淀，尚未扩展到更完整的浏览器工作流。
6. 没有看到自动化安装说明、兼容性矩阵或浏览器端回归清单。

### 当前成熟度判断

- 研发阶段：已过原型期，处于内部可用 MVP
- 完成度判断：约 60%
- 产品属性：可演示、可自用，但还不是可长期维护的浏览器产品版本

### 下一步优先级

1. 先补连接失败、桌面端未启动、端口变化时的诊断和恢复提示。
2. 再补扩展设置页，至少允许配置桥接地址、显示连接状态、查看最近错误。
3. 再决定是否把能力从“网页总结器”升级为“浏览器工作流助手”，否则范围会散。

### 与当前产品主线的关系（补充）

浏览器扩展当前不应被当作独立产品线单独扩张，而应明确服务于思忆集的第一条主线：

1. 把当前网页稳定转成结构化上下文。
2. 把结构化上下文送入动态收件箱或“整理当前内容”动作。
3. 让桌面端继续决定是生成摘要、转笔记、转任务，还是沉淀到私人空间。

因此当前优先级不是“增加更多浏览器权限”，而是“让输入更稳、更结构化、更容易进入思忆集主闭环”。

### 当前项目的分阶段实施建议

在当前思忆集项目里，浏览器扩展不建议走“高权限下载解析器”路线，而应继续沿用“轻扩展采集 + 本地桥接处理”的方式演进。按现有代码结构，建议分三阶段推进。

#### 阶段 1：浏览器侧低风险增强

目标：在不改变当前权限模型的前提下，让扩展上报的页面上下文更有结构。

实施项：

1. 给页面采集结果增加 contentKind 字段，用于区分 article、video_page、document_page、image_page、mixed_page。
2. 在侧边栏 summarize 请求里一并上报 contentKind，而不是只上传标题、URL、选中文本和 pageText。
3. 保持当前 activeTab、tabs、storage、sidePanel 模型，不优先引入 downloads、fileSystem、webRequest。

建议落点：

- 页面内容采集：browser-extension/siyi-browser-assistant/content-script.js
- 侧边栏请求组装：browser-extension/siyi-browser-assistant/sidepanel.js

这一阶段的目的不是增加新功能表面，而是给桥接层提供更好的路由输入。

#### 阶段 2：桥接层按内容类型分流

目标：把当前“统一 summarize”升级为“按内容类型做差异化总结与动作建议”。

实施项：

1. 在 summarize 接口中按 contentKind 做提示词分流，至少先区分视频页、文档页、资讯页。
2. 在桥接返回中增加 actionHints，明确推荐沉淀到收件、笔记或任务。
3. 保持当前浏览器桥事件继续进入桌面角色运行时，复用现有字幕、tone 和 mood 映射，不单独再造一套反馈逻辑。

建议落点：

- 桌面端桥接服务：思忆集app/browser_assistant_bridge.py

这一阶段完成后，浏览器扩展会从“只会总结网页”变成“能感知内容类型并给出后续建议的浏览器入口”。

#### 阶段 3：站点增强与回退机制

目标：在保持通用提取能力的基础上，优先增强高价值站点，而不是一开始铺满所有平台。

实施项：

1. 对 3 到 5 个高价值站点补选择器增强，优先考虑 B站、知乎、微信公众号、CSDN、文库类页面。
2. 视频页重点提取标题、简介、字幕区、正文说明等可见内容，不把“破解音视频流”作为第一阶段目标。
3. 文档页重点提取可见正文容器、分页正文区、在线阅读主容器。
4. 当站点增强失败时，统一回退到通用正文提取，不依赖强耦合站点脚本作为唯一入口。

建议落点：

- 页面内容采集：browser-extension/siyi-browser-assistant/content-script.js
- 后续如需要，可再抽成独立站点适配模块，但不建议当前先拆复杂框架。

这一阶段的原则是“少量高价值增强 + 稳定回退”，而不是“大规模站点覆盖”。

### 为什么这样排优先级

原因很直接：

1. 当前项目已经有可运行的扩展、桥接和桌面事件流，最该补的是结构化输入，而不是重新造重型扩展。
2. contentKind 和 actionHints 能明显提升总结质量与沉淀建议命中率，投入小、回报快。
3. 站点增强如果先做过量，很容易把项目重新拉回“高维护脚本集合”，与当前思忆集的产品化方向相冲突。

### 对当前浏览器线的更新判断

基于以上实施顺序，浏览器扩展这条线的下一阶段目标可以明确调整为：

1. 先从“网页总结器”升级为“结构化网页上下文采集器”。
2. 再从“统一总结入口”升级为“按内容类型分流的浏览器工作流入口”。
3. 最后才做少量高价值站点增强，并始终保留通用正文提取回退。

### 可直接开工的代码级任务清单（需阶段门控）

本节只在满足当前阶段门槛时可直接执行，不单独绕过 [next-phase-product-boundary.md](next-phase-product-boundary.md) 和 [siyi-m0-m3-implementation-checklist.md](siyi-m0-m3-implementation-checklist.md) 的前置依赖。

真正开工前，至少先确认：

1. 当前需求仍属于“结构化网页上下文采集、低风险桥接增强或悬浮继续入口收口”，而不是高权限浏览器扩张。
2. 涉及收件、交付、桥接写回或统一任务语义时，实施清单里的对应工作包已经开放，而不是未来阶段预研项。
3. 若改动会触及小问事件流、动作治理或统一交付，需同步对齐 [xiaowen-agent-layer-roadmap.md](xiaowen-agent-layer-roadmap.md) 与 [assistant-execution-framework.md](assistant-execution-framework.md)。

为了避免后续实现时再次回到泛化讨论，这里把三阶段进一步落到文件、字段和函数级别。

#### 阶段 1 代码任务

文件：browser-extension/siyi-browser-assistant/content-script.js

建议新增：

1. 新增 detectContentKind(context) 或等价函数，根据标题、URL、DOM 结构和候选元素返回 contentKind。
2. 在 collectPageContext() 的返回结构中加入以下字段：
	- contentKind
	- readableSelector 或 rootHint
	- mediaHints，例如 subtitle、coverImage、hasVideo、hasDocumentContainer
3. 保持 collectPageContext() 仍然可以在站点增强失败时回退到通用正文提取。

建议判定规则先从低复杂度做起：

1. 页面存在 video 标签、播放器容器或视频站特征时，优先判为 video_page。
2. 页面存在在线文档容器、分页阅读容器或文库特征时，优先判为 document_page。
3. 页面正文块明显且长度足够时，判为 article。
4. 多种信号同时存在时，判为 mixed_page。

文件：browser-extension/siyi-browser-assistant/sidepanel.js

建议修改：

1. 在 buildSummaryPayload(context, mode) 中把 contentKind 一并传给桌面端。
2. 如果 content-script 后续返回 mediaHints，也应一并透传给 summarize。
3. 在 renderPageMeta(context) 中增加 contentKind 显示，便于调试当前识别结果。

建议测试：

1. 浏览器侧至少做手工回归清单，覆盖普通文章页、视频页、文档页。
2. 如果后续补扩展测试，可优先给 buildSummaryPayload 增加最小单元测试。

#### 阶段 2 代码任务

文件：思忆集app/browser_assistant_bridge.py

建议修改 summarize_page()：

1. 读取 payload 中的 contentKind、mediaHints、rootHint 等新增字段。
2. 在 summarize_page() 内增加一个轻量路由函数，例如：
	- _resolve_browser_summary_mode(payload)
	- _build_action_hints(content_kind, summary, key_points)
3. 对至少三类内容做差异化处理：
	- video_page：总结更偏“核心内容 + 可转任务项”
	- document_page：总结更偏“重点条目 + 可沉淀笔记”
	- article 或资讯页：总结更偏“主旨 + 推荐进入收件或收藏”
4. 在返回结构中新增 actionHints 顶层字段，而不只放在 meta 中。
5. 在 _queue_assistant_event("browser.summarized", ...) 时补入 contentKind 和 actionHints，便于角色运行时后续消费。

建议返回结构更新为至少包含：

1. summary
2. keyPoints
3. studySuggestions
4. contextSuggestions
5. actionHints
6. meta.contentKind
7. meta.warnings

建议测试：

文件：思忆集app/tests/test_browser_assistant_bridge.py

补三类最小覆盖：

1. summarize 接口能回传 contentKind。
2. 指定 contentKind=video_page 时会得到非空 actionHints。
3. browser.summarized 事件入队时包含 contentKind 或 actionHints 元数据。

#### 阶段 3 代码任务

文件：browser-extension/siyi-browser-assistant/content-script.js

建议增加“站点增强但可回退”的组织方式，不必第一步就拆太多文件。可以先在单文件内形成如下结构：

1. 通用提取函数
2. 站点增强函数映射
3. 统一回退逻辑

建议优先增强的站点信号：

1. B站：标题、简介、视频说明区、字幕容器、分区标签。
2. 知乎：回答正文、文章正文、问题标题。
3. 微信公众号：文章标题、作者区、正文容器。
4. CSDN：文章正文、代码块密集区、专栏标题。
5. 文库类页面：在线阅读正文容器、分页正文区、文档标题。

建议约束：

1. 站点增强函数只负责帮助 pickReadableRoot 或生成更优正文候选，不直接承担整条总结流程。
2. 任一站点选择器失效时，必须回退到当前已有的通用正文提取。
3. 不把某个站点脚本写成唯一入口，避免页面小改版就整站失效。

建议测试：

1. 至少保留一份人工回归站点清单。
2. 如果补自动化测试，优先做 DOM 片段级样本测试，而不是依赖真实网页在线环境。

#### 推荐实现顺序

为了降低返工，建议开发顺序固定为：

1. 先改 content-script.js 的 context 结构。
2. 再改 sidepanel.js 的 summarize payload。
3. 然后改 browser_assistant_bridge.py 的 summarize 路由与 actionHints。
4. 最后才做站点增强和桥接测试补充。

这样可以保证每一步都能在现有可运行链路上逐段验证，而不是一次性改完后集中排错。

#### 当前阶段不建议先做的事

1. 不优先走 downloads、fileSystem、webRequest 等更重权限路线。
2. 不把浏览器扩展做成音视频下载器、网页破解器或站点规则大合集。
3. 不脱离桌面端主闭环单独扩更多“浏览器内 AI 功能”，避免范围失控。

## 二、悬浮辅助

### 当前结论

当前已经达到桌面常驻入口级能力，不只是视觉原型。

它已经打通了托盘、悬浮圆球、小问面板、关闭主窗口后继续常驻、快速收件与快速整理等主链路。

但它目前仍然主要是主窗口的轻入口和陪伴层，不是独立自治的智能助手。

### 已实现能力

1. 已有系统托盘驻留。
2. 已有托盘菜单，可恢复主窗口、打开悬浮助手、打开小问面板和退出程序。
3. 主窗口关闭后不会直接退出，而是默认隐藏到托盘并拉起悬浮助手。
4. 已有悬浮圆球入口，支持显示、隐藏、置顶和拖动。
5. 已有小问面板，可直接输入内容并调用主窗口助手链路。
6. 已支持快速收件。
7. 已支持快速触发整理当前内容。
8. 已支持快捷打开任务、笔记、新闻、翻译等模块。
9. 已有桌宠式表现层，支持 idle、thinking、happy、alert 四种状态。
10. 已支持动画资源扫描、帧序列播放、呼吸动效和 idle 随机轮换。
11. 已支持右键菜单切换形象、显示主窗口、启动七奈桌宠和退出。
12. 文档中已经明确把它定位成思忆集工作台和个人云的常驻入口。
13. **已支持小问对话气泡可拖拽拉缩** — 右下角拖拽手柄，最小 240×200，最大不限。
14. **已支持建议操作面板** — 主动触发时（剪贴板/空闲/窗口切换/情绪）显示带有操作按钮的悬浮面板，按钮调用模式化操作引擎直接执行。
15. **已支持消息右键菜单** — 主聊天区和小问对话气泡均支持选中文本后右键呼出菜单：翻译、总结、润色、保存笔记、提取链接，零 LLM 调用。
16. **已支持模式化操作引擎** — 6 个内置操作（翻译/译英/总结/润色/保存笔记/提取链接），通过本地的 PatternActionRegistry 统一注册和执行，悬浮窗建议按钮和右键菜单共享同一入口。
17. **已支持低风险任务模式识别** — 助理在 LLM 调用前拦截”查看桌面””清理桌面”等低风险指令，直接执行文件扫描并返回分类报告，不再反问确认。

### 关键落地点

- 托盘入口与关闭行为：思忆集app/ui_main_window.py
- 悬浮助手打开入口：思忆集app/ui_main_window.py
- 小问面板实现：思忆集app/ui_main_window.py
- 桌宠式悬浮圆球实现：思忆集app/ui_main_window.py
- 产品现状说明：思忆集app/README.md
- 悬浮扩展协议预留：思忆集app/plugin_protocol.py
- 建议操作面板 + 拉缩：思忆集app/ui/assistant/floating_chat_bubble.py
- 模式化操作引擎：思忆集app/assistant/pattern_actions.py
- 右键菜单（主聊天）：思忆集app/ui/assistant/message_renderer.py
- 右键菜单（气泡）：思忆集app/ui/assistant/floating_chat_bubble.py
- 低风险任务拦截：思忆集app/ui/assistant/_tool_calls.py
- 主动触发接入 action_ids：思忆集app/ui/main_window_proactive.py
- 悬浮圆球 show_suggestion 桥接：思忆集app/ui/assistant/floating_handlers.py

### 当前缺口

1. 目前很多能力仍然通过调用主窗口方法完成，尚未形成独立的助手运行边界。
2. 虽然长期定位包含同步状态、冲突、最近继续事项和提醒，但当前展示能力还比较浅。
3. 插件协议已经定义了悬浮助手扩展点，但还看不到成型的插件化接入成果。
4. 缺少围绕悬浮辅助的专项测试，当前更多依赖人工体验验证。
5. 桌宠表现层已经比入口层更丰富，但核心高频工作流还没有完全产品化。
6. 还没有看到更细的吸边、透明度、尺寸、状态卡片等稳定交互配置。

### 当前成熟度判断

- 研发阶段：已过原型期，处于桌面常驻入口 MVP → 接近正式产品
- 完成度判断：约 80%（交互模式升级后提升约 10%）
- 产品属性：已具备稳定高频交互闭环（建议面板+右键菜单+模式化操作+低风险直接执行），但还不是独立成熟助手

### 下一步优先级

1. ~~先把悬浮辅助真正承接一类状态信息，例如同步状态或提醒摘要，避免它只剩快捷入口。~~ ✅ **已完成** — 建议操作面板已在剪贴板/空闲/窗口切换/情绪触发时展示，带一键操作按钮。
2. ~~再补一组稳定高频动作，例如一键记笔记、一键加任务、一键查看待处理同步冲突。~~ ✅ **已完成** — 模式化操作引擎提供翻译/总结/润色/保存笔记/提取链接，右键菜单和悬浮面板共用。
3. 继续扩展低风险任务模式识别（如”查看下载目录”、”列出 C 盘文件”、”检查磁盘空间”等）。
4. 再决定是否继续加强桌宠表现层；如果核心动作还没稳，不应优先加更重的角色化资源。

补充说明：悬浮辅助后续应优先成为”状态和继续入口”，而不是先继续加强外观复杂度。只有当同步状态、提醒摘要、最近继续事项和高频动作已经稳定承接后，角色化表现层投入才有意义。

## 三、综合判断

如果从“离日常可用还有多远”来排优先级：

1. 悬浮辅助更接近日常可用，因为它已经嵌进桌面主流程。
2. 浏览器扩展更接近一个明确边界的工具型 MVP，链路完整但产品面还窄。
3. 两者都已经不是概念阶段。
4. 两者都不建议立刻追求大而全，应该先补稳定性、状态反馈和高频闭环。

## 四、建议的实际推进顺序

1. 先把悬浮辅助补成真正可长期常驻的桌面入口，重点是状态承载和高频动作。
2. 再把浏览器扩展补成更稳的浏览器侧采集与沉淀工具，重点是连接诊断和设置页。
3. 等这两条线都稳定后，再考虑更重的插件化和角色化演进。