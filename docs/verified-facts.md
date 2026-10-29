# 思忆集 已核验事实登记册（Verified Facts）

> 建立：2026-08-25（审计自核验 V2.1 批次 0 交付物）
> 用途：登记 **已确认无问题 / 已确认边界** 的事实，防止未来审计把确认过的事项当新发现误报。
> 登记纪律（V2.1 §11.7 G-4）：每条**必须带「失效条件」**。条目失效 → 移入文末「已失效」区，不删除（保留审计轨迹）。
> 核验级别沿用 V2.1 第〇节：✅亲验 / 🔍复核。

| # | 事实 | 核验级 | 复核入口 | 失效条件 |
|---|---|---|---|---|
| F1 | `.env`、`settings.json`、`*.pem`、`*.key` 均未入 git | ✅亲验 | `git ls-files` | 未来有人强制 add 敏感文件且未识别 |
| F2 | 备份快照（`data/backups/snapshot_*`）为语义 JSON（notes/tasks/settings/memories/vocab/news 等），**不含 auth_token/api_key/token**；`data/auth_token` 文件当前不存在 | ✅亲验 | grep 快照目录 `auth_token\|api_key\|token` 零命中 + glob `data/auth_token*` 空 | 批次1 引入 `data/auth_token` 后未纳入快照白名单 → 需补验并登记 |
| F3 | CSV/语义备份有 checksum 校验，恢复前验证，不一致即中止 | 🔍复核 | `backup_service.py:396-402,529-558` | checksum 算法被改或绕过 |
| F4 | `restore_backup` 存在组件测试（5 处调用覆盖伪造/篡改/缺文件路径），且 `backup_service` 支持 SQLite data_store 恢复路径 | 🔍复核 | `tests/test_backup_restore_checksum.py`；`backup_service.py:466-672` | 测试被删除或不再运行 |
| F5 | `scripts/rollback_drill.py` 存在（五步+沙箱不变真实数据），但**当前契约已与真实存储衰退**：仅 glob 顶层 `*.json`、硬卡 `settings/tasks/notes.json`，对 SQLite 主存储（13 个 .db）失明 → 实跑全 `all_ok=False`（见 risk-register R-DRILL-1） | ✅亲验 | `scripts/rollback_drill.py:15-21,76-119` + 实跑记录 | 批次3 重写（SQLite 表级快照/恢复+启动+health 冒烟）后，此条失效并反转登记 |
| F6 | 覆盖率门禁阈值已上调至 22.0（略高于实测 22.51%） | ✅亲验 | `coverage_gate.py:25-28` | 阈值被回退 |
| F7 | 认证 token 现状为纯内存态（重启即换）；`/auth` 端点只有会话创建，**无轮换/吊销** | ✅亲验 | `ws_server.py:83-84,218-275`；grep `revoke\|rotate` 零命中 | 批次1 落地 `auth.rotate/revoke` 后需更新 F7/F8 |
| F8 | 全库当前无 Windows ACL 处理（`icacls\|win32security` 零命中） | ✅亲验 | grep 全库 | 批次1 落地 icacls 后反转登记 |
| F9 | 同步游标存储于 settings 键 `cloud_sync_cursor`（`_persist_cursor`）；现无 `server_watermark` 字段 | 🔍复核 | `cloud_sync_client.py:162-186,684-692` | 批次3 引入水位模型后更新 |
| F10 | `get_auth_token()` 为死代码（全库零调用） | ✅亲验 | `ws_server.py:132-134` | 批次1 接线或删除后更新 |
| F11 | 认证 token（若启用）在可持续架构下轮换/吊销可即时生效（会话与连接均在进程内存） | 🔍复核 | `ws_server.py:85,94-98` | 会话/连接迁移到持久层后不再即时 |
| F12 | SQLite at-rest 加密当前为**显式不做**决策（评估见 V2.1 §11.3），威胁模型以网络/进程边界为主 | 🔍复核 | V2.1 §11.3 评估 | 产品进入多用户/合规场景后需反转评估 |
| F13 | pip 依赖门禁当前语义为「空基线=跳过」——门禁暂未真正生效 | ✅亲验 | `run_pip_audit.py:100-105,46-59` | **批次5 必须封堵（E1/E2），失效日期为批次5 交付时** |
| F14 | 手机端至于 P0-7 假配对债（`setPlatformStorage` 零调用、401 无 status 字段判成功） | ✅亲验 | `useStore.ts:148` + `DevicePairingSheet.tsx:34,37` | 批次4 配对重做后更新 |

## 已失效区（由 ✅ → 失效后移入，不删除）

（暂无）