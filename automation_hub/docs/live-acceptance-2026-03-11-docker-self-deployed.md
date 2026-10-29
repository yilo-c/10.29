# Live 验收记录 2026-03-11（本机自部署 Docker 三容器栈）

## 基本信息

- 验收环境：本机 Docker Desktop 自部署三容器栈
- API 地址：http://127.0.0.1:8010
- 执行时间：2026-03-11
- 执行人：GitHub Copilot
- 触发方式：命令行
- release-check 模式：live
- token 来源：直接注入 API 容器内 SQLite 后落盘到本地文件 [automation-hub/.tmp_self_deployed_live_token.txt](../.tmp_self_deployed_live_token.txt)

## 部署形态

- 镜像：automation-hub:docker-local
- Redis 容器：automation-hub-docker-redis
- API 容器：automation-hub-docker-api
- Worker 容器：automation-hub-docker-worker
- Network：automation-hub-docker-net
- Volumes：automation-hub-docker-data、automation-hub-docker-workspace

## 前置检查

- [x] API 已启动
- [x] Worker 已启动
- [x] GET /health 返回 200
- [x] GET /auth/me 返回 200
- [x] token 未过期且具备管理员能力

健康检查返回：

```json
{"status":"healthy","timestamp":"2026-03-10T18:26:57.570036"}
```

## 执行命令

首次尝试：

```bash
python automation-hub/scripts/release_check.py --mode live --live-api-base http://127.0.0.1:8010 --live-token-file automation-hub/.tmp_self_deployed_live_token.txt
```

正式通过命令：

```bash
python automation-hub/scripts/release_check.py --mode live --live-api-base http://127.0.0.1:8010 --live-token-file D:/BaiduSyncdisk/个人/思忆集/思集/运维/automation-hub/.tmp_self_deployed_live_token.txt
```

## 预检结果

- [x] token 文件存在
- [x] live preflight 输出通过
- [x] 未出现 /health 不可达
- [x] 未出现 /auth/me 401 / 403

关键输出摘要：

```text
[release-check] live preflight api: http://127.0.0.1:8010
[release-check] live preflight token file: D:\BaiduSyncdisk\个人\思忆集\思集\运维\automation-hub\.tmp_self_deployed_live_token.txt
[release-check] live preflight passed
```

## 验收结果

- [x] [release-check] live preflight passed
- [x] [release-check] all checks passed
- [x] verify_system.py 汇总失败数为 0

结果判定：通过

## 覆盖摘要

- 获取当前用户信息：通过
- Token 管理：通过，查询返回 token 数量为 2
- 创建工具：通过
- 列出工具：通过，返回工具数量为 10
- 禁用工具：通过
- 执行低风险工具：通过，状态为 queued
- 执行高风险工具：通过，返回 approval_id
- 列出审批请求：通过，待审批数量为 2
- 查询审计日志：通过，返回事件数量为 10

## 暴露并修复的问题

### 1. live 子进程对相对 token 路径不稳定

- 现象：`release_check.py` 的 preflight 能读取相对 token 路径，但下游 `verify_system.py` 因工作目录切到 `automation-hub/` 而报 `Token file not found`
- 根因：`release_check.py` 将用户传入的相对 token 路径原样传给子进程，子进程运行目录与当前 shell 目录不一致
- 处置：将 [scripts/release_check.py](../scripts/release_check.py) 调整为在 preflight 和子进程调用前统一解析绝对 token 路径
- 修复后结果：同一条命令改回相对路径后也可稳定通过

## 结论

- 是否允许继续发布：是，可作为本机自部署 Docker 栈的 API 放行证据
- 备注：这份记录证明当前仓库提供的 Docker 三容器脚本能在本机独立拉起并通过 API live；若要作为真实远端环境放行证据，仍需单独回填远端留档