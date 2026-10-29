<!-- 模块说明：小问的可扩展执行框架、能力注册和治理边界。 -->

# 小问可扩展执行框架设计

> **与 complete-architecture-design.md 的关系：**
> 本文档专注"执行框架与能力治理边界"，是 [complete-architecture-design.md](complete-architecture-design.md)（v3.2，架构唯一真源）在统一执行主链维度的展开。
> 核心概念（assistant_task、Action Broker、Capability Registry、execution_receipt、Model Gateway）均继承自架构总纲第 11 章，本文档细化执行对象模型、注册协议和分层约束。

本文档用于把“小问代理层”和“下层能力市场”的实现边界进一步细化成执行框架。

它不重复 [xiaowen-agent-layer-roadmap.md](xiaowen-agent-layer-roadmap.md) 的产品路线，而是回答实现层问题：

1. 统一任务对象应该长什么样。
2. planner、router、plugin provider 各自负责什么。
3. 动态能力注册表怎样替代当前分散能力目录。
4. 风险分级、授权、审计怎样穿过整条执行链。

## 范围定义

执行框架的最小闭环固定为：

`用户目标 -> 任务对象 -> 规划 -> 能力选择 -> 执行 -> 交付 -> 记忆`

当前先覆盖：

- 本地内建能力
- 插件 provider
- MCP/外部 provider 的统一注册入口

当前不覆盖：

- 开放第三方插件市场
- 无边界脚本执行
- 绕过动作治理的直连执行

## 当前代码锚点

- 规划层：[../思忆集app/assistant_planner.py](../思忆集app/assistant_planner.py)
- 执行路由：[../思忆集app/assistant_capability_router.py](../思忆集app/assistant_capability_router.py)
- 安全层：[../思忆集app/assistant_security_layer.py](../思忆集app/assistant_security_layer.py)
- 扩展协议：[../思忆集app/plugin_protocol.py](../思忆集app/plugin_protocol.py)

## 核心对象模型

第一阶段固定四个对象：

1. `assistant_task`
2. `capability_spec`
3. `execution_result`
4. `delivery_route`

约束：

- planner 只输出任务与候选能力，不直接做高风险执行
- router 只执行能力，不负责用户画像推理
- security layer 统一做风险、授权、审计
- UI 不绕过这些对象直接调底层服务

## 动态能力注册表

后续把现有静态能力目录收口到统一 registry，至少维护：

- `capability_id`
- source
- risk_level
- health_state
- required_permissions
- delivery_support

第一阶段目标不是完全替换现有 router，而是先形成单一真源。

## 2026-04 补充：稳定上层合同优先于具体实现

如果执行框架要长期适应模型、终端和运行环境的更迭，业务层必须优先依赖稳定合同，而不是依赖当前时代最顺手的 SDK 或 provider 线程模型。

执行框架长期固定五类上层合同：

1. `Memory API`：承接短期记忆、长期记忆、执行记忆、偏好引用和可检索证据。
2. `Task API`：承接任务对象、状态机、交付路由、工单引用和跨端继续。
3. `Action Broker`：承接本机动作、白名单权限、必要确认和执行回执。
4. `Capability Registry`：承接能力目录、来源、风险、健康和权限要求。
5. `Model Gateway`：承接模型路由、provider 选择、成本/隐私策略和回退逻辑。

执行层约束进一步固定为：

- planner、router、security layer 只能依赖上述合同，不直接把厂商 thread id、SDK 返回对象或 provider 特有字段写成业务主模型。
- `execution_result` 必须能回写任务状态、执行历史和审计证据，而不是只返回一段自然语言文本。
- 本机动作与云端推理分离：模型可以给出意图与计划，但执行必须通过 `Action Broker` 或受控 capability 才能落地。
- 当手机直连第三方模型作为加速模式存在时，也只是替换 `Model Gateway` 的一个 provider 来源，不改 `Task API`、`Memory API` 和 `Action Broker` 的契约。

换句话说，执行框架真正要冻结的不是“今天接哪个模型”，而是“任务怎样表达、能力怎样注册、动作怎样落地、结果怎样写回”。

## 2026-04-20 补充：双端自治下的执行约束

在“手机优先、桌面增强、双端自治”的新定位下，执行框架还必须固定下面几条约束：

- 手机、桌面和后端代理都可以创建 `assistant_task`，但必须写回同一套 `Task API` 状态机。
- 双端都允许运行本地 planner、router 和 `Model Gateway`，但长期记忆、收件箱、执行回执和偏好更新必须走统一 writeback 链路。
- `delivery_route` 和 `execution_result` 必须天然支持跨端继续，而不是默认绑定某个单一终端会话。
- provider thread id、SDK session id、临时 planner token 和本地缓存 key 都不能直接承担 `assistant_task`、`memory_entry` 或 `execution_receipt` 的主键职责。
- 当手机直连第三方模型存在时，也只能视为 `Model Gateway` 的一个 provider 来源；任务完成的判断标准仍然是统一对象已写回、可被桌面和底座继续使用。

## 执行治理边界

必须固定三条硬约束：

1. 所有高风险动作继续经过动作治理和安全层
2. 任何 provider 都不能直接拿到窗口对象和敏感配置
3. planner、router、registry 三层不相互越权

## 分阶段执行顺序

1. 定义统一任务对象与 registry 读模型
2. 把内建能力先接入 registry
3. 让 plugin provider 与 MCP provider 走同一注册协议
4. 再逐步把 planner 从硬编码能力目录切到 registry

## 当前阶段完成定义

- 新能力先注册再可用，而不是先散落到主窗口
- planner 至少能读取统一能力视图，而不是只看硬编码列表
- provider 的风险、健康和来源能被统一查看
- 同一任务对象可被聊天入口、规则中心和定时入口复用