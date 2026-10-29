<!-- 模块说明：API 使用说明。 -->

# API 文档

## Health 检查接口

### GET /health

检查系统健康状态。

**响应示例：**
```json
{
  "status": "healthy",
  "timestamp": "2026-01-15T13:29:26"
}
```

## 脚本管理接口

### GET /scripts/

获取所有可用脚本列表。

**响应示例：**
```json
[
  {
    "name": "fetch_rss.py",
    "description": "抓取RSS源",
    "parameters": [
      {
        "name": "url",
        "type": "string",
        "required": true,
        "description": "RSS URL地址"
      },
      {
        "name": "limit",
        "type": "integer",
        "required": false,
        "description": "抓取条目数量限制"
      }
    ]
  }
]
```

### POST /scripts/

注册新脚本。

**请求体示例：**
```json
{
  "name": "my_script.py",
  "description": "描述",
  "parameters": []
}
```

## 任务管理接口

### GET /tasks/

获取所有任务列表。

### POST /tasks/

创建新任务。

### GET /tasks/{task_id}

获取特定任务详情。

### PUT /tasks/{task_id}

更新特定任务。

### DELETE /tasks/{task_id}

删除特定任务。

## 运行管理接口

### GET /tool-runs/{run_id}/events

读取指定 tool-run 的事件流（stdout 片段、completed/failed 等）。

- 需要权限：`tool:read`
- 返回按 `created_at` 升序排列

### GET /tool-runs/{run_id}/artifacts

读取指定 tool-run 的产物元数据列表。

- 需要权限：`tool:read`
- 返回字段包含：`id`、`filename`、`size_bytes`、`content_type`、`created_at`

### GET /tool-runs/{run_id}/artifacts/{artifact_id}

读取单个产物元数据。

- 需要权限：`tool:read`
- 不返回产物二进制字段 `data`

### GET /tool-runs/{run_id}/artifacts/{artifact_id}/download

下载单个产物二进制内容。

- 需要权限：`tool:read`
- 可选查询参数：`disposition=attachment|inline`（默认 `attachment`）
- 响应头包含：`Content-Disposition`、`X-Artifact-Id`
- `Content-Type` 优先使用产物记录中的 `content_type`，缺省 `application/octet-stream`

### POST /tool-runs/{run_id}/retry

对一个已经进入终态的 tool-run 重新发起执行。

- 会复用原始 `tool_id` 与 `args`
- 若工具风险级别仍需审批，会重新创建新的审批请求
- 不会覆盖旧 run，而是生成一个新的 run 记录

请求体示例：

```json
{
  "reason": "retry after approval cancelled"
}
```

## 提案管理接口

### POST /proposals

现在支持可选 `repo_context` 字段，用于在 proposal 创建时把一次 repo 搜索结果固化为证据快照。

- 创建时会立即执行一次 repo 搜索
- 搜索结果会写入 proposal 产物目录下的 `repo_context.json`
- 响应体中的 `repo_context` 会返回本次固化的摘要
- 只有在传入 `repo_context` 时，才额外要求 `repo:read`

请求体示例：

```json
{
  "title": "Inspect config drift",
  "summary": "prepare proposal with repo evidence",
  "plan_md": "1. inspect repo\n2. draft patch",
  "patch_diff": null,
  "verify_tools": [],
  "risk_level": "write",
  "repo_context": {
    "repo_id": "repo-uuid",
    "query": "needle",
    "limit": 5,
    "offset": 0
  }
}
```

### POST /proposals/{proposal_id}/retry

对一个已经进入 `rejected` 状态的 proposal 显式重提交流程。

- 会把 proposal 状态重新推进到 `pending_approval`
- 会创建一条新的审批请求
- 审计中会记录 `proposal.retried`

请求体示例：

```json
{
  "reason": "retry after expiration"
}
```

## 仓库搜索接口

### GET /repos/{repo_id}/search

按字符串搜索仓库内容，当前会同时返回兼容字段 `matches` 与结构化字段 `items`。

- `engine`：`rg` 或 `python`，表示使用的搜索引擎
- `total`：命中的总条数，未分页前
- `matches`：兼容旧调用方的 `path:line:text` 字符串列表
- `items`：结构化匹配项，包含 `path`、`line`、`text`

响应示例：

```json
{
  "query": "needle",
  "engine": "rg",
  "total": 3,
  "limit": 2,
  "offset": 1,
  "matches": [
    "notes.txt:2:needle two",
    "notes.txt:3:needle three"
  ],
  "items": [
    {"path": "notes.txt", "line": 2, "text": "needle two"},
    {"path": "notes.txt", "line": 3, "text": "needle three"}
  ]
}
```

## Agent 上下文接口

### POST /agent/plan

现在支持在同一次规划请求中附带 `repo_context`，在真正生成计划前自动执行一次 repo 搜索并把结果写入 session 上下文。

请求体示例：

```json
{
  "query": "分析这段实现",
  "session_id": "optional-session-id",
  "repo_context": {
    "repo_id": "repo-uuid",
    "query": "needle",
    "limit": 5,
    "offset": 0
  }
}
```

说明：

- 不再需要先单独调用 `/agent/context/repo-search`
- 只有在传入 `repo_context` 时，才额外要求 `repo:read`
- 响应中的 `repo_context` 会返回本次自动注入的搜索摘要

### POST /agent/ask

与 `/agent/plan` 相同，`/agent/ask` 也支持在一次请求里通过 `repo_context` 自动检索并注入 repo 搜索上下文，然后再进入规划与执行流程。

### POST /agent/context/repo-search

将 repo 搜索结果注入到 agent session 上下文，供后续 `/agent/plan` 或 `/agent/ask` 使用。

- 会复用 `/repos/{repo_id}/search` 的搜索逻辑
- 会把命中的文件路径写入会话的 `recent_files`
- 会把最近一次搜索结果写入会话变量 `repo_search_last`
- 当调用方希望把“检索 + 规划”合并为一次请求时，也可以直接使用 `/agent/plan` 或 `/agent/ask` 的 `repo_context` 字段

请求体示例：

```json
{
  "repo_id": "repo-uuid",
  "query": "needle",
  "session_id": "optional-session-id",
  "limit": 5,
  "offset": 0
}
```

### GET /runs/

获取所有运行记录。

### POST /runs/

触发脚本运行。

**请求体示例：**
```json
{
  "script_name": "fetch_rss.py",
  "parameters": {
    "url": "https://example.com/rss",
    "limit": 10
  }
}
```

### GET /runs/{run_id}

获取特定运行记录详情。
# API（10 个最小接口）

> 启动后可访问 http://localhost:8000/docs 查看 OpenAPI 文档（交互式）。

1. `GET /health` 健康检查  
2. `GET /version` 版本信息  
3. `GET /scripts` 列出可用脚本（来自 `scripts/manifest.json`）  
4. `GET /scripts/{script_id}` 获取脚本详情  
5. `POST /tasks` 创建待办任务  
6. `GET /tasks` 列出任务（可选 `?status=todo`）  
7. `PATCH /tasks/{task_id}` 更新任务  
8. `DELETE /tasks/{task_id}` 删除任务  
9. `POST /runs` 触发脚本执行（异步入队列）  
10. `GET /runs` 列出运行记录（默认 50 条）  
11. `GET /runs/{run_id}` 获取运行详情（包含 stdout/stderr tail）  

> 注：第 11 个是为了“可用性”额外给的（你可以把它当成必选接口之一）。
