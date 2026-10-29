# RBAC 权限控制文档

## 概述

Automation Hub 使用基于范围（Scope）的访问控制模型，所有 API 操作都需要相应的权限范围。

## 权限范围清单

### 工具权限

- `tool:read` - 查看工具列表和详情
- `tool:write` - 创建、更新、启用/禁用工具
- `tool:execute` - 执行工具

### 审批权限

- `approval:read` - 查看审批请求
- `approval:decide` - 批准或拒绝审批请求

### 审计权限

- `audit:read` - 查看审计日志

### 用户管理权限

- `user:admin` - 用户和设备管理

## Token 管理

### 创建 Token

```bash
POST /auth/tokens
Authorization: Bearer <existing-token>

{
  "device_id": "device-uuid",
  "scopes": ["tool:read", "tool:execute"],
  "expires_at": "2026-12-31T23:59:59Z"
}
```

### Token 最佳实践

1. **最小权限原则**：只授予必需的权限范围
2. **定期轮换**：设置合理的过期时间
3. **分设备管理**：每个设备使用独立的 token
4. **及时吊销**：不再使用的 token 应立即吊销

## 权限检查流程

1. 请求携带 `Authorization: Bearer <token>` 头
2. 系统验证 token 的有效性（未吊销、未过期）
3. 检查 token 的 scopes 是否包含所需权限
4. 通过检查后执行操作

## 安全建议

- 管理员 token 应妥善保管，仅在必要时使用
- 生产环境应启用 HTTPS
- 定期审计 token 使用情况
- 监控异常的 token 使用行为
