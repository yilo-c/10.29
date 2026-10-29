# C4-P2b 跟进计划：手机端持久化加固 + E2E 强化

> 状态：**待办**
> 创建：2026-06-23

## 本轮 (P4.2-P2) 已落地

- `思忆集app/core/envelope_crypto.py`（commit 待定）— v4 AES-256-GCM **带 AAD**
  - AAD = `SHA256("v4:" + envelope_id + ":" + resource_type)`
  - 阻断密文嫁接（把 note 密文塞进 todo 槽位会被 InvalidTag 拒绝）
  - 12 单元测试全绿（roundtrip / 三类绑定字段错配 / 版本字节路由 / 输入校验 / IV 唯一）
- 与 v1/v2/v3 完全向后兼容：旧密文继续走 `sync.cloud_sync_client.decrypt_data`，新模块的 v4 解密会显式拒绝 v3。

## 加密栈现状（截至 2026-06-23）

| 维度 | 现状 | 评分 |
|---|---|---|
| 对称加密 | AES-256-GCM | ✅ |
| KDF | PBKDF2-SHA256 600,000 iter (v3+) | ✅ (OWASP 2023 ≥600K) |
| Salt | per-device, `"siyi-sync-v2:" + device_id` | ✅ |
| AAD 绑定 | **v4 已支持**（envelope_id + resource_type） | ✅（本轮） |
| IV 重用风险 | `os.urandom(12)` 每次新生成 | ✅ |
| Token 持久化（手机） | Capacitor Preferences（Android EncryptedSharedPreferences / iOS Keychain） | ✅ |
| Token 持久化（桌面） | settings.json + .lock，由 OS 文件权限保护 | ⚠ 中（依赖宿主机安全） |
| 前向保密 | **无** — Token 一次配对长期使用 | ⚠ 需要 ratchet |
| 后向保密 | **无** — 设备 revoke 后历史密文仍可解密（如果 token 泄露） | ⚠ |

## 待办（按优先级）

### P2b.1 — v4 在 cloud_sync_client 中启用（手机端配套后）

**前置**：手机端 `cloudSync.ts` 实现对称的 v4 `encryptData/decryptData`（识别 0x04 版本字节 + AAD 计算）。

**桌面侧改动**：
- `sync/cloud_sync_client.py` 增加 `_use_v4_envelope: bool` 开关（settings 控制）
- push 时调 `core.envelope_crypto.encrypt_envelope_payload`
- pull 时按版本字节路由：0x04 → envelope_crypto，其它 → decrypt_data
- 协议协商：通过 `system.info` 暴露 `supported_crypto_versions: [3, 4]`

**估时**：小（已有完整 helper + 测试）；本质是 wiring。

### P2b.2 — 手机端非敏感数据持久化加固（评估）

**当前**：
- 敏感字段（token / api key / pin）→ Capacitor Preferences（系统级加密）
- 非敏感字段（floatingNotes / todos / settings 开关 / 同步游标）→ localStorage（WebView 明文）

**风险**：root 设备 + ADB pull + `allowBackup` 可拖走全部笔记/待办。

**升级方案对比**：

| 方案 | 优点 | 缺点 |
|---|---|---|
| `@capacitor-community/sqlite` + AES 加密 | 性能好，结构化查询，原生加密 | 改造工程大，需要 store schema 迁移 |
| Capacitor Preferences for everything | 一致接口，已就绪 | KV 模型，搜不了，体积膨胀 |
| 应用层加密 localStorage（CryptoJS） | 改动最小 | JS 端密钥仍在 WebView 内存中暴露 |

**建议**：跨越式选 SQLite。先做 **schema 设计 + 迁移工具**，再分模块切换（先 notes/tasks，再 settings/inbox/memory）。属于单独的中大型轮次。

### P2b.3 — Forward / Backward Secrecy（X25519 棘轮）

**目标**：每个 envelope 用一次性密钥加密；token 仅用于初始握手。

**技术栈**：
- `cryptography.hazmat.primitives.asymmetric.x25519`（已在桌面 deps）
- 移动端 `@noble/curves` 或 native bridge
- 简化版双棘轮（Double Ratchet 子集）：每次 push 派生新会话密钥，遗忘旧密钥

**估时**：大（≥ 3 轮迭代 + 跨端协议设计 + 重放测试）。属于 P3 级别。

### P2b.4 — 设备 revoke 强制刷新 token

**当前**：revoke 设备后，已派发的 token 仍能解密历史密文。

**改进**：把 token 拆为 `master_token`（不出本机）+ `sync_token = HKDF(master_token, epoch_counter)`，每次 revoke 推进 epoch_counter，旧 sync_token 失效。

**估时**：中。

## 决策记录

2026-06-23：用户请求顺序 P1 → P2 → C3-1b。

P2 本轮交付：**v4 AAD-绑定加密辅助模块 + 12 单测**。

未落地项（P2b.1 ~ P2b.4）原因：均涉及跨端协议协商或工程量较大，需要单独评估窗口（每个 ~ 1 轮迭代），不阻塞 C3-1b。
