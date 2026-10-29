# automation-hub 处置方案（新声明，取代旧定位）

> **版本：** 1.0
> **日期：** 2026-08-17
> **类型：** 架构决策声明（ADR 性质）
> **状态：** ✅ 现行真源
>
> **本文档是一份新声明，不是对旧文档的修改。** 它的职责是：**明确写下此前文档中的定位错误**，给出经代码现状核实后的正确处置方向，让旧文档的偏差**有迹可循、可追溯**。旧文档的原文一律保留，不改写、不删除，只在其顶部追加"更正声明"块指向本文档。

---

## 0. 为什么需要这份声明

`automation_hub`（后端底座）早期文档 [ARCHITECTURE_DESIGN.md](../automation_hub/docs/design/ARCHITECTURE_DESIGN.md) 声明其为"唯一后端底座，负责认证、同步、审计、AI、外部工具执行和跨端统一 API"，并把 [思忆集app](../思忆集app) 定位为"待收敛的桌面端原型"。

经过对仓库**实际代码与运行链路**的核查，该定位与仓库现状存在系统性偏差。为避免后续开发继续被错误定位牵引，特此开具本处置声明。

---

## 1. 仓库真实全景（已核实，2026-08-17）

此前架构讨论只关注"桌面端 + hub"两端，遗漏了仓库中的另两条活跃链路。仓库实际由 **4 个模块 + 3 条跨设备链路**组成：

| 模块 | 角色 | 实际状态 |
|---|---|---|
| `思忆集app` | 桌面主应用，**内置 JSON-RPC backend**（`backend/ws_server.py`，约 112+ 方法，默认 `ws://:18765`）| 活跃主战场 |
| `思忆集手机端app` | Capacitor Android 原生 app | 活跃 |
| `cloudflare/worker` | 云同步 Worker（`sync.ts`：仅 `register`/`upload`/`download` 三端点，AES-GCM v4 端到端加密）| 活跃，轻量 |
| `automation_hub` | 自称"唯一后端底座"（auth/审批/审计/工具/agent/worker/K8s 全家桶）| **被边缘化** |

### 三条跨设备链路（均不依赖 hub 为唯一枢纽）

| 链路 | 参与者 | 技术底座 | 用途 |
|---|---|---|---|
| **A. LAN 直连** | 手机端 ↔ 桌面 app | JSON-RPC WebSocket（桌面 `ws_server.py`，`rpcClient.ts`）| 手机实时拉/推 notes/tasks/translations/wordbooks；桌面 AI 代理 |
| **B. 云同步** | 手机端 ↔ Cloudflare Worker ↔ 桌面 app | E2E 加密信封（`cloudSync.ts` ↔ `cloud_sync_client.py`，AES-GCM v4）| 离线跨设备数据云同步 |
| **C. hub PWA / pairing** | 手机 PWA ↔ hub | FastAPI 托管 `mobile_app/` | 已被原生手机 app 实质取代 |

**关键事实：手机端的三条真实链路没有一条依赖 hub 作为"唯一枢纽"，hub 只作为 LAN/C/W 的中继之一。**

---

## 2. 旧定位的错误声明清单

以下为早期文档中的**错误/失效定位**，逐一列明，方便追溯：

### 错误 E1：automation-hub 是"唯一后端底座"
- **出错位置**：[ARCHITECTURE_DESIGN.md](../automation_hub/docs/design/ARCHITECTURE_DESIGN.md) "2026-03-11 规划对齐说明"；`automation_hub/docs/design/ARCHITECTURE_DESIGN.md`
- **实际**：同步真源被 [cloudflare/worker](../cloudflare/worker)（云）与桌面 `ws_server.py`（LAN 直连）分担，hub 并非唯一底座，而是三岔路之一。

### 错误 E2：思忆集app 是"待收敛的桌面端原型"
- **出错位置**：同 [ARCHITECTURE_DESIGN.md](../automation_hub/docs/design/ARCHITECTURE_DESIGN.md)
- **实际**：`思忆集app` 已远超原型——自带完整 JSON-RPC backend、react_executor、agent 链、guard、cron、memory、knowledge、MCP，是仓库的**主战场**，不是待收敛的瘦客户端。

### 错误 E3：同步/数据能力应"逐步迁回后端 hub"
- **出错位置**：[ARCHITECTURE_DESIGN.md](../automation_hub/docs/design/ARCHITECTURE_DESIGN.md) "翻译、新闻抓取、身份接入、同步等逐步迁回后端"
- **实际**：云同步已由轻量的 Cloudflare Worker（E2E 加密）承载，桌面 LAN 直连已由 app 自带 ws_server 承载。把这些迁回 hub 的明文 SQLite 属倒退。

### 错误 E4：跨设备同步契约将 complete-architecture-design 列为唯一真源，且未体现双链路
- **出错位置**：[cross-device-sync-spec.md](cross-device-sync-spec.md) 顶部引用块
- **实际**：存在 **LAN（桌面 ws）** 与 **云（Worker）** 两条并行同步主链，契约/加密本各自为真源，仅一份"完整架构文档"无法覆盖两个独立协议的实现真相。
- 另见 [automation-hub-dual-module-status.md](archive/automation-hub-dual-module-status.md)：声明 `automation-hub` 与 `automation_hub` 包名的"兼容层"关系——该说明本身合理，但并未触及上面同步真源的偏差。

### 错误 E5：以 `automation_hub_client.py` 的具名方法（pull_notes/upsert_* 等）为同步对外接口
- **出错位置**：[automation_hub_client.py](../思忆集app/integrations/automation_hub_client.py) 中约 100 行 `__getattr__` 动态生成方法（注释称"兼容 24 个历史具名方法"）
- **实际**：全仓库**无任何调用点**（grep 核实）；真实消费方 [generic_sync_service.py](../思忆集app/sync/generic_sync_service.py) 走泛型 `_upsert/_pull/_delete` 私有方法。且 `_ENTITY_MAP`(9 类) 与 `_ENTITY_ENDPOINTS`(6 类) 契约漂移，存在 `inbox/memories/settings` 调用即 `KeyError` 的隐患。

---

## 3. 处置结论：hub 裁剪归位为「LAN 治理网关」

基于上述核实，`automation_hub` **不是多余**（其三条铁律治理内核是仓库唯一不可替代资产），但**定位错了**。正确处置是：

> **automation_hub = 局域网多端网关 + AI 执行治理层（审批/审计/工具白名单）**

**职责分配（零重叠）：**

| 能力域 | 真源位置 | 说明 |
|---|---|---|
| 云跨设备同步（离线）| **Cloudflare Worker**（E2E 加密）| 保持现状，不迁回 |
| LAN 直连 / 实时中继 / 桌面 AI 代理 | **思忆集app `ws_server`** | 保持现状，不迁回 |
| 跨端 LAN 数据中继（`/personal/*` 6 实体）+ pairing | **automation_hub** | hub 在此保留 |
| 审批、审计、工具注册（三条铁律内核）| **automation_hub** | hub 的价值核，保留 |
| 桌面本地能力（guard/cron/copilot/记忆/知识库）| **思忆集app** | 保留，不搬 |

### 裁剪动作（建议阶段 A 先行）

**保留：**
- `/personal/*` 6 实体同步端点、`/mobile/pairing`、auth/token、审计、审批、工具注册表、`/agent/ask|plan` 代理

**删除（约 60% 体积，均无当前消费方）：**
- `deploy/`（Helm/ArgoCD/kind）、`.github/` CI、Dockerfile/docker-compose —— LAN 服务用 `python -m uvicorn` 即可（[mobile_bridge_manager.py](../思忆集app/cross_device/mobile_bridge_manager.py) 已如此启动）
- `worker/`（ARQ+Redis 执行器）、`local_agent/`（远端节点注册）
- `intel/`、`repos/`、`proposals/`、cron/workflows/evolution 路由 —— app 侧已有更强实现
- 20+ 份 `live-acceptance-*` / `rollback-*` / `runbook` 文档

**配套（app 侧）：**
- `automation_hub_client.py` 收口为公开泛型 API（修 9:6 契约漂移、删约 100 行死代码）、修复乱码
- 同步职责写入规范（见下节）

**远期（可选）：** 若确认 PWA 废弃 → hub 再砍 `mobile_app/`；最终可瘦身为纯"审批/审计/工具治理"轻服务，甚至并入 app backend，hub 退役。

---

## 4. 同步规范真源声明（取代错误 E4）

本声明取代 `cross-device-sync-spec.md` 顶部"complete-architecture-design 为唯一真源"的说法。同步域真源改为：

| 链路 | 真源文档/代码 |
|---|---|
| 云同步（手机↔Worker↔桌面）| 桌面 [cloud_sync_client.py](../思忆集app/sync/cloud_sync_client.py) + 手机 [cloudSync.ts](../思忆集手机端app/src/lib/cloudSync.ts)；协议注释为最高真源 |
| LAN 直连 | 桌面 [ws_server.py](../思忆集app/backend/ws_server.py) + 手机 [rpcClient.ts](../思忆集手机端app/src/lib/rpcClient.ts) |
| hub LAN/PWA 中继 | [automation_hub_client.py](../思忆集app/integrations/automation_hub_client.py)（收口后）|

任何涉及同步的改动，须先对照对应链路**实现源码**核实，而非仅依据单份架构文档。

---

## 5. 相关旧文档更正声明索引

以下旧文档顶部已分别追加"更正声明"块指向本文档：

- [automation_hub/docs/design/ARCHITECTURE_DESIGN.md](../automation_hub/docs/design/ARCHITECTURE_DESIGN.md) — 更正 E1/E2/E3
- [docs/cross-device-sync-spec.md](cross-device-sync-spec.md) — 更正 E4
- [docs/archive/automation-hub-dual-module-status.md](archive/automation-hub-dual-module-status.md) — 更正 E4 补充
- [docs/document-map.md](document-map.md) — 纳入本文档为现行真源

**注：** 以上旧文档原文一律保留，仅追加更正声明块，确保历史有迹可循。

---

## 6. 待办执行清单（后续按此推进）

- [ ] 阶段 A：裁剪 hub（删 deploy/worker/local_agent/intel/repos/proposals/旧验收文档）
- [ ] 阶段 A：`automation_hub_client.py` 收口 + 乱码修复
- [ ] 阶段 A：补契约漂移测试（用 hub `/openapi.json` 校验实体端点）
- [ ] 阶段 B：按上节真源表复核同步实现，消除双契约
- [ ] 阶段 C：远期 PWA 废弃评估与 hub 瘦身

---

## 7. 实际执行进展（2026-08-23，代码实证，覆盖上文静态判定）

> 本节记录**真实落地情况**，纠正第 3 节静态判定与代码现状的偏差。

### 7.1 已完成
- **`deploy/` 已归档**：`automation_hub/deploy/`（27 文件 Helm/ArgoCD/kind 纯部署配置，git 跟踪）经 `git mv` 移入 `automation_hub/archive/deploy/`，保留历史、移出活跃编译路径。无任何 `.py` 逻辑、零代码耦合。
- **`intel/`、`repos/` 早已是空目录**：git 未跟踪、磁盘无文件，ADR 的删除项实质已落地（无需再删）。
- **`worker` 与 `api/*` 软解耦**（见 `docs/hub-worker-decoupling.md`）：原 6 个 API 路由在模块顶层硬 import `automation_hub.worker.*`，导致 `import api.main` 在 Redis/worker 缺失时立即 `ImportError`。已改为调用时延迟 import（`api/_worker_bridge.py`），新增 `tests/test_worker_bridge.py` 固化。
- **`automation_hub_client.py` 收口早已完成**：实测为干净泛型 `_ENTITY_ENDPOINTS`（6 类），无 `__getattr__` 死代码、无乱码，ADR 第 3 节"配套"描述已过时。
- **hub `tests/conftest.py` 修复**：移除硬编码 `basetemp = .../siyi_hub_pytest_tmp` 全局覆盖（Windows 沙箱回收站不可用导致残留目录使后续 run `mkdir` 报 `FileExistsError`）；改用 pytest 默认随机临时目录。

### 7.2 与 ADR 静态判定的偏差（重要）
- **`worker/` 暂不可删**：第 3 节称"无当前消费方"，但 `api/approvals`、`api/proposals`、`api/routes/{runs,tool_runs,metrics,agent}` 仍 import `worker`（审批/提议 ARQ 入队核心）。已通过软解耦解除启动期硬依赖，但 `worker` 本体仍撑着业务核心，删除需先由 app 侧 `integrations/automation_hub_client.py` 接管入队职责。
- **`proposals/` 路由仍在**：被 `api.main` include，属审批核心，非"无消费方"。
- **`local_agent/` 未处理**：本轮未涉及，保留待后续。

### 7.3 全量验证
- `pytest -q`（automation_hub）：**185 passed / 0 回归**（含本次新增 bridge 测试与 conftest 修复）。

---

## 7. 个人数据双源收敛实施记录（T1.6，2026-08-23）

依据 [docs/decisions/2026-08-23-automation-hub-single-source.md](decisions/2026-08-23-automation-hub-single-source.md)：
**桌面 repositories 为个人数据唯一权威源；hub `personal_*` 域降级为只读镜像。**

### 已落地动作

- **写入来源守卫（决策 4.1）**：在 `automation_hub/api/routes/personal_factory.py`
  的 POST/PUT/DELETE 端点注入 `require_desktop_sync_source()` 依赖。缺少
  `X-Siyi-Sync-Source: desktop` 头的写入请求一律返回 403，从入口根除非桌面来源的双写。
  **V2-01（2026-08-24）默认翻转：镜像锁默认开启**（桌面为 personal 域唯一真源落地）；
  仅设 `SIYI_HUB_PERSONAL_MIRROR_LOCK=0` 显式关闭，供迁移期回退。
- **桌面客户端发源标识（决策 4.2 衔接）**：`思忆集app/integrations/automation_hub_client.py`
  的 `_prepare_upsert` / `_prepare_delete` 现已在 `/personal/*` 写入请求附加
  `X-Siyi-Sync-Source: desktop` 头（经由 `AutomationHubRequestSpec.extra_headers`
  透传），使合法桌面同步推送通过镜像锁守卫。
- **契约测试（决策 4.3）**：新增 `automation_hub/tests/test_personal_mirror_lock.py`，
  断言锁开启时「无来源头→403、有来源头→200」，锁关闭时既有无头写入仍可用。

### 验证状态

- 桌面侧改动（client/types）已通过语法与导入校验。
- hub 侧守卫代码已落地。
- **`cron.py` 缺失缺陷已修复（2026-08-23 续做）**：新建
  `automation_hub/api/routes/cron.py`，提供只读检视端点 `GET /cron`（列出 arq
  注册的定时任务及其调度描述）与 `GET /cron/{name}`（按函数名查详情）。该路由
  **不重复造调度器**——真正的 cron 调度由 `worker/arq_settings.py` 的 arq
  `watchdog_cron`（每 5 分钟回收 stale run）负责；API 层只暴露元数据，与
  `cronjob` 角色（scope=`tool:execute`+`tool:read`，由 `api/middleware/scope.py`
  强制仅限 `/tools/` 路径）的设计意图一致。导入链路做了安全降级（`build_worker_kwargs`
  不可用时返回空列表），确保 `import api.main` 永不在加载期崩溃。
- **配套修复**：`automation_hub/tests/conftest.py` 原将 pytest `basetemp` 硬编码为
  仓库内 `automation_hub/.pytest-tmp`，该目录权限损坏后触发 safe-delete 批量删除
  防护误杀整个测试会话；已改为指向系统 temp 下独立路径，既修好 T1.6 测试运行，
  也避免测试临时文件污染 git 仓库。另修正 `test_personal_mirror_lock.py` 中二次
  `bootstrap_headers` 导致同库 409 的测试隔离 bug。
- **测试结果**：`test_personal_mirror_lock.py` **3/3 通过**；hub 全量套件
  `--collect-only` 恢复至 **182 tests collected**（此前因 cron 悬空引用全盘
  collect 失败）。后续需在 CI 中跑全量确认无回归。

### 文档同步（决策 4.4）

本记录即文档同步落点；与第 3 节「`/personal/*` 6 实体同步端点」归属 hub 的中继
定位一致——中继保留，但写入权唯一归属桌面。