# 决策：automation_hub personal 域单一来源（C3 收敛）

- 状态：已决策（2026-08-23）
- 决策人：项目唯一开发者（个人 agent 自评视角）
- 关联：评审报告 T0.2 / P0#2 / 架构约束 C3（实体契约单一来源）
- 来源：依据 `deliverables/思忆集全栈评审报告-2026-08-23.md` 第 8 节执行规划

## 1. 背景与问题

`automation_hub` 与桌面后端各自维护一套**可写**的个人数据存储：

- 桌面权威存储：`思忆集app/domain/models/`（13+ 实体）+ `思忆集app/data/` 下 repositories。
- Hub 个人存储：`automation_hub/api/routes/personal_*.py`（9 个路由文件）直接 `from automation_hub.api.db import (...)`（`personal_notes.py:3`）做自有 CRUD，写入 hub 独立数据库表。
- 桌面侧已有推送管道：`思忆集app/integrations/automation_hub_client.py`（`_ENTITY_ENDPOINTS` 注册 note/task/news/translation/wordbook/word 共 6 类）向 hub `/personal/*` 推送；`services/automation_hub_service.py` 为其封装。

**核心矛盾（C3 硬违反）**：同一批个人实体（notes/tasks/words/...）存在两个可写源。Hub 的 `personal_*.py` 端点本身可被非桌面调用方（直接 API、移动端配对流程）独立写入，存在双写漂移风险——桌面与 hub 副本可能静默分歧。

## 2. 决策

**桌面 repositories 为个人数据的唯一权威来源（single source of truth）。**
**automation_hub 的 personal_* 域降级为只读镜像（read-only mirror）**，仅接收桌面 `automation_hub_client` 同步推送写入，不再接受其他来源的写入。

移动端写路径必须**经桌面中转**：移动端 → 桌面 repositories →（可选）桌面同步推送至 hub 镜像。移动端不得直写 hub 个人库。

## 3. 理由

1. 现有管道已以"桌面推送"为主（`automation_hub_client.py` 存在且被契约测试 `test_automation_hub_client_contract.py` 覆盖），收敛到"桌面权威 + hub 镜像"改动最小、数据安全风险最低。
2. "hub 为权威源"需大改桌面写入方向与同步逻辑，易引入回归，对单人维护项目不划算。
3. 镜像模型保留了 hub 对移动端/配对场景的价值（读取个人数据），同时根除双写。

## 4. 影响与后续动作（落到 T1.6 收敛）

| 动作 | 说明 | 验收 |
|---|---|---|
| 4.1 封禁 hub 个人域非桌面写入 | 在 `personal_*.py` 写入路径加来源校验：仅接受桌面同步令牌/身份，否则拒绝（或整体改为只读 + 仅桌面推送能写） | hub 个人表不再被非桌面调用方变更 |
| 4.2 移动端写落地桌面 | 移动端配对/写入流程改为先写桌面 repositories，再触发同步推送 | 移动端写操作不在 hub db 产生独立记录 |
| 4.3 契约测试 | 新增/强化测试：断言 hub personal 表仅由桌面同步客户端变更 | 测试绿，反向写入被拒 |
| 4.4 文档同步 | 更新 `docs/automation-hub-disposition.md`，写明"桌面权威、hub 镜像" | 文档与代码一致 |

## 5. 不做的范围

- 不删除 hub：保留其作为镜像与移动端读取底座。
- 不做"hub 为权威源"方案（已否决）。
- 不在本决策内处理 hub 的 agent/approvals/audit 等非个人域（另案处理）。
