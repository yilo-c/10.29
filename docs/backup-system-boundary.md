# 思忆集 备份系统边界决策（一页纸）

> 建立：2026-08-25（V2.1 批次0 §11.8 交付物；回应 §11.4 N7「两套备份边界未定义」）
> 状态：决策已定，写入 verified-facts F2/F4 与 docs/ 索引。

## 现状（✅亲验 2026-08-25）

库内存在两套备份机制，此前边界未定义：

| 维度 | A. `backup_service`（语义备份） | B. `siyi_backup_snapshot` / `data/backups/snapshot_*`（物理快照） |
|---|---|---|
| 内容 | 语义 JSON：notes/tasks/settings/memories/ai_cache（`backup_service.py:89-105` 开关） | `data/` 目录下各域 JSON 逐份拷贝 + `snapshot_manifest.json` |
| 是否含 auth_token/密钥 | 含 settings（含敏感键），**不含独立密钥文件**（grep 快照零命中） | 同上：快照目录 grep `token` 零命中；`data/auth_token` 当前不存在 |
| checksum | 有，恢复前校验（`:396-402,529-558`） | 有 manifest |
| 是否有 restore | **是**（`restore_backup()` 466+，含组件测试） | 是（快照拷贝回填） |
| 触发方式 | `create_backup`（CLI + 可能内联） | `siyi_backup_snapshot` 工具（risk=high 需 sandbox 审批，保留 10 份轮换） |

## 决策

1. **角色划分**：A = **灾备（DR）**——恢复首选，有 checksum 校验 + 恢复路径+测试；B = **时间点快照（point-in-time）**——审计/回滚用，随「保留 10 份轮换」自动健康管理。
2. **恢复时用哪套**：日常被删/被改错 → 用 **A（backup_service restore）**；需退回某时间点整库 → 优先 **B 快照**。
3. **批次 0 恢复演练**：两步都演练一次（见 risk-register「批次0·恢复演练」行），演练结果记录耗时/失败项。
4. **批次 1 联动**：`data/auth_token` 落盘后，必须把它纳入**两套**备份都覆盖的边界（快照白名单 + backup_service 若含 settings 则评估是否需含 token）——防止恢复后认证态与数据不同步。
5. **不重复备份**：避免对同一份 `data/*.json` 做两套全量拷贝；B 的轮换即「历史保留」，A 的 restore 即「恢复路径」，二者互补不互斥。

## 待办锚点
- 批次 1：`data/auth_token` 纳入备份边界 → 更新 verified-facts F2 失效条件并复核。
- 批次 5：补一次「某时间点 B 快照恢复」的长期保留策略说明（是否需压缩/去重，超范围则不阻塞本批）。