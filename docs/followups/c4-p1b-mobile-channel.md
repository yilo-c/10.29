# C4-P1b 跟进计划：手机端 channel 配套

> 状态：**待办**（P1 后端 helper 已完成 commit `0b386e39`）
> 创建：2026-06-23

## 背景

P4.2-P1（commit `0b386e39`）已在**桌面侧**为 4 个新同步实体落地了基础设施：

| 实体 | helper 模块 | RESOURCE_SPECS | ENTITY_MAP | 冲突策略 |
|---|---|---|---|---|
| news | `sync/news_sync.py` | ✅ | n/a (已存在) | REMOTE_WINS |
| inbox_item | `sync/inbox_sync.py` | ✅ | ✅ | REMOTE_WINS (矩阵) / MANUAL (文档建议) |
| memory_entry | `sync/memory_sync.py` | ✅ | ✅ | MANUAL |
| user_preference (settings 行模型) | `sync/settings_sync.py` | ✅ | ✅ | REMOTE_WINS + 黑名单 |

orchestrator 通过 `RESOURCE_SPECS` 把这些资源接入 generic pipeline，**前提是 repository 实现对应的 9 个方法**（`pull_X / merge_remote_X / list_pending_sync_X / build_X_sync_payload / upsert_X / delete_X / mark_X_as_synced / resolve_local_only_deleted_X / set_remote_snapshot_X`）。

## 待办拆解（建议按顺序）

### 1) 桌面 repository 实现（最大块）

为 `inbox / memory / settings (key-value 行)` 在各自 repository 上补齐 9 个方法：
- `repositories/inbox_repository.py`（新建或扩展）
- `repositories/memory_repository.py`（基于 `memory/` 现有 store）
- `repositories/settings_repository.py`（行级 sync，需要新表 `synced_settings`）

每个实体需要：
- `list_pending_sync_X()` — 扫 `sync_state in (LOCAL_ONLY, DIRTY)`
- `build_X_sync_payload(id)` — 调 `build_X_sync_payload` helper + 包装
- `mark_X_as_synced(id, remote)` — 写回 `remote_id` + `last_synced_at` + `sync_state=SYNCED`
- `merge_remote_X(items, prefer_remote=False)` — 按冲突矩阵分发到 `note_sync.resolve_conflict`
- 其它 6 个常规 CRUD-on-sync 方法

### 2) 桌面 `sync/cloud_sync_client.py` 加 channel

```python
RESOURCE_NEWS   = "news"
RESOURCE_INBOX  = "inbox"
RESOURCE_MEMORY = "memories"
# settings 已存在，但需要切换到行级模型并应用黑名单 filter
```

为每个 resource 补：`_collect_X` / `_merge_X` / `_upload_resource` 分发 / `pull` 分发。

注意：**敏感字段过滤** — `settings_sync.filter_settings_for_sync()` 必须在 push 前调用，避免 API Key 出库。

### 3) Worker（automation_hub）侧

当前 `automation_hub/api/routes/sync.py` 中 `resource_type` 是自由文本，**不需要**改协议；但建议：

- 在 `SyncEnvelopeRequest` 加 `resource_type` 白名单（防垃圾数据）
- 文档化推荐的 channel 名（`news / inbox / memories / settings / notes / todos / translations / wordbooks`）

### 4) 手机端 `lib/cloudSync.ts`

当前 `pushLocalData()` 硬编码 3 个 resource。需要：

- 增加 `news / inbox / memories` 三组
- `settings` channel 应切换到行级（key/value 数组而非整对象）
- pull 侧 `switch (item.resource)` 加 case 并写入 Zustand store

**前提**：手机端 Zustand store 需要先有 `news / inboxItems / memories` 字段；目前应只有 inbox 的雏形，需要先建模。

### 5) 端到端测试

- 桌面单元测试：`tests/test_sync_X_repository.py`
- 桌面集成测试：`test_sync_conflict_paths.py` 加 4 个 channel
- 手机端 Vitest：`__tests__/cloudSync.spec.ts` 加 4 个 channel mock

## 估算

| 步骤 | 估时 | 风险 |
|---|---|---|
| 1) Repository 9 方法 × 3 实体 | ~大 | 高（memory 现有结构复杂） |
| 2) cloud_sync_client.py | 中 | 中 |
| 3) Worker 白名单 | 小 | 低 |
| 4) 手机端 cloudSync.ts + store | 中 | 中（store schema 变更牵动多个屏） |
| 5) 测试 | 中 | 低 |

## 决策记录

2026-06-23：用户请求顺序 P1 → P2 → C3-1b，按上述拆解 P1 桌面 helper 部分已闭环；考虑到 (1) 涉及跨仓库实现且 (2) 需要 mobile store schema 改动，C4-P1b 留作后续单独轮次，先推进 C4-P2（端到端加密 + 手机端持久化）。
