# 决策：行为采集范围（C5 处置）

- 状态：已决策（2026-08-23）
- 决策人：项目唯一开发者（个人 agent 自评视角）
- 关联：评审报告 T0.3 / P1#3 / 架构约束 C5（行为隐私）
- 来源：依据 `deliverables/思忆集全栈评审报告-2026-08-23.md` 第 8 节执行规划

## 1. 背景与问题

评审发现：在**活跃前端（Electron）**行为采集是盲区。

- 行为事件生产者均在 Python 后端：`ai/client/*`、`assistant/judge`、`core/browser_bridge`、`core/file_watcher`、MCP 工具运行器等。
- 唯一能采"用户在 App 内 UI 活动"的 `思忆集app/desktop/app_state_monitor.py` 是 **PyQt6 专用**：`if QApplication is None: ... app state monitor disabled`，在 headless/Electron 模式下被禁用。
- Electron 前端**零行为事件上报**：`desk-notes/packages/desktop/src` 中 `behavior` 引用几乎全是 `scrollIntoView`，无行为 RPC；`backend/handlers/` 下无任何 `_behavior.py`。

即：后端能记系统/代理/文件/浏览器行为，但**记不到用户在 Electron 应用内的 UI 活动**。若视为"缺失"，便是沉默盲区。

## 2. 决策

**将行为采集范围显式定义为：系统 / 代理 / 文件 / 浏览器 事件；不采集 App 内 UI 遥测（焦点、活跃模块、空闲等）。**

这是一项**刻意的设计决策**，而非能力缺口。C5（行为隐私/最小化采集）以"不收集 UI 行为"得到显式满足。

## 3. 理由

1. 单人个人项目，UI 遥测对"第二大脑"的边际价值低，而建设前端采集 + 后端 RPC + 隐私分级 UX 的成本不低。
2. 后端已覆盖系统/代理/文件/浏览器四类行为，足以支撑记忆与上下文推断。
3. 把"不采集"写成明文决策，消除评审指出的"沉默盲区"——将来若确有需要（如基于使用习惯的主动建议），再按选项 (a) 升级。

## 4. 影响与后续动作

| 动作 | 说明 | 验收 |
|---|---|---|
| 4.1 文档化 | 在行为日志设计说明（或 AGENTS 相关章节）写明采集范围与不采 UI 遥测的决策 | 文档可 30 秒判明范围，无歧义 |
| 4.2 清理误导代码 | `desktop/app_state_monitor.py`（PyQt6 专用、headless 禁用）归档或加注释标明"遗留、不用于活跃前端"，避免暗示 UI 采集在生效 | 不再让人误以为 UI 行为被采集 |
| 4.3 预留升级路径 | 若未来需完整 UI 行为事实：新增 `backend/handlers/_behavior.py`（`behavior.log` RPC）+ Electron 前端事件发射器；当前不实施 | 升级有据可循 |

## 5. 不做的范围

- 不在本决策内实现 Electron 行为上报链路（选项 (a) 留作后续增强）。
- 不改变后端既有行为生产者（系统/代理/文件/浏览器事件保持采集）。
