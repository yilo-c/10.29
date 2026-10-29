<!-- 模块说明：AI 联网扩展的产品行为、授权边界与阶段范围规则。 -->

# AI 联网扩展产品规则

本文档只回答产品规则问题：

当思忆集允许 AI 联网获取信息、调用外部工具、接入远端能力或扩展来源时，产品上允许到什么程度，不允许到什么程度。

本文档不替代：

- [ai-networked-extension-security-design.md](ai-networked-extension-security-design.md)：技术控制点与安全流程。
- [next-phase-product-boundary.md](next-phase-product-boundary.md)：当前阶段是否允许把某类联网扩展放进主线。
- [assistant-execution-framework.md](assistant-execution-framework.md)：统一任务对象、能力注册和执行治理。

## 当前定位

AI 联网扩展当前不是“开放插件市场”，也不是“让 AI 自己上网随便装东西”。

当前更准确的定位是：

受控的外部能力接入与信息补充层。

## 当前必须坚持的规则

1. 用户可感知。
2. 用户可关闭。
3. 来源可解释。
4. 权限可分级。
5. 结果可追踪。
6. 高风险动作必须继续经过治理链路。

## 当前允许进入主线的联网扩展

- 搜索补充
- 网页抓取补充
- 低风险内容理解
- 浏览器桥接带来的结构化上下文
- 受控 MCP 或 provider 作为能力来源

这些能力的共同特点是：

- 主要用于补充信息、形成建议、辅助整理
- 不默认直接改本地核心资产
- 不默认执行高风险系统动作

## 当前不允许直接进入主线的联网扩展

- 无授权安装外部模块
- 高权限网页下载器
- 脱离桌面主闭环的独立浏览器 AI 套件
- 无白名单的脚本执行
- 以联网扩展名义绕过本地动作治理

## 授权规则

至少分三层：

1. 仅获取信息
2. 获取信息并产生建议
3. 获取信息后触发受控动作

默认只开放前两层；第三层仍必须进入确认、审批或白名单链路。

## 来源规则

联网扩展必须能说明：

- 来源类型
- 是否可信
- 是否来自白名单
- 是否是用户显式添加
- 最近一次成功或失败状态

## 阶段规则

### 当前阶段

- 以结构化信息补充、网页上下文采集和受控 provider 接入为主
- 不做安装型市场
- 不做大规模来源生态

### 后续阶段

- 在统一 `capability registry` 稳定后，再考虑更丰富的扩展目录
- 在安全设计与回滚流程稳定后，再考虑安装、启停、升级和回退

## 与浏览器扩展的关系

浏览器扩展当前应被视为 AI 联网扩展体系中的一个受控输入入口，而不是一条独立产品主线。

它优先服务于：

`网页上下文采集 -> 收件 -> 整理 -> 建议`

而不是服务于：

`高权限抓取 -> 任意站点脚本集合 -> 浏览器内独立自治`

## 当前代码锚点说明

文档栈当前大量引用的桌面端锚点包括：

- `browser_assistant_bridge.py`
- `plugin_protocol.py`
- `assistant_capability_router.py`
- `assistant_planner.py`

这些文件当前在主线目录中未直接可见，可见源码锚点主要位于 `.publish_3_25_repo/4.19/思忆集app/`。

因此当前所有“联网扩展开工”都必须先区分：

- 是在补产品规则和能力边界
- 还是在真正修改主线代码

前者可直接按本文推进，后者必须先完成源码目录对齐。

## 与其他文档的关系

- 产品边界： [next-phase-product-boundary.md](next-phase-product-boundary.md)
- 浏览器与悬浮现状： [browser-extension-floating-assistant-status.md](browser-extension-floating-assistant-status.md)
- 执行框架： [assistant-execution-framework.md](assistant-execution-framework.md)
- 安全设计： [ai-networked-extension-security-design.md](ai-networked-extension-security-design.md)
