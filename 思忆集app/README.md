# 思忆集app 入口说明

思忆集app 当前定位是一个本地优先、可云端增强的个人记忆与工作台客户端，而不是继续并列堆叠零散模块的桌面原型集合。

本文件只负责桌面端产品入口、当前主线摘要和运行口径，不替代仓库级路线图和专题设计文档。

## 当前定位

- 主线是“收件 -> 整理 -> 建议 -> 继续入口”
- 桌面端承担重型工作台、高级控制台、冲突恢复和高风险执行节点
- 手机和桌面都可以自治，但长期任务、记忆、收件、执行回执和偏好必须回写同一套对象合同

## 当前阶段重点

当前优先级不是继续加新功能，而是：

1. 收口桌面端日用稳定性
2. 收口设置、启动链路和桌面 Doctor
3. 收口工作台基础容器和悬浮入口
4. 为后续小问统一任务入口与同步合同冻结打底

具体阶段门槛和工作包，以以下文档为准：

- [../docs/project-roadmap.md](../docs/project-roadmap.md)
- [../docs/next-phase-product-boundary.md](../docs/next-phase-product-boundary.md)
- [../docs/siyi-desktop-delivery-plan-2026-04.md](../docs/siyi-desktop-delivery-plan-2026-04.md)
- [../docs/consolidated-implementation-checklist.md](../docs/consolidated-implementation-checklist.md)

## 当前架构状态

桌面端源码已全部恢复到 [../思忆集app](.) 主线目录，并完成以下架构调整：

1. **子包子化** — 从 229 个扁平文件重构为按领域组织的子包（ai/, ui/, core/, adapters/, plugins/, workflow/），根目录仅保留 17 个 .py 文件（12 个待迁移 shim + 5 个实际文件）。
2. **巨型 UI 文件拆分** — `ui/main_window.py`（718 行）采用 12 mixin 架构，所有对话框已按领域独立。
3. **统一启动器** — `启动思忆集.bat` 支持 normal/debug 双模式。
4. **交互模式升级** — 右键菜单 + PatternActionRegistry（零 LLM 模式化操作）+ 悬浮建议面板 + 低风险任务直接执行。

## 当前产品模块

- 工作台：承接继续事项、状态摘要和高频入口
- 收件与整理：承接浏览器桥接、手动收件、当前内容整理
- 设置与 Doctor：承接配置治理、诊断、恢复与排障入口
- 小问入口：承接 chat / execute / automate 三类目标
- 同步与恢复：承接底座增强模式下的同步、冲突和回写
- 小问交互升级：右键菜单 + 模式化操作 + 建议面板 + 低风险直接执行

## 不在当前阶段优先做的事

- 不把桌面端做成多渠道消息平台
- 不先做复杂桌宠表现层
- 不先做开放插件市场
- 不把浏览器扩展扩成高权限抓取器

## 推荐阅读顺序

1. [../README.md](../README.md)
2. [../docs/project-roadmap.md](../docs/project-roadmap.md)
3. [../docs/next-phase-product-boundary.md](../docs/next-phase-product-boundary.md)
4. [../docs/siyi-desktop-delivery-plan-2026-04.md](../docs/siyi-desktop-delivery-plan-2026-04.md)
5. [../docs/app-platform-boundary-design.md](../docs/app-platform-boundary-design.md)
6. [../docs/app-client-structure-draft.md](../docs/app-client-structure-draft.md)

## 相关专题文档

- app 与底座分层： [../docs/app-platform-boundary-design.md](../docs/app-platform-boundary-design.md)
- 客户端结构草案： [../docs/app-client-structure-draft.md](../docs/app-client-structure-draft.md)
- 小问主路线： [../docs/xiaowen-agent-layer-roadmap.md](../docs/xiaowen-agent-layer-roadmap.md)
- 角色层边界： [../docs/xiaowen-character-assistant-plan.md](../docs/xiaowen-character-assistant-plan.md)
- 浏览器与悬浮现状： [../docs/browser-extension-floating-assistant-status.md](../docs/browser-extension-floating-assistant-status.md)
