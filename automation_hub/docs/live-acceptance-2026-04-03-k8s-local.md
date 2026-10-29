# Live 验收记录 2026-04-03（Docker Desktop 本地 Kubernetes 集群）

## 基本信息

- 验收环境：本机 Docker Desktop Kubernetes（单节点，`docker-desktop` context）
- API 地址：http://127.0.0.1:30000（NodePort 30000）
- 执行时间：2026-04-03
- 执行人：GitHub Copilot
- 触发方式：命令行（`k8s_local_stack.ps1 -Action live`）
- release-check 模式：live
- token 来源：`New-TemporaryAdminToken` 动态注入 pod SQLite，落盘至 `.tmp_k8s_local_live_token.txt`，验收结束后由脚本自动吊销

## 部署形态

- Helm release：`automation-hub-local`
- Namespace：`automation-hub`
- Profile：`host`
- API Pod：`automation-hub-local-6465c6747f-p2hzx`（Up 4 hours）
- Redis Pod：`automation-hub-local-redis-59f4fbbdc5-f6d5h`（Up 4 hours）
- Worker Pod：`automation-hub-local-worker-5d787d9c5-r5gd9`（Up 4 hours）
- 集群运行已 23 天，Pod 重启次数正常（7~14 次 / 23 天）

## 前置检查

- [x] API 已启动
- [x] Worker 已启动
- [x] `GET /health` 返回 200
- [x] `GET /auth/me` 返回 200
- [x] token 未过期且具备管理员能力

健康检查返回：

```json
{"status":"healthy","timestamp":"2026-04-02T18:26:04.884268"}
```

## 执行命令

```powershell
powershell -File automation-hub/scripts/k8s_local_stack.ps1 -Action live -Profile host
```

等价展开：

```text
[k8s-local] preparing temporary admin token via pod automation-hub-local-6465c6747f-p2hzx
[k8s-local] running live acceptance against http://127.0.0.1:30000
[release-check] live preflight api: http://127.0.0.1:30000
[release-check] live preflight token file: ...\.tmp_k8s_local_live_token.txt
```

## 预检结果

- [x] token 文件存在
- [x] live preflight 输出通过
- [x] 未出现 `/health` 不可达
- [x] 未出现 `/auth/me` 401 / 403

关键输出摘要：

```text
[release-check] live preflight passed
[release-check] run live api verification
[release-check] command: ...\.venv3.11\Scripts\python.exe ...automation-hub\verify_system.py --api-base http://127.0.0.1:30000 --token-file ...\.tmp_k8s_local_live_token.txt
```

## 验收结果

```text
╔══════════════════════════════════════════════════════════╗
║  Automation Hub - 系统验证                                ║
╚══════════════════════════════════════════════════════════╝

✅ 通过: 获取当前用户信息           用户ID: 47ae1be9-687f-4eb7-a152-c128885eebf5
✅ 通过: Token 管理                 Token 数量: 6
✅ 通过: 创建工具                   工具ID: test_echo
✅ 通过: 列出工具                   工具数量: 10
✅ 通过: 禁用工具                   已禁用工具: test_echo
✅ 通过: 执行低风险工具             运行ID: 97b7026d-c5c3-4d65-9370-491f3ea4d552
✅ 通过: 创建高风险工具
✅ 通过: 执行高风险工具             审批ID: 517dd214-6f23-45f1-bb66-dc2b99b17ad2
✅ 通过: 列出审批请求               待审批数量: 2
✅ 通过: 查询审计日志               审计事件数量: 10

测试完成  ✅ 通过: 10  ❌ 失败: 0
🎉 所有测试通过！系统运行正常。
[release-check] all checks passed
[k8s-local] revoking temporary live token
```

- [x] `[release-check] live preflight passed`
- [x] `[release-check] all checks passed`
- [x] `verify_system.py` 汇总失败数为 0

结果判定：**通过**

## 结论

- 是否允许继续发布：是
- 备注：本次验收在本机 Docker Desktop K8s 单节点集群上执行，与真实部署环境（`live-acceptance-2026-03-11-remote.md`）相互独立；真实远端环境验收仍待补齐；本记录用于证明当前代码在 K8s 本地集群链路中的可用性。
