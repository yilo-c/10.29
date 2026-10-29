# Worker Live 验收记录 2026-04-03（Docker Desktop 本地 Kubernetes 集群）

## 基本信息

- 验收环境：本机 Docker Desktop Kubernetes（单节点，`docker-desktop` context）
- API 地址：http://127.0.0.1:30000（NodePort 30000）
- 执行时间：2026-04-03
- 执行人：GitHub Copilot
- 触发方式：命令行（`k8s_local_stack.ps1 -Action live-worker`）
- release-check 模式：live-worker
- 执行器类型：host
- token 来源：`New-TemporaryAdminToken` 动态注入 pod SQLite，落盘至 `.tmp_k8s_local_live_token.txt`，验收结束后由脚本自动吊销

## 部署形态

- Helm release：`automation-hub-local`
- Namespace：`automation-hub`
- Profile：`host`
- API Pod：`automation-hub-local-6465c6747f-p2hzx`（Up 4 hours）
- Redis Pod：`automation-hub-local-redis-59f4fbbdc5-f6d5h`（Up 4 hours）
- Worker Pod：`automation-hub-local-worker-5d787d9c5-r5gd9`（Up 4 hours）

## 前置检查

- [x] API 已启动
- [x] Worker 已启动
- [x] Redis 队列可用
- [x] GET /health 返回 200
- [x] GET /auth/me 返回 200
- [x] token 未过期且具备管理员能力

## 执行命令

```powershell
powershell -File automation-hub/scripts/k8s_local_stack.ps1 -Action live-worker -Profile host -LiveWorkerExecutor host
```

## 验收结果

```text
╔══════════════════════════════════════════════════════════╗
║  Automation Hub - Worker Live 验收                      ║
╚══════════════════════════════════════════════════════════╝

执行器模式: host

✅ 通过: 低风险 tool-run 真实消费执行
   工具ID: worker-live-72953a73-low
   运行ID: 7587901a-3ef0-411e-b07e-901dc00c1940

✅ 通过: 高风险 tool-run 审批后执行
   工具ID: worker-live-72953a73-high
   运行ID: 640e8db3-16e4-458c-a1d5-e1c931e8dfc4
   审批ID: 0fc7e022-666f-4c01-90fe-c9215c695ed2

测试完成  ✅ 通过: 2  ❌ 失败: 0
🎉 Worker live 验收通过。
[release-check] all checks passed
[k8s-local] revoking temporary live token
```

- [x] 低风险 tool-run 进入 succeeded
- [x] 高风险 tool-run 在 approve 后进入 succeeded
- [x] `[release-check] all checks passed`

结果判定：**通过**

## 结论

- 是否允许继续发布：是
- 备注：本次验收采用 host 执行器，K8s 单节点集群。真实远端环境 worker 验收（`worker-live-acceptance-2026-03-11-remote.md`）仍待补齐；本记录证明当前代码在本地 K8s 集群 worker 消费链路中可用。
