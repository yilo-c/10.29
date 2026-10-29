# Changelog

本文件记录「思忆集」桌面应用的重要变更，遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)、版本语义化 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- 行为采集：`usage.record` 接入 `behavior_logger`，Electron 前端补 5 处埋点（模块导航 / 新建便签 / 新建待办 / 收件 / 聊天发送），数据仅本地不上云。
- 通知推送通道（P2.9）：`notification.*` RPC + WebSocket/系统 Toast 双通道分发。
- 用户反馈学习闭环（P2.8）：`chat.feedback.*` RPC + 偏好摘要注入对话锚点。

### Changed
- `automation_hub` personal 域收敛为桌面唯一权威源，hub 降级只读镜像（镜像锁默认开启）。
- WS URL `?token=` 通道默认关闭（走 header/cookie），受 `SIYI_WS_ALLOW_URL_TOKEN` 门控。
- `remote.shell` / `remote.screenshot` 标注弃用（下个发布周移除）。

### Fixed
- `proactive.tick` 死属性回归（`context.inbox_service` → `assistant_inbox_service`）。
- `graph_extractor` LLM 返回非对象 JSON 时的回退（防 `AttributeError`）。
- P0-1 SQLite `database is locked` 根因：审计写入收敛到独立 `audit_writer`（单写线程 + append-only 表）。

## [v0.1.0] - 2026-08-24

质量治理期基线：CI 硬化 + 债务清零机制化落地。

### Added
- 质量门禁闭环：`scripts/done_check.py` 一键聚合 11 项门禁（doc 一致性 / inventory / 无裸 pyc / 层边界 / 核心完整性 / pip-audit / ruff 棘轮 / mypy 棘轮 / broad-except / ignored-source / 工作树干净）。
- 漂移守卫：`scripts/gen_inventory.py` 生成方法/环境变量清单 + CI drift 检查；`scripts/check_core_integrity.py`、`scripts/run_pip_audit.py`（幽灵脚本补齐）。
- 审计 writer 独立库 `core/audit_writer.py`（独立 `audit_trail.db` + 单写线程 + 队列 + 幂等迁移），根治双路径审计整写。
- 增量写入 API `LocalDataStore.append_to_list` / `SqliteKVStore.append_to_list`（SQLite `json_insert` 原地追加）。

### Performance
- JSON 写放大下降 **99.7%**：`feedback_service` 热点的 `load→append→save_json` 改 `append_to_list`，基准 34.9MB / 383.5x → 91KB / 1.0x。

### Changed
- CI 硬化：mypy / ruff / broad-except 棘轮真阻塞；`pre-commit` 配置入库；coverage 门禁固定 21。
- `mypy-baseline.txt` 转为 `--gen-baseline` 事实生成（基线=当前真值，清陈旧；122 条存量债入库），全量唯一指纹持续下探。
- 上帝模块拆分：`_inbox_data.py`(1077 行) → `_inbox_crud`/`_inbox_web`/`_inbox_knowledge`/`_inbox_info`；`_ai.py`(858 行) → `_ai_health`/`_ai_translate`/`_ai_pattern`/`_ai_usage`。

### Fixed
- `subprocess_host.py:211` `JSONDecode` → `JSONDecodeError`（深度验证发现的现存 bug）。
- 19 份 docs 新旧双份归档清除，`check_doc_index_consistency` 归绿；`docs/INDEX.md` 成为唯一导航。
- PyQt6 遗留 `ui.*` 死测试清理 + 无源码 pyc 守卫首抓（2026-07-18 事故遗留）。

### Security
- 命令边界审计（T1.3）：health 命令白名单 + 注入参数拒绝 + 审计报告落 docs。
- 子进程隔离灰度（T4.1）开启观察期。

### Removed
- 根目录磁盘垃圾清理（26+ 临时/日志文件）。