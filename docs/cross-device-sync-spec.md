# 思忆集跨设备同步与数据交换规范

> ## ⚠️ 更正声明（2026-08-17）
>
> 本节以下对同步域真源的定义**已被现实核实修正**，原文保留仅供历史追溯。新增说明：
>
> - **"complete-architecture-design 为同步唯一真源" —— 不充分。** 仓库实际存在 **两条并行同步主链**，其协议/加密各自独立实现，单份架构文档无法覆盖：
>   1. **云同步**（手机 ↔ Cloudflare Worker ↔ 桌面）：E2E 加密信封，真源 = 桌面 [sync/cloud_sync_client.py](../思忆集app/sync/cloud_sync_client.py) + 手机 [lib/cloudSync.ts](../思忆集手机端app/src/lib/cloudSync.ts)。
>   2. **LAN 直连**（手机 ↔ 桌面 app ws_server）：JSON-RPC WS，真源 = 桌面 [backend/ws_server.py](../思忆集app/backend/ws_server.py) + 手机 [lib/rpcClient.ts](../思忆集手机端app/src/lib/rpcClient.ts)。
> - **现行真源：**[docs/automation-hub-disposition.md](automation-hub-disposition.md)（新声明）第 4 节给出同步域真源表，取代本节"唯一架构文档"的说法。
>
> 请在改动前先阅读现行真源。

---

# 思忆集跨设备同步与数据交换规范

本文档定义思忆集桌面端与 Android 客户端之间的同步语义、数据交换格式、全局唯一标识策略和冲突解决原则。

> **与 complete-architecture-design.md 的关系：**
> 本文档是 [complete-architecture-design.md](complete-architecture-design.md)（v3.2，架构唯一真源）在跨设备同步维度的展开。
> 同步核心原则已从"桌面端唯一权威、手机端只读"升级为"双端自治 + 语义对象同步"。
> 手机端和桌面端都是自治节点，各自拥有本地数据库和本地执行能力；同步主链只传递结构化语义对象，不做数据库文件镜像。

---

## 1. 设计原则

| 原则 | 说明 |
|------|------|
| **双端自治** | 桌面端和手机端各自拥有本地数据库、本地模型入口和本地执行能力，不依赖对方即可完成核心工作流 |
| **语义对象同步** | 同步主链只传递结构化语义对象（notes、tasks、memory_entries 等），不做数据库文件镜像或整表复制 |
| **增量同步优先** | 每次同步只传输时间窗口内的变更，不进行全量传输 |
| **离线容忍** | 手机端和桌面端在离线状态下均可独立工作，重连后自动同步变更 |
| **可解释冲突** | 任何冲突都生成可读摘要，而非静默覆盖；富文本对象冲突时保留双版本由用户合并 |

---

## 2. 全局唯一标识策略

### 2.1 统一对象 ID 格式

所有跨设备实体使用统一的 64 字符 hex 标识：

```
{entity_type}_{timestamp_ms}_{random_16_hex}
```

示例：
- `note_20260428_a3f2b1c4d5e6f7a8`
- `task_20260428_b2c3d4e5f6a7b8c9`
- `device_20260428_c1d2e3f4a5b6c7d8`

### 2.3 双端生成规则

- `entity_type`：与 `siyi_model_contract.yaml` 中的对象类型名一致
- `timestamp_ms`：Unix 毫秒时间戳
- `random_16_hex`：`secrets.token_hex(8)`
- 双端均可生成合法 ID；`origin_device_id` 字段标识创建来源
- 手机端在离线状态下创建的实体也使用同一格式，不会因"等待桌面端下发 ID"而阻塞本地工作流

---

## 3. 同步通道定义

### 3.1 通道分层

```
┌─────────────────────────────────────────┐
│            手机端 (Android)               │
│  ┌─────────┐ ┌─────────┐ ┌───────────┐  │
│  │ 快捷收件 │ │ 设备状态 │ │ SyncCenter│  │
│  │ (中继)   │ │ (显示)   │ │ (手动刷新)│  │
│  └────┬────┘ └────┬────┘ └─────┬─────┘  │
│       │           │            │         │
├───────┼───────────┼────────────┼─────────┤
│       │   WebSocket / REST API  │         │
├───────┼───────────┼────────────┼─────────┤
│       │           │            │         │
│  ┌────┴───────────┴────────────┴─────┐  │
│  │         SyncService (桌面端)        │  │
│  │  - 变更检测                         │  │
│  │  - 增量生成                         │  │
│  │  - 冲突检测 & 摘要                  │  │
│  │  - 设备注册 & 配对                  │  │
│  └────────────────────────────────────┘  │
│            桌面端 (Windows)               │
└─────────────────────────────────────────┘
```

### 3.2 通道类型

| 通道 | 传输方向 | 协议 | 说明 |
|------|---------|------|------|
| **SyncPull** | 桌面→手机 | REST/WebSocket | 手机主动拉取变更或桌面端推送增量 |
| **QuickRelay** | 手机→桌面 | REST | 快捷收件中继：手机接收外部内容并转发到桌面端 inbox |
| **AckWrite** | 手机→桌面 | REST | 回执确认、已读标记等有限写回 |
| **DeviceHeartbeat** | 双向 | WebSocket | 设备在线状态、配对健康检查 |

---

## 4. 同步数据格式

### 4.1 SyncPull 响应格式

```json
{
  "sync_cursor": "2026-04-28T12:00:00.000Z",
  "since": "2026-04-28T10:00:00.000Z",
  "entities": {
    "note": [
      {
        "id": "note_20260428_a3f2b1c4d5e6f7a8",
        "type": "note",
        "status": "created",
        "data": {
          "title": "...",
          "body": "...",
          "tags": ["..."],
          "created_at": "2026-04-28T11:30:00.000Z",
          "updated_at": "2026-04-28T11:30:00.000Z"
        }
      }
    ],
    "task": [],
    "reminder": [],
    "device_summary": {
      "id": "device_20260428_c1d2e3f4a5b6c7d8",
      "status": "online",
      "last_seen": "2026-04-28T12:00:00.000Z"
    }
  },
  "conflict_summaries": []
}
```

### 4.2 QuickRelay 请求格式

```json
{
  "device_id": "device_20260428_c1d2e3f4a5b6c7d8",
  "relay_type": "clipboard_text",
  "payload": {
    "text": "...",
    "source_app": "com.tencent.mm",
    "captured_at": "2026-04-28T12:05:00.000Z"
  }
}
```

### 4.3 AckWrite 请求格式

```json
{
  "entity_type": "reminder",
  "entity_id": "reminder_20260428_d4e5f6a7b8c9d0e1",
  "ack_type": "read",
  "acked_at": "2026-04-28T12:10:00.000Z"
}
```

---

## 5. 冲突解决策略

### 5.1 冲突类型与处理

| 冲突场景 | 检测条件 | 解决策略 |
|---------|---------|---------|
| **并发修改** | 同一实体在双端同时修改，基于版本号（`base_version`）检测 | 保留双版本，生成冲突摘要通知用户合并；桌面端作为"冲突与恢复入口"提供合并界面 |
| **离线过期写** | 一端离线期间的写入已被另一端更新覆盖超过 30 分钟 | 丢弃过期写操作，推送最新版本给过期端，并生成冲突摘要 |
| **实体已删除** | 一端尝试写回已被另一端删除的实体 | 返回 410 Gone，写入端移除本地缓存 |
| **设备未配对** | 请求的 `device_id` 未在配对列表 | 返回 403，引导重新配对 |
| **追加型冲突** | execution_receipt、inbox_item 等 append_only 对象同时追加 | 全部保留，按 updated_at 排序，不做覆盖

### 5.2 冲突摘要格式

```json
{
  "conflict_id": "conflict_20260428_e5f6a7b8c9d0e1f2",
  "entity_type": "note",
  "entity_id": "note_20260428_a3f2b1c4d5e6f7a8",
  "reason": "concurrent_modification",
  "desktop_version": { "..." },
  "mobile_version": { "..." },
  "resolution": "desktop_wins",
  "generated_at": "2026-04-28T12:15:00.000Z"
}
```

---

## 6. 离线恢复流程

```
手机端检测网络断开
  ↓
标记所有待发送操作为 PENDING
  ↓
本地操作继续（读缓存，写操作入队）
  ↓
网络恢复事件触发
  ↓
1. 发送 DeviceHeartbeat 确认桌面端可达
2. 按时间顺序重放 PENDING 队列
3. 对每个操作检查返回码：
   - 200/201: 移除队列
   - 409: 接受冲突摘要，移除队列
   - 410: 移除队列 + 本地缓存
   - 403: 暂停重放，提示重新配对
4. 队列清空后执行 SyncPull 拉取最新变更
```

---

## 7. 数据量控制

| 约束项 | 限制值 | 说明 |
|--------|--------|------|
| 单次 SyncPull 最大实体数 | 200 | 超出时分页，使用 `sync_cursor` 续传 |
| 单次 QuickRelay 最大 payload | 64KB | 超出时拒绝并提示用户分段发送 |
| PENDING 队列最大长度 | 500 | 超出时暂停入队，提示用户连接桌面端 |
| 离线缓存最大条目数 | 5000 | 超出时按 LRU 淘汰，优先保留 starred 项 |

---

## 8. 设备配对流程

### 8.1 配对状态机

```
UNPAIRED → PAIRING → PAIRED → TRUSTED
                ↓         ↓
              EXPIRED ← SUSPENDED
```

### 8.2 配对步骤

1. 手机端生成临时 `pairing_code`（6 位数字，有效期 5 分钟）
2. 手机端通过局域网广播或二维码展示 `pairing_code`
3. 桌面端输入 `pairing_code` 发起配对请求
4. 桌面端生成 `device_id` 并注册设备指纹（Android ID + 设备型号）
5. 配对成功后，桌面端下发 `device_token`（JWT，有效期 30 天）
6. 后续所有请求携带 `Authorization: Bearer {device_token}`

### 8.3 安全约束

- 配对仅在局域网环境内允许（或通过用户手动输入桌面端 IP:Port）
- `pairing_code` 一次性使用，过期或使用后立即失效
- 设备令牌续期需桌面端显式确认
- 支持手动撤销设备授权

---

## 9. 同步状态模型（与 `siyi_model_contract.yaml` 对齐）

| 对象类型 | 同步状态 | 双端可见性 | 说明 |
|---------|---------|------------|------|
| `note` | `Implemented` | 完整读写 | 笔记实体当前已实现 Syncable，双端均可创建/编辑/删除 |
| `reminder` | `ReadOnly` | 只读 | 手机端可查看/确认，不可创建/修改 |
| `task` | `Transitional` | 摘要可见 | 仅可查看摘要，详细信息需在桌面端查看 |
| `device` | `Transitional` | 状态可见 | 手机端可查看配对设备状态 |
| `ingest` | `Implemented` | 中继写入 | 快捷收件通过 QuickRelay 转发，双端均可作为目标 |
| `chat` | `ReadOnly` | 只读 | 手机端可查看聊天历史，不可新建会话 |
| `briefing` | `Future` | 不可见 | 未进入当前矩阵，不在同步范围内 |
| `assistant_task` | `Future` | 不可见 | 未进入当前矩阵，不在同步范围内 |

---

## 10. 测试验证清单

| 测试场景 | 验证点 |
|---------|--------|
| 手机端首次配对→同步 | SyncPull 返回完整的当前数据快照 |
| 桌面端新增笔记→手机端拉取 | 增量同步仅传输新增笔记 |
| 手机端快捷收件→桌面端 inbox | QuickRelay 成功后，桌面端 inbox 出现新条目 |
| 手机端回执确认→桌面端状态更新 | AckWrite 成功后，桌面端对应实体已读状态更新 |
| 手机端离线→编辑→在线恢复 | PENDING 队列重放成功，冲突正确处理 |
| 桌面端删除实体→手机端拉取 | 手机端收到 410，本地缓存清除 |
| 并发修改冲突 | 桌面端覆盖，手机端收到冲突摘要通知 |
| 设备令牌过期 | 返回 401，手机端提示重新配对 |

---

## 11. 与现有文档的关系

- **统一对象合同**：[siyi_model_contract.yaml](../siyi_model_contract.yaml) — 定义所有可同步实体的字段和状态
- **Android 技术设计（可开发真源）**：[android-client-technical-design.md](android-client-technical-design.md) — 第 5 节定义 SyncCenter 和 Android 端同步入口
- **Android UI 布局（交互母版）**：[android-client-ui-layout.md](android-client-ui-layout.md) — 第 9-10 节定义同步状态在 UI 上的呈现方式
- **产品边界**：[next-phase-product-boundary.md](next-phase-product-boundary.md) — 约束当前阶段手机端只做合同冻结和同步语义
- **M0-M3 检查清单**：[siyi-m0-m3-implementation-checklist.md](siyi-m0-m3-implementation-checklist.md) — M2/M3 中的手机端同步相关验收项
