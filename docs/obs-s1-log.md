# S1 观测计数账本 (obs-s1-log)

> 用途：记录观察期（M4 → M5 过渡）每日 gateway 健康与 `gateway-fatal` 计数，作为
> 解锁 M5「破坏性退役」的量化依据。由 `scripts/obs_s1_tick.py` 每日追加。

## 基线
- 基线日期: 2026-08-16
- 观察实例: 无头后端 `ws://0.0.0.0:18765`（PID 4988）
- 基线 `kernel.status`: `route=gateway` / `effective_engine=gateway` / `degraded=false` /
  `gateway_name=dsh` / `gateway_health.available=true`

## 验收门（S1 达成条件）
- 连续 **7 天** `kernel.status.agent_gateway=active`（`degraded=false`）且当日 `gateway-fatal=0`
  → 解锁 M5 破坏性退役（gateway 相关代码的真实下线/重构）。
- 冒烟通过率 = 100%；集成 ≥ 98% 且 0 个 P0/P1。
- 冷启动 < 30s；p95 chat 首 token < 3s（在具备 API Key 的实例上度量）。

## 每日 tick 记录
| 日期 | gateway_active | degraded | gateway_fatal_count | 连续 active 天数 | 备注 |
|------|---------------|----------|---------------------|------------------|------|
| 2026-08-16 | yes | false | 0 | 1

## 计数规则
- 由 `system.self_maintenance`（每日 05:00）或手动运行 `python scripts/obs_s1_tick.py` 追加一行。
- `gateway-fatal` = 当日 `kernel.status` 出现 `degraded=true`（gateway 对象不可用时）或 WS 推送
  `event_type` 含 `gateway` 且 `fatal=true` 的次数（本仓当前降级为配置级，运行期进程故障经
  `gateway_health.available` / `GatewayHealthMonitor` 暴露）。
- `连续 active 天数` 在出现 `gateway-fatal>0` 或 `degraded=true` 时归零重计。
