# 工具规范文档

## 工具定义

工具（Tool）是 Automation Hub 中可执行操作的基本单元。AI 和用户只能通过注册的工具来执行操作。

## 三条铁律

1. **只能执行 tool_id（白名单工具），不接受任意字符串命令**
2. **所有写操作必须可回滚（patch/版本化/备份）**
3. **所有动作必须可追溯（audit log）**

## 工具结构

```json
{
  "id": "backup_notes",
  "name": "备份笔记",
  "description": "将笔记目录备份到指定位置",
  "risk_level": "write",
  "executor": "docker",
  "args_schema": {
    "type": "object",
    "properties": {
      "source": {"type": "string"},
      "destination": {"type": "string"}
    },
    "required": ["source", "destination"]
  },
  "command": ["python", "/app/scripts/backup_notes.py"],
  "cwd": "/workspace",
  "timeout_sec": 300,
  "allowed_paths": ["/workspace/notes", "/data/backups"],
  "is_enabled": true
}
```

## 字段说明

### 基础字段

- **id**: 工具唯一标识符（只能包含字母、数字、下划线、短横线）
- **name**: 工具显示名称
- **description**: 工具功能描述

### 风险级别

- **read**: 只读操作（如查询、搜索）
- **exec_low**: 低风险执行（如格式化、测试）
- **exec_high**: 高风险执行（如部署、重启服务）**需要审批**
- **write**: 写入操作（如修改文件、数据库）**需要审批**

### 执行配置

- **executor**: 执行器类型
  - `host`: 在主机上直接执行
  - `docker`: 在 Docker 容器中执行（推荐）
  - `k8s_job`: 作为 Kubernetes Job 执行（未来支持）

- **command**: 执行命令数组，如 `["python", "script.py", "--flag"]`
- **cwd**: 工作目录
- **timeout_sec**: 超时时间（秒）
- **allowed_paths**: 允许访问的路径列表（安全控制）

### 参数验证

- **args_schema**: JSON Schema 格式的参数定义
  - 定义参数类型、是否必需、默认值等
  - 执行前自动验证参数合法性

## 注册工具示例

```bash
POST /tools
Authorization: Bearer <token>
Content-Type: application/json

{
  "id": "cleanup_dir",
  "name": "清理目录",
  "description": "清理临时文件和缓存",
  "risk_level": "write",
  "executor": "docker",
  "args_schema": {
    "type": "object",
    "properties": {
      "directory": {
        "type": "string",
        "description": "要清理的目录路径"
      },
      "pattern": {
        "type": "string",
        "default": "*.tmp",
        "description": "文件匹配模式"
      }
    },
    "required": ["directory"]
  },
  "command": ["python", "/app/scripts/cleanup_dir.py"],
  "timeout_sec": 60,
  "allowed_paths": ["/tmp", "/data/cache"]
}
```

## 工具执行流程

1. **请求验证**
   - Token 验证
   - 权限检查（tool:execute）
   - 工具是否启用

2. **策略评估**
   - 检查风险级别
   - 验证参数 Schema
   - 检查路径权限

3. **审批流程**（高风险操作）
   - 创建审批请求
   - 等待人工批准
   - 批准后才执行

4. **执行工具**
   - 选择执行器
   - 设置环境变量
   - 执行命令
   - 记录输出

5. **审计记录**
   - 记录执行者
   - 记录执行时间
   - 记录执行结果
   - 保存日志

## 最佳实践

### 工具命名

- 使用描述性的 ID：`backup_notes` 而非 `bn`
- 使用小写字母和下划线
- 避免使用通用名称（如 `run`、`execute`）

### 参数设计

- 使用清晰的参数名
- 提供默认值和描述
- 使用 JSON Schema 严格验证
- 避免接受任意代码或命令

### 安全考虑

- 高风险操作必须设置为 `exec_high` 或 `write`
- 使用 `allowed_paths` 限制文件访问
- 优先使用 `docker` 执行器实现隔离
- 避免在命令中直接拼接用户输入

### 版本管理

- 重要变更时创建版本记录
- 使用语义化版本号（如 1.0.0）
- 保留历史版本以便回滚

## 工具迁移

从旧的 scripts manifest 迁移到工具注册：

```python
# 旧方式（scripts/manifest.json）
{
  "backup_notes": {
    "cmd": "python /app/scripts/backup_notes.py"
  }
}

# 新方式（通过 API 注册）
POST /tools
{
  "id": "backup_notes",
  "name": "备份笔记",
  "command": ["python", "/app/scripts/backup_notes.py"],
  "risk_level": "write",
  "executor": "docker"
}
```

## 常见问题

**Q: 为什么不能直接执行 shell 命令？**  
A: 为了安全性和可追溯性。所有操作都必须通过注册的工具，这样可以：
- 强制进行参数验证
- 实施风险评估
- 记录审计日志
- 实现审批流程

**Q: 如何临时执行一个操作？**  
A: 创建一个临时工具，设置合适的风险级别。执行后可以禁用或删除。

**Q: 工具执行失败如何调试？**  
A: 
1. 查看 stdout/stderr 日志文件
2. 检查审计日志：`GET /audit?resource_id=<run_id>`
3. 验证参数是否符合 Schema
4. 检查 allowed_paths 限制
