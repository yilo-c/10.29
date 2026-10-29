# 离线回退与跨端会话续接 ADR（2026-05-04）

## 状态

已采纳。

## 背景

思忆集三端（桌面 Python、Android Kotlin、automation_hub 后端）在 AI 请求处理上存在以下未闭合的问题：

1. **桌面端 AI 调用无缓存**：每次复杂推理都走 Ollama/云端，网络闪断或模型 OOM 时直接抛错给用户。
2. **Android 端无离线兜底**：手机网络不稳定时，AI 请求直接失败，没有任何降级路径。
3. **跨端会话无法续接**：用户在桌面端讨论半天的主题，切到手机端后上下文完全丢失，需要从头重新描述。

三次任务依次解决了这三个问题。本文档记录每项方案的技术决策、实现路径和关键权衡。

---

## 任务 A：AI 响应缓存策略（桌面端 LocalDataStore）

### 问题

桌面端的 `AssistantAutonomy` 每次调用 AI 模型（本地 Ollama 或云端 API）都重新请求，对重复或相似查询（如"帮我分析这段代码"的连续编辑）浪费 token 和延迟。

### 方案：LocalDataStore TTL 缓存

```
用户输入 → 计算 SHA-256 hash → 查 LocalDataStore
  ├─ 命中且未过期 → 直接返回缓存结果（跳过 AI 调用）
  └─ 未命中或过期 → 调用 AI → 写入缓存 → 返回
```

### 关键决策

| 决策 | 选项 | 选择理由 |
|------|------|---------|
| 缓存键 | MD5 / SHA-256 / 全文索引 | SHA-256。防碰撞，不需查表，hash 即 key |
| 存储后端 | Redis / 内存 / LocalDataStore | LocalDataStore。桌面端已有 SQLite 基础设施，零额外依赖 |
| TTL | 固定 7d / 可配置 / 永不过期 | 固定 7d。AI 响应时效性敏感，7d 平衡命中率和新鲜度 |
| 逐出策略 | LRU / TTL-only / 容量上限 | TTL-only + 定时 purge。AI 缓存量级小（KB/条），7d 自然过期足够 |

### 实现要点

- `local_storage.py`：`get_cached_ai_response(key)` / `set_cached_ai_response(key, value, ttl=604800)`
- hash 计算在调用侧（`AssistantAutonomy._get_cached_or_fetch()`），存储层不感知 key 语义
- 缓存写入在 AI 成功返回后异步执行，不阻塞主流程
- 定时任务每小时 `purge_expired_cache()`，避免过期数据堆积

### 未采用方案

- **内存缓存**：App 重启后所有缓存丢失，桌面端常驻但也会更新重启。
- **Redis**：99% 的场景不需要独立缓存服务，运维成本 > 收益。

---

## 任务 B：离线 AI 回退（Android OfflineAiFallback）

### 问题

Android 端 `AssistantRuntime.process()` 在 `DESKTOP_LOCAL_AI` 和 `CLOUD_AI` 目标上没有任何降级。网络断开或服务端不可用 => 直接 `FAILED`。用户得到错误消息，却不知道请求已被排队稍后重试。

### 方案：三级降级链

```
AI 请求 → 主目标执行（desktop/cloud）
  ├─ 成功 → 缓存响应 → 返回 COMPLETED
  └─ 失败 ─→ 查 Room 响应缓存
       ├─ 有缓存 → 返回 OFFLINE_CACHE + 异步排队重试
       └─ 无缓存 → 返回 FAILED + 异步排队重试
```

### 关键决策

| 决策 | 选项 | 选择理由 |
|------|------|---------|
| 缓存后端 | Room / DataStore / 文件 | Room。与 PendingOperationDao 在同一数据库，事务一致 |
| 缓存键 | SHA-256 / 自增 ID | SHA-256。与桌面端一致，消息内容决定缓存命中 |
| TTL | 7d / 3d / 1d | 7d。移动端离线窗口可能较长（出差/地铁），7d 可覆盖 |
| 重试队列 | Room 表 / WorkManager | Room 表。思忆集已有 PendingOperation 系统，复用现有同步机制 |
| AiTarget 扩展 | 新增 OFFLINE_CACHE / 复用 QUEUED_PENDING | 新增。语义不同：OFFLINE_CACHE = 有缓存降级，QUEUED_PENDING = 排队等待 |

### Room 迁移 (v3→v4)

```sql
CREATE TABLE IF NOT EXISTS ai_response_cache (
    messageHash TEXT PRIMARY KEY,
    responseText TEXT NOT NULL,
    cacheTtlMs INTEGER NOT NULL DEFAULT 604800000,
    createdAt INTEGER NOT NULL
);
```

- `AiResponseCacheDao`：getByHash / insert (REPLACE) / deleteByHash / deleteExpired / clearAll
- `PendingOperationDao.insert()` 已存在，复用即可

### 降级路径数据流

```
AssistantRuntime.handleAiFailure()
  ├─ offlineFallback?.getCachedResponse(message)
  │   ├─ != null → AiTarget.OFFLINE_CACHE + queueForRetry()
  │   └─ = null  → AiTarget.FAILED + queueForRetry()
  └─ offlineFallback = null → 向后兼容，表现同旧版
```

- `queueForRetry()` 写入 `pending_operations` 表，`operationType = "ai_request_retry"`，`maxRetries = 5`
- 下次同步周期自动重试

### 测试覆盖

11 个单元测试覆盖：
- 缓存命中/未命中/过期
- 缓存写入与 TTL 验证
- 重试队列写入与 payload 结构
- 逐出/清空/计数
- 缓存键唯一性

---

## 任务 C：跨端会话续接（Session Continuation Protocol）

### 问题

桌面端和 Android 端各自维护独立的 `sessionId` 和对话上下文。用户在桌面端讨论"Python 装饰器"30 分钟后切换到手机：
- 手机不知道桌面端在聊什么
- 用户必须手动说"继续刚才的话题"
- 桌面端的上下文（消息数、待执行 action）完全丢失

### 方案：服务端协调的会话续接协议

```
桌面端 ──PUT /sessions/{id}──→ automation_hub ──GET /sessions/switch──→ Android 端
  保存当前会话状态                存储/查询                   检测到切换 → 提示用户
```

### 关键决策

| 决策 | 选项 | 选择理由 |
|------|------|---------|
| 状态存储 | 服务端 SQLite / Redis / 桌面端本地 | 服务端 SQLite。双端都需要访问，automation_hub 已有 SQLite 基础设施 |
| 检测机制 | 轮询 / 推送 / 请求时附带设备类型 | 请求时附带。Android 端发起 API 调用时携带 `device_type=android`，服务端对比上次交互设备 |
| 续接粒度 | 仅恢复 sessionId / 恢复全部上下文 | 仅恢复 sessionId + topic。完整消息历史由各端自行管理，服务端只存元数据 |
| UI 模式 | 自动切换 / 提示让用户选择 | 提示让用户选择。`ContinuationPromptCard` 以卡片形式展示，"继续"或"忽略" |

### API 设计

```
POST /sessions/switch
  Request:  {device_type: "android", current_session_id: "sess-1"}
  Response: {switch_detected: true, from_device: "desktop", to_device: "android",
             session_id: "desktop-sess-2", topic: "Python 学习",
             last_message: "帮我讲解装饰器", message_count: 5,
             pending_actions_count: 1, prompt: "你在 desktop 上正在讨论…"}

PUT /sessions/{session_id}
  Request:  {device_type: "desktop", topic: "Python 学习",
             last_message: "帮我讲解装饰器", message_count: 5}

GET /sessions?device_type=android
  Response: [{session_id, device_type, topic, last_message, message_count, updated_at}]
```

### 续接触发流程

```
Android WebSocket 连上（ChatViewModel.checkContinuation()）
  └─ SessionContinuationService.checkForSessionSwitch()
      ├─ 检测到切换 → ContinuationPromptCard 展示
      │   ├─ 用户点"继续" → acceptContinuation() → 设置 sessionId → 加载上下文
      │   └─ 用户点"忽略" → dismissContinuation() → 清除 prompt
      └─ 无切换 → 正常流程
```

### 桌面端适配

桌面端 `ConnectionManager._on_message()` 中已有的会话管理逻辑：
- 每次用户发送消息后自动调用 `PUT /sessions/{session_id}` 更新状态
- `CrossDeviceSessionManager` 负责检测来自其他设备的续接请求（已有实现）

### 测试覆盖

9 个单元测试覆盖：
- 切换检测（有/无切换、API 失败、null 响应）
- 保存会话（成功/失败）
- 获取会话列表（成功/失败/空列表）

---

## 三端职责矩阵

| 能力 | 桌面端 (Python) | Android (Kotlin) | automation_hub (Python) |
|------|-----------------|------------------|------------------------|
| AI 响应缓存 | LocalDataStore TTL | Room ai_response_cache | — |
| 离线降级 | 已有 AssistantAutonomy fallback | OfflineAiFallback (3 级链) | — |
| 重试队列 | 已有 CommandQueue | PendingOperation (复用) | — |
| 会话存储 | PUT /sessions/{id} (已实现) | — | POST/PUT/GET /sessions |
| 续接检测 | CrossDeviceSessionManager | SessionContinuationService | POST /sessions/switch |
| 续接 UI | — | ContinuationPromptCard | — |

## 后续优化方向

1. **缓存预热**：桌面端启动时预加载高频缓存到内存，减少首次 SQLite 读取延迟。
2. **离线缓存同步**：移动端离线缓存的响应可在桌面端复用，目前双端缓存独立。
3. **续接失败降级**：如果服务端 `/sessions/switch` 不可用，Android 端应静默降级（目前返回 false，已实现）。
4. **缓存压缩**：长响应（>10KB）可考虑压缩存储，减少 Room 磁盘占用。
5. **Analytics**：缓存命中率、续接成功率数据可用于调优 TTL 和检测触发时机。
