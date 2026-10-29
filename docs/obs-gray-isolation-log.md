# subprocess 隔离灰度遥测账本 (obs-gray-isolation-log)

> 用途：T4.1 灰度观察期（≥1 周）记录 capability_routing 的 plugin / fallback
> 命中与插件错误，作为 **SIYI_PLUGIN_ISOLATION=subprocess** 转正的量化依据。
> 由 `python scripts/obs_gray_tick.py` 每次运行追加一行（纯只读探测，不改变后端状态）。

## 观察基线
- 灰度模式: `SIYI_PLUGIN_ISOLATION=subprocess`（`plugins.sandbox.choose_isolation_mode` 切换）
- 观察实例: 无头后端 `ws://0.0.0.0:18765`，经 `system.health` 的 `capability_routing` 子系统采样（10s 聚合缓存）
- 已装配 plugin invoker: 见下方 `plugin 化 caps` 列（启动时日志 `Registered 3 plugin tools`）

## 转正验收门（T4.1 达成条件）
- 观察期 ≥ 1 周，期间 `plugin_errors` 保持 0 且 info 链路业务无回退；
- `plugin_hits` 随真实业务流量增长；冒烟/装配闭环脚本（`_smoke_gray_isolation.py` / `_verify_gray_wiring.py`）持续通过；
- 达成后移除 `start_backend.bat` 的灰度开关或默认切换，并归档 `plugins/sandbox.py` 的 sandbox 直执行路径。

## tick 记录
| 时间 | plugin_hits | fallback_hits | plugin_errors | 已跟踪 caps | plugin 化 caps | per_capability(plugin/fallback/error) |
|------|-------------|---------------|---------------|-------------|----------------|----------------------------------------|
| 2026-08-22 20:38 | 0 | 0 | 0 | 0 | 6 |  |
| 2026-08-22 22:29 | 0 | 0 | 0 | 0 | 6 |  |
