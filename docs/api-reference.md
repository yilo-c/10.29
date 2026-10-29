<!-- 模块说明：API 参考说明文档。 -->

# API 参考文档

## /tool-runs - 工具运行（推荐）

说明：当前 API 默认启用鉴权；请求需要 `Authorization: Bearer <token>`。

`/runs` 为兼容层（Legacy），建议逐步迁移到 `/tool-runs`。

### GET /tool-runs
获取工具运行记录列表。

**查询参数**（示例，具体以 `/docs` 为准）：
- `tool_id`: 工具过滤
- `status`: 状态（queued, running, succeeded, failed）

**响应示例**：
```json
[
  {
    "run_id": "uuid",
    "tool_id": "fetch_rss",
    "status": "succeeded",
    "created_at": "2026-01-23T09:00:00",
    "finished_at": "2026-01-23T09:00:05",
    "exit_code": 0
  }
]
```

### POST /tool-runs
触发工具执行（必要时会进入审批）。

**请求体**：
```json
{
  "tool_id": "fetch_rss",
  "args": {
    "url": "https://www.people.com.cn/rss/politics.xml",
    "limit": 10
  }
}

### GET /tool-runs/{run_id}/events
读取指定 run 的事件流（按时间升序）。

**说明**：需要 `tool:read`。

**响应示例**：
```json
[
  {
    "id": "evt-uuid",
    "run_id": "run-uuid",
    "event_type": "output",
    "data": "hello",
    "created_at": "2026-03-25T08:00:00Z"
  }
]
```

### GET /tool-runs/{run_id}/artifacts
读取指定 run 的产物元数据列表。

**说明**：需要 `tool:read`。

### GET /tool-runs/{run_id}/artifacts/{artifact_id}
读取单个产物元数据（不返回二进制内容）。

**说明**：需要 `tool:read`。

### GET /tool-runs/{run_id}/artifacts/{artifact_id}/download
下载单个产物二进制内容。

**查询参数**：
- `disposition`: `attachment|inline`，默认 `attachment`

**说明**：
- 需要 `tool:read`
- 返回真实文件流，`Content-Type` 来自产物 `content_type`，缺省为 `application/octet-stream`

```

**响应**：
```json
{
  "run_id": "uuid",
  "tool_id": "fetch_rss",
  "status": "queued",
  "approval_id": null
}
```

## /runs - 兼容层（Legacy）

### POST /runs
（Legacy）触发脚本执行（兼容层）。

推荐使用：`POST /tool-runs`。

**请求体**：
```json
{
  "tool_id": "string",
  "args": {}
}
```

**响应**：
```json
{
  "run_id": 1,
  "status": "queued"
}
```