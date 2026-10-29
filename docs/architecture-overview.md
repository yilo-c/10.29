<!-- 模块说明：思忆集现行架构总览（2026-08-09 重写，取代旧 PyQt6 版本）。架构决策与实现细节的索引入口见 docs/document-map.md。 -->

# 思忆集整体架构（现行版）

> 状态：**现行架构真源**（2026-08-09 重写）。旧版描述的 PyQt6 + QWebChannel 架构已淘汰；
> 曾指向的 `complete-architecture-design.md` 并不存在（死链），本文档即为替代真源。

## 一、架构分层图

```
┌──────────────────────────────────────────────────────────────────┐
│  桌面端壳层 — Electron (desk-notes/packages/desktop)              │
│  React + TypeScript + Zustand；Orb 悬浮球 / 聊天 / 工作台 / 桌宠   │
│  web_dist 内嵌副本由 `pnpm run sync` 从构建产物同步               │
├──────────────────────────────────────────────────────────────────┤
│  手机端 — Capacitor (思忆集手机端app)                              │
│  5 Tab + 收件箱；Tailscale 直连 WS 或经 Cloudflare Worker 同步     │
├──────────────────────────────────────────────────────────────────┤
│            WS JSON-RPC（port 18765，334 个已注册方法）             │
│   请求 {"jsonrpc":"2.0","id","method","params"} / 事件推送          │
├──────────────────────────────────────────────────────────────────┤
│  Python 无头后端 — 思忆集app/backend/                              │
│  headless_main.py（入口+单实例锁+启动续跑扫描）                    │
│  ws_server.py / method_registry.py / handlers/（47 模块）          │
│  event_bridge.py（EventBus→WS，每客户端独立队列+优先级丢弃）        │
│  process_watchdog.py（常驻守护：崩溃重启+熔断+端口僵死探测）        │
├──────────────────────────────────────────────────────────────────┤
│  AppContext（app_runtime/，DI 装配根 + mixins + providers 懒加载）  │
│  ├─ assistant/  小问代理层：butler/agent_loop/chain/judge/审批      │
│  ├─ ai/         多 Provider 聚合/熔断/降级/配额                    │
│  ├─ memory/ knowledge/  记忆与知识图谱 RAG                        │
│  ├─ services/   cron/task_queue/通知分发/沙箱审批/备份等           │
│  ├─ sync/       Cloudflare Worker 协议同步（E2E 加密）             │
│  └─ data/ repositories/ domain/  存储与领域模型                    │
├──────────────────────────────────────────────────────────────────┤
│  可选底座 — automation_hub/（FastAPI，port 8000）                  │
│  Agent 规划/执行、审批、审计、ARQ Worker + Docker 隔离执行          │
│  桌面端经 integrations/automation_hub_client.py 调用（松耦合）      │
└──────────────────────────────────────────────────────────────────┘
```

## 二、启动链路与常驻机制

```
启动.py / 启动.bat
  └─ python -m backend.process_watchdog        ← 常驻守护（生产路径）
       └─ spawn: python -m backend.headless_main
            1. 单实例锁（backend/instance_lock.py）
               - 锁旁路文件记录持有者 PID
               - 获取失败时三重校验接管：PID 存活 + WS 端口宽限探测 + 进程名
               - SIYI_LOCK_TAKEOVER=0 可关闭接管
            2. AppContext 初始化（DI，headless 模式）
            3. TraceRecorder 初始化 + cron 接入观测
            4. 注册 334 个 RPC 方法 → WebSocketServer 监听 18765
            5. EventBridge 桥接 + checkpoint 续跑扫描（task.resumable 事件）
            6. CronService / task_queue worker / 各调度器启动

  watchdog 行为：
  - 子进程非零退出 → 指数退避重启（base 1s，cap 60s，max 5 次）
  - 300s 窗口内重启超 3 次 → 熔断放弃（防重启风暴）
  - 稳定运行 >60s 后崩溃 → 重置重启计数
  - 端口存活探测：子进程活着但 WS 端口宽限期后持续不可达 → 判定僵死，
    终止子进程走崩溃重启流程（SIYI_WATCHDOG_HEALTH_ENABLED=0 可关闭）
```

## 三、关键数据流

### 聊天/工具任务
```
前端 chat.send(stream) → handlers/_chat_memory → judge 路由
  ├─ butler 路径（PydanticAI 单 agent 循环，默认）
  ├─ smolagents 路径（backend/siyi_tools + chat_paths/smolagents_path，force_tools）
  └─ 工具进度经 tool_progress chunk + exec.step 事件 → TraceRecorder

自治面（定时/agent_task 目标）：autonomy 子系统
  execution_router{react/computer_use/goal_run} + chain 多步规划（judge_mixin 驱动）
```

### 事件推送
```
services/assistant 发 EventBus 事件 → event_bridge
  → 每客户端独立 asyncio.Queue + sender 协程（慢客户端只拖自己）
  → 队列满按优先级丢弃（审批/同步/通知/sandbox/续跑/告警类永不丢弃）
  → WS 推送给桌面/手机/浏览器扩展各端
```

### 定时任务与监控
```
CronService（APScheduler + polling 双引擎）
  → 全部走 PersistentTaskQueue（崩溃后自动恢复）
  → 执行结果：job.history（兼容）+ TraceRecorder（统一观测）
  → 连续失败 ≥ 阈值 → cron.job_alert → 通知分发器 → 桌面/手机
  → 每日 04:00 记忆健康巡检（存储可写/数据可读/记忆可查）
```

### 同步与冲突
```
sync/cloud_sync_client（AES-256-GCM + PBKDF2 v4）⇄ Cloudflare Worker（D1）
  拉取合并：LWW + 真冲突检测（双方自上次同步后均修改）
  冲突 → sync.conflict 事件 + sync_state=conflict 标记 + 本地版本快照
  裁决 → sync.resolve_conflict RPC（仅桌面端；手机端只收通知）
```

### 云同步加密与 Worker 结构（AES-GCM v4）

与手机端 `思忆集手机端app/src/lib/cloudSync.ts` 协议完全兼容（桌面端真源：
[cloud_sync_client.py](../思忆集app/sync/cloud_sync_client.py)）。

```
端点（Cloudflare Worker，D1 存储）
  POST /api/sync/register  — 设备注册（需 register_secret，H1 安全修复）
  POST /api/sync/upload    — 上传加密数据（Bearer token 认证）
  GET  /api/sync/download?since=&device_id= — 拉取增量更新（Worker unixepoch() 秒级游标）

加密：AES-256-GCM + PBKDF2-HMAC-SHA256（密钥由用户 sync_token 派生）
  版本字节自动识别：v4(0x04) / v3(0x03) / v2(0x02) / v1(legacy，无版本字节)
  v4（当前写入）信封：[0x04][iter(4 BE)][iv(12)][ct+tag]，base64 编码
    - salt = "siyi-sync-v4:<sync_group_id>"，sync_group_id = sha256(register_secret)[:16]
    - 同 Worker 部署（共享 register_secret）的设备派生同 salt → 同密钥 → 可互解；
      跨组设备即使 token 相同也派生出不同密钥 → 不可互解（不入信封）
    - iterations(600K, OWASP 2023) 写入信封头，支持未来调参而不换版本字节
    - 无 register_secret 时降级 v3（per-device salt，仅同设备可解，向后兼容）
  PBKDF2 600K 迭代约 300-500ms → 派生密钥按 (token_sha256, salt, iterations) 内存缓存（最多 32 条）
  解密在 asyncio.to_thread 中执行，避免阻塞事件循环

资源域：notes → PersonalNoteRepository / todos → PersonalTaskRepository /
        settings → SettingsRepository（仅同步非敏感白名单字段）/ info_brief / info_sections

合并策略
  LWW + 真冲突检测：本地 updated_at > last_synced_at 且远端更新 > last_synced_at 即冲突
  冲突 → 保留本地副本到 versions 历史 + 标记 sync_state=conflict + 发 sync.conflict 事件
  时间戳归一化：ISO 8601 / 毫秒 epoch / 秒 epoch → epoch 秒（P1-8）
  软删除传播（M24）：deleted 标记随数据推送，远端删除不会在本地复活

同步循环：默认 5 分钟一次（cloud_sync_interval_seconds），连续失败指数退避（上限 30 分钟）
游标：仅由 pull 推进并持久化到 settings（cloud_sync_cursor），重启不重拉全量
```

### 审批（受控执行铁律）
```
破坏性操作 → sandbox_manager 挂起 pending（预览可见）
  → 用户批准（approve / batch_approve）才执行
  → 超时策略（默认 30 分钟）：reject 或 dry_run 降级；临期 60s 提醒
  → sandbox.timeout 事件进通知白名单（high，手机端可收）
```

## 四、设计原则

1. **本地优先，云端增强**：核心数据本地 SQLite/文件；云同步 E2E 加密且可选。
2. **双向桥接唯一通道**：前端 ↔ 后端只走 WS JSON-RPC，不另开 HTTP 端点。
3. **受控自动化**：所有破坏性/高危动作先审批后执行，可追踪、可回退（audit_rollback）。
4. **依赖注入统一入口**：核心依赖经 AppContext 装配，handlers 通过闭包取 context 属性。
5. **层边界 CI 守卫**：`scripts/check-layer-boundaries.py` 强制 assistant↛backend 等方向约束；
   `scripts/check_no_sourceless_pyc.py` 防源码丢失事故再发（2026-07-18 事故守卫）。
6. **常驻优先**：后端以 watchdog 守护为生产路径；启动即扫描可续跑任务，
   执行全程入统一观测（trace），失败可告警、可恢复。

## 五、环境变量速查（本架构相关）

| 变量 | 默认 | 说明 |
|---|---|---|
| `SIYI_WS_HOST` / `SIYI_WS_PORT` | `127.0.0.1` / `18765` | WS 绑定（N-D 收口 2026-09-17：默认仅本机，Tailscale/LAN 显式设 `0.0.0.0`，非回环绑定+无认证 fail-closed 拒启） |
| `SIYI_LOCK_TAKEOVER` | `1` | 僵死锁接管开关 |
| `SIYI_WATCHDOG_HEALTH_ENABLED` | `1` | watchdog 端口存活探测开关 |
| `SIYI_WATCHDOG_HEALTH_GRACE` / `_INTERVAL` / `_FAILS` | `90`/`10`/`6` | 探测宽限/间隔/失败阈值 |
| `SIYI_WS_CLIENT_QUEUE` | `256` | 每客户端事件队列长度 |
| `SIYI_CRON_ALERT_THRESHOLD` / `_COOLDOWN` | `3` / `3600` | cron 连续失败告警阈值/冷却 |
| `SIYI_SANDBOX_TIMEOUT_SECONDS` / `_POLICY` | `1800` / `reject` | 审批超时/策略（reject\|dry_run） |
| `SIYI_RPC_TIMEOUT` | `60` | 集成测试 RPC 接收超时 |

完整环境变量表见根目录 `AGENTS.md`；测试基线与回归约定见 AGENTS.md「测试基线」章节。
