# 跨设备同步协议设计

> 本文档定义思忆集的跨设备同步协议。这是路线图「阶段 A：冻结长期资产合同」的核心输出物。
> 设计原则：本地优先、离线可用、增量同步、冲突可追溯。

## 参考对标

| 方案 | 借鉴点 |
|------|--------|
| **syncthing** | 设备发现、Block Exchange Protocol、cursor 增量 |
| **ElectricSQL** | Postgres ↔ SQLite 本地优先同步、Shape-based 订阅 |
| **Automerge / Yjs** | CRDT 无冲突数据类型、离线编辑自动合并 |
| **Notion** | 结构化数据同步、Block 级增量 |

---

## 1. Sync Envelope（同步邮包）JSON Schema

这是阶段 A 的核心交付物——所有跨设备数据交换的统一信封格式。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "SyncEnvelope",
  "type": "object",
  "required": [
    "envelope_id", "device_id", "resource_type", "resource_id",
    "action", "payload", "version", "cursor", "created_at", "checksum"
  ],
  "properties": {
    "envelope_id": {
      "type": "string",
      "description": "UUID v7，邮包唯一标识，含时间戳用于排序"
    },
    "device_id": {
      "type": "string",
      "description": "创建设备的唯一 ID，首次配对时由 automation-hub 签发"
    },
    "resource_type": {
      "type": "string",
      "enum": [
        "note", "personal_task", "inbox_item", "memory_entry",
        "execution_receipt", "user_preference", "device_record",
        "wordbook_entry", "translation_history", "saved_article",
        "sync_envelope"
      ]
    },
    "resource_id": {
      "type": "string",
      "description": "业务对象的全局唯一 ID"
    },
    "action": {
      "type": "string",
      "enum": ["create", "update", "delete", "undelete"],
      "description": "CRUD 操作类型。delete 为软删除（tombstone）"
    },
    "payload": {
      "type": "object",
      "description": "资源完整 JSON。delete 时可为 null"
    },
    "version": {
      "type": "integer",
      "description": "乐观锁版本号，每次 update 递增。用于冲突检测"
    },
    "cursor": {
      "type": "integer",
      "description": "设备级单调递增序号，用于增量拉取"
    },
    "created_at": {
      "type": "string",
      "format": "date-time",
      "description": "ISO 8601 UTC"
    },
    "checksum": {
      "type": "string",
      "description": "payload JSON 的 SHA-256 哈希，用于完整性校验"
    }
  }
}
```

---

## 2. Cursor 增量同步

### 2.1 机制

每台设备维护两类游标：

| 游标类型 | 作用 | 存储位置 |
|---------|------|---------|
| `device_cursor` | 本设备已发出的最后一个序号 | 本地 |
| `sync_cursor`（按设备） | 从每个对端设备已拉取到的序号 | 本地 |

```
设备 A                       automation-hub                      设备 B
  │                              │                                 │
  │── POST /sync/push ──────────►│                                 │
  │   envelopes [cursor: 1..5]   │                                 │
  │                              │── 存储 envelopes                │
  │                              │── 通知设备 B（WebSocket）        │
  │                              │                                 │
  │                              │◄── GET /sync/pull?device=A&since=0│
  │                              │── envelopes [cursor: 1..5] ──►│
  │                              │                                 │
  │                              │◄── GET /sync/pull?device=A&since=5│
  │                              │── 200 OK (空，无新数据) ────────►│
```

### 2.2 Pull 请求

```
GET /sync/pull?device={peer_device_id}&since={cursor}&resource_type={type}&limit=100

Response:
{
  "envelopes": [...],
  "latest_cursor": 42,
  "has_more": false
}
```

### 2.3 Push 请求

```
POST /sync/push
Body: { "envelopes": [...] }

Response:
{
  "accepted": 5,
  "rejected": 0,
  "rejected_details": []
}
```

---

## 3. 冲突策略矩阵

按资源类型选择不同的冲突解决策略：

| 资源类型 | 策略 | 说明 |
|---------|------|------|
| `note` | **LWW + 内容合并提示** | 基于 `updated_at` 最后写入胜出。若两端同时修改，保留两个版本并通知用户选择 |
| `personal_task` | **字段级合并** | 不同字段的修改自动合并；同字段冲突走 LWW |
| `inbox_item` | **LWW** | 收件箱条目简单，最后写入胜出 |
| `memory_entry` | **用户裁决** | 记忆冲突必须由用户确认，不自动覆盖 |
| `user_preference` | **LWW** | 偏好设置的语义是"最后一次设置生效" |
| `device_record` | **服务端权威** | 设备信息以 automation-hub 为准 |
| `wordbook_entry` | **LWW** | 单词条目简单 |

### 冲突检测机制

```
envelope.version < local.version → 冲突
envelope.version == local.version → 正常应用
envelope.version > local.version → 正常应用（本地落后）
```

---

## 4. 离线队列与重试

### 4.1 离线操作

- 离线时所有写操作正常执行，产生本地 envelope
- envelope 存入 `pending_operations` 队列表
- 恢复连接后按 cursor 顺序推送
- 推送成功前不删除本地 envelope

### 4.2 重试策略

```
最大重试次数: 5
退避算法: 指数退避 (1s → 2s → 4s → 8s → 16s)
超过最大重试: 标记为 failed，通知用户手动处理
```

### 4.3 队列优先级

| 优先级 | 资源类型 |
|--------|---------|
| 高 | `inbox_item`, `personal_task`（用户操作类） |
| 中 | `note`, `memory_entry`（内容类） |
| 低 | `user_preference`, `wordbook_entry`（配置/积累类） |

---

## 5. 设备配对

### 5.1 配对流程

```
第一阶段（当前）：
  桌面端生成配对 token → 用户在手机端输入 token → 互信建立

第二阶段（M3 后）：
  桌面端展示 QR 码 → 手机扫码 → 自动完成配对

降级通道：
  手动输入 token 始终可用
```

### 5.2 设备身份

```python
class DeviceRecord:
    device_id: str          # UUID，首次配对时签发
    device_name: str        # 用户可读名称
    device_type: str        # desktop | android | ios
    public_key: str         # 用于端到端签名验证
    paired_at: datetime
    last_seen_at: datetime
    sync_cursor: int        # 该设备最后推送的 cursor
    status: DeviceStatus    # active | revoked | offline
```

---

## 6. Tombstone 与清理

### 6.1 软删除

- `delete` action 不物理删除数据，写入 tombstone 标记
- tombstone 保留 30 天，之后由清理作业物理删除
- 30 天内可通过 `undelete` 恢复

### 6.2 清理策略

| 数据类型 | Tombstone TTL | 说明 |
|---------|--------------|------|
| note, task | 30 天 | 用户可能误删 |
| inbox_item | 7 天 | 收件箱条目生命周期短 |
| memory_entry | 90 天 | 记忆价值高，保留更久 |

---

## 7. 与 syncthing/CRDT 的对比选择

| 方案 | 优势 | 劣势 | 思忆集选择 |
|------|------|------|-----------|
| **syncthing (文件级)** | 成熟稳定、P2P | 不适合结构化数据增量 | 借鉴 cursor 和发现机制 |
| **CRDT (Automerge/Yjs)** | 无冲突、实时协作 | 内存占用高、实现复杂 | 当前不采用，后续可评估 |
| **自定义 Envelope** | 精确控制、轻量 | 需要自建冲突处理 | ✅ **当前方案** |
| **ElectricSQL** | 数据库级同步 | 依赖特定 ORM 版本 | 阶段 E 评估 |

选择自定义 Envelope 的理由：思忆集不需要实时协作编辑，结构化数据的 LWW + 按资源类型冲突策略已足够。CRDT 的复杂度在当前阶段不必要。

---

## 8. 自有同步：直接文件同步

### 8.1 设计原则

不依赖第三方同步服务。数据直接同步到用户指定的本地目录。用户完全掌控数据存储位置和同步行为。

### 8.2 同步目录结构

用户指定同步根目录（如 `D:\思忆集同步数据`）：

```
{同步根目录}/
├── 笔记/                    ← notes JSON/Markdown
│   └── {note_id}.md
├── 日记/                    ← diary entries
│   └── {yyyy-MM-dd}.md
├── 任务/                    ← tasks
│   └── {task_id}.json
├── 记忆/                    ← memory entries
│   └── {memory_id}.json
├── 图片/                    ← photos & attachments
│   └── {yyyy-MM}/
│       └── {attachment_id}.jpg
├── 单词本/                  ← wordbook exports
│   └── wordbook.json
├── 配置/                    ← preferences & settings
│   └── preferences.json
└── .sync/                   ← 同步元数据（隐藏）
    ├── cursor_state.json    # 设备级游标
    ├── checksum_manifest.json # 内容哈希清单
    └── conflict_log.json    # 冲突记录
```

### 8.3 同步模式

#### 模式 A：本地目录直写（默认）

- 桌面端直接读写同步目录中的文件
- 变更自动生成 sync_envelope → 推送至 automation-hub
- 其他设备通过 automation-hub 拉取变更
- 适用：桌面端作为主设备

#### 模式 B：API 代理同步

- 手机端通过 automation-hub API 同步
- automation-hub 将结构化数据写入同步目录
- 桌面端监听目录变更自动更新
- 适用：手机端、远程访问

### 8.4 文件级同步服务

```python
class FileSyncService:
    """自有文件同步服务——不依赖第三方同步工具"""

    def configure_sync_root(self, path: str):
        """设置同步根目录，自动创建子目录结构"""

    def write_resource(self, resource_type: str, resource_id: str, content: bytes):
        """将资源写入同步目录对应位置"""

    def read_resource(self, resource_type: str, resource_id: str) -> bytes:
        """从同步目录读取资源"""

    def watch_changes(self) -> AsyncIterator[FileChange]:
        """监听同步目录的文件变更事件"""

    def compute_checksum(self, file_path: str) -> str:
        """计算文件 SHA-256 哈希用于增量判断"""

    def resolve_conflict(self, local_path: str, remote_content: bytes) -> ConflictResult:
        """冲突解决：保留双方副本，通知用户选择"""
```

### 8.5 与 Sync Envelope 的关系

- **结构化数据**（notes, tasks, memory entries 等）：通过 sync_envelope JSON Schema 同步 → 同时写入同步目录
- **文件类数据**（图片、附件等）：直接文件复制 → 通过 content hash 判断增量
- **同步元数据**（cursor, checksum）：维护在 `.sync/` 隐藏目录中

### 8.6 优势

- 用户数据完全在自己掌控的目录中
- 可以用任何文件备份工具额外备份
- 即使 automation-hub 不可用，桌面端数据不丢失
- 迁移到新设备只需复制目录

---

## 9. 实现状态

| 组件 | 状态 | 说明 |
|------|------|------|
| sync_envelope Schema | ⬜ 待冻结 | 阶段 A 核心输出 |
| cursor 增量 | 🟡 原型 | Android 端已有 SyncService |
| 冲突策略 | ⬜ 待实现 | 当前无冲突处理 |
| 离线队列 | 🟡 原型 | PendingOperationManager 已有骨架 |
| 设备配对 | 🟡 原型 | Token 配对已有，QR 待实现 |
| WebSocket 推送 | ⬜ 待实现 | 阶段 C 计划 |
| 自有文件同步 | ⬜ 待实现 | 需新建 `file_sync_service.py` |
| 同步目录管理 | ⬜ 待实现 | 需新建 `sync_directory_manager.py` |

---

## 10. V2.1 水位模型处置（2026-08-27 登记，B-18）

> 上文 §1-9 为历史设计（syncthing/cursor 语义），现行云同步真源为
> `cloud_sync_client.py`（Cloudflare Worker 协议）。本节登记 V2.1 批次3
> 「server_watermark 统一水位模型」的处置结论，避免后人重新引入。

**决策：`sync/watermark.py`（WatermarkStore）已删除，显式不做「接线为真源」。**

依据：

1. **零消费方**：WatermarkStore（`sync_watermark.json`）全库仅 `_sync.py`
   `sync.reset_watermark` 逃生舱一处消费；生产游标真源是
   `CloudSyncClient._cursor`（内存）+ settings `cloud_sync_cursor`（持久层，
   `SyncBridge._save_cursor` 写回）。两套水位并存只会制造「改一个、另一个失效」。
2. **逃生舱失效根因**：旧 `sync.reset_watermark` 只写 WatermarkStore，运行中
   client 的 `_cursor` 不受影响（仅构造期读入）→ 返回 success 但水位没变，
   需重启才生效（B-18 实证）。
3. **修复**：逃生舱改调 `CloudSyncClient.reset_cursor()`（新增）——内存 + 持久层
   双清，运行中同步循环下次 pull 即以 since=0 全量重拉。旧 `sync_watermark.json`
   残留文件无害（无消费者），可手动删除。

现行游标语义（替代旧水位模型）：

| 项 | 语义 |
|---|---|
| 真源 | `CloudSyncClient._cursor`（内存）+ settings `cloud_sync_cursor`（持久） |
| 推进 | pull 仅按「成功解密 + 成功合并」条目推进；失败条目挡游标（P1-1） |
| 认证失败 | 不推进 + `sync.failed` fatal:true + `stop_sync_loop()`（11.1 矩阵「水位×认证」） |
| 逃生 | `sync.reset_watermark` → `client.reset_cursor()`（内存+持久双清，运行中生效） |
