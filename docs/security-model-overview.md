# 安全模型总览

> **与 complete-architecture-design.md 的关系：**
> 本文档是 [complete-architecture-design.md](complete-architecture-design.md)（v3.2，架构唯一真源）在安全模型维度的展开。
> 风险策略已统一为 R1-R4 四档分级（读/草稿/可回滚写/不可逆副作用），并固定不可逆操作的红线规则。

> 本文档是思忆集安全体系的统一入口。安全相关设计此前散落在多份文档中——这里整合为一份完整的安全模型总览。

## 相关文档

| 专题 | 文档 |
|------|------|
| AI 联网扩展安全 | [ai-networked-extension-security-design.md](ai-networked-extension-security-design.md) |
| app/底座安全边界 | [app-platform-boundary-design.md](app-platform-boundary-design.md)（安全补充章节） |
| 自动化执行安全 | [../automation-hub/THREE_LAWS.md](../automation-hub/THREE_LAWS.md) |
| Token 治理 | [token-management.md](token-management.md) |
| 插件权限 | [plugin-sdk-spec.md](plugin-sdk-spec.md)（权限模型章节） |

---

## 1. 安全分层架构

```
┌──────────────────────────────────────────────────────────┐
│  第五层：AI 行为安全                                      │
│  ├─ 只允许白名单 tool_id 执行                             │
│  ├─ 写操作必须可回滚                                      │
│  ├─ 关键动作必须可审计                                    │
│  └─ LLM 建议 ≠ 直接执行（action_guard 网关）              │
├──────────────────────────────────────────────────────────┤
│  第四层：认证与授权 (AuthN / AuthZ)                       │
│  ├─ Token 体系（有效期、scope、轮换、撤销）                │
│  ├─ 设备身份认证                                          │
│  └─ 审批机制（高风险操作需人工审批）                       │
├──────────────────────────────────────────────────────────┤
│  第三层：数据保护                                         │
│  ├─ 四级数据分类（仅本地/可同步/可分享/可送模型）          │
│  ├─ 密钥管理（环境变量/Secret，绝不明文落盘）              │
│  └─ 数据导出与删除（用户完全控制）                         │
├──────────────────────────────────────────────────────────┤
│  第二层：网络安全                                         │
│  ├─ 本地 HTTP (127.0.0.1) — 桌面↔底座                    │
│  ├─ HTTPS + TLS — 远程通信                                │
│  └─ WebSocket (WSS) — 实时推送                            │
├──────────────────────────────────────────────────────────┤
│  第一层：代码与依赖安全                                    │
│  ├─ .gitignore 阻止敏感文件进入版本控制                    │
│  ├─ 依赖审计（已知漏洞检查）                               │
│  └─ Token/Secret 泄漏防护（Git history filter-branch）    │
└──────────────────────────────────────────────────────────┘
```

---

## 2. 认证层（AuthN）

### 2.1 Token 体系

```
Token 类型:
├─ admin_token     — 全权限，手动签发，用于初始化
├─ device_token    — 设备级，绑定 device_id，永不过期但可撤销
├─ agent_token     — Agent 节点专用，有限 scope
└─ personal_token  — 个人 API 访问（未来）

Token 属性:
├─ scope: 精细权限范围（如 approval:read, tool:execute）
├─ expires_at: 过期时间
├─ created_at / last_used_at: 用于审计
└─ status: active | revoked | expired
```

### 2.2 设备身份

- 首次配对时由 automation-hub 签发 `device_id` + `device_token`
- 设备 token 绑定设备指纹
- 撤销设备时所有关联 token 级联失效

### 2.3 现有实现

- Token 管理：`automation-hub/api/auth/tokens.py`
- Token 路由：`automation-hub/api/auth/router.py`
- 依赖注入：`automation-hub/api/auth/deps.py`
- Agent 节点认证：`automation-hub/api/auth/agent_node_deps.py`

---

## 3. 授权层（AuthZ）

### 3.1 Action Guard 网关

所有 AI 建议的副作用性操作必须经过 `action_guard.py`：

```
LLM 建议 action
  ├─ 不在白名单 → 拒绝
  ├─ 只读操作 → 放行
  ├─ 写操作 → 检查是否有回滚方案 → 放行/审批
  └─ 高风险操作 → 生成审批请求 → 用户确认 → 执行
```

### 3.2 审批机制

- 审批对象：`approval_request` (scope, status, requested_by, decided_by)
- 审批路由：`automation-hub/api/approvals/router.py`
- 审批审计：所有审批决策写入 audit 日志

### 3.3 Scope 体系

| Scope | 说明 |
|-------|------|
| `tool:execute` | 执行工具 |
| `tool:register` | 注册新工具 |
| `approval:read` | 查看审批列表 |
| `approval:decide` | 做出审批决定 |
| `audit:read` | 查看审计日志 |
| `sync:push` / `sync:pull` | 数据同步 |
| `device:manage` | 设备管理 |

### 3.4 R1-R4 风险策略矩阵

所有 AI 建议的操作按四档风险等级分类，与 `assistant-security-layer` 和 `Action Broker` 联动：

| 风险等级 | 操作类型 | 示例 | 执行策略 |
|---------|---------|------|---------|
| **R1_READ** | 只读查询、检索、摘要 | 知识库搜索、记忆查询、文件内容预览 | 自动执行，无需确认 |
| **R2_DRAFT** | 创建草稿、本地待确认对象 | 新建笔记草稿、翻译建议、收件箱暂存 | 可自动执行或会话级授权 |
| **R3_REVERSIBLE_WRITE** | 可回滚写入 | 任务状态更新、笔记编辑、配置修改 | 默认逐次确认 |
| **R4_IRREVERSIBLE_SIDE_EFFECT** | 不可逆外部副作用 | 批量删除、文件覆盖、API 调用、外发数据 | 禁止自动执行，需人工审批或跨端确认 |

约束：
- R3 操作必须绑定额外的 `rollback_hint`（回滚方式说明）
- R4 操作在桌面端需要显示审批请求面板，在手机端则转桌面端或 automation-hub 处理
- 所有 R3/R4 操作必须生成 `execution_receipt` 进入审计

### 3.5 不可逆操作红线

以下操作标记为"不可逆操作红线"，无论授权状态如何，必须满足额外条件：

| 红线操作 | 约束条件 | 审批要求 |
|---------|---------|---------|
| **批量删除**（≥5 条记录） | 必须先创建快照/备份，执行后生成删除报告 | 双端确认或桌面端显式确认 |
| **文件覆盖/删除** | 非用户工作目录下的文件需额外授权 | 逐次确认 + 版本备份 |
| **配置变更**（影响数据路径/安全设置） | 变更前自动快照，变更后生成回滚方案 | 桌面端确认 |
| **外发数据到第三方** | 仅允许 `model_egress_allowed` 级别的数据外发 | 每次外发前弹窗确认 |
| **插件安装/更新** | 安装前自动创建恢复点 | 用户显式授权，非 AI 推荐即装 |

这些红线规则在 `Action Guard` 和 `action_guard.py` 中实现，任何绕过红线的执行链路视为安全事件。

---

## 4. 数据保护层

### 4.1 四级分类

| 等级 | 说明 | 示例 | 存储 | 传输 |
|------|------|------|------|------|
| **仅本地** | 不离开设备 | 私密笔记、API 密钥、设备私钥 | 本地加密 | 不传输 |
| **可同步** | 设备间同步 | 笔记、任务、单词本 | 本地 + 服务端 | 加密传输 |
| **可分享** | 可分享给他人 | 公开笔记、分享链接 | 服务端 | HTTPS |
| **可送模型** | 可作为 LLM 上下文 | 偏好设置、非敏感记忆 | 服务端 | 厂商 API |

### 4.2 密钥管理

- 翻译密钥（有道）：环境变量 `SIYI_YOUDAO_APP_KEY`，次级 `settings.json`
- Hub API Token：环境变量 `SIYI_AUTOMATION_HUB_API_TOKEN`，次级 `settings.json`
- 生产环境：通过 Kubernetes Secret 注入，不使用明文文件
- Git 防护：已 filter-branch 清除历史泄漏，`.gitignore` 阻止再次提交

---

## 5. AI 联网扩展安全

详见 [ai-networked-extension-security-design.md](ai-networked-extension-security-design.md)，核心原则：

1. **安装 ≠ 启用**：AI 只能"推荐"扩展，不能"安装"
2. **manifest 最小权限**：每个扩展声明权限，用户显式授权
3. **来源可信**：首批仅允许内部签名的扩展
4. **可回滚**：每个扩展安装前自动创建恢复点

---

## 6. 审计层

### 6.1 审计事件

| 事件类型 | 记录内容 |
|---------|---------|
| 工具执行 | tool_id, params(脱敏), result_type, duration, executor |
| 审批决策 | approval_id, decision, decider, reason |
| 数据同步 | device_id, resource_type, action, envelope_count |
| 认证事件 | login, token_refresh, token_revoke, device_pair |
| 配置变更 | key, old_value_hash, new_value_hash, changer |

### 6.2 现有实现

- 审计模型：`automation-hub/api/models/audit.py`
- 审计路由：`automation-hub/api/audit/router.py`
- 审计存储：`automation-hub/api/db/migrations/004_audit.sql`

---

## 7. 安全检查清单（每次提交前）

- [ ] 无硬编码密钥（API keys, passwords, tokens）
- [ ] `.env` / `*.pem` / `*.key` 在 `.gitignore` 中
- [ ] 所有用户输入有验证
- [ ] 文件路径访问有路径遍历防护
- [ ] 第三方 API 调用使用 HTTPS
- [ ] 错误消息不泄漏敏感信息
- [ ] `action_guard` 检查覆盖了新工具注册
