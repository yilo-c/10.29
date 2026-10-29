<!-- 模块说明：Docker Desktop 本地 Kubernetes 操作手册。 -->

# 本地 Kubernetes 操作手册

本文档只覆盖 Docker Desktop 本地 Kubernetes，也就是当前仓库已经验证过的 `docker-desktop` 单节点集群路径。

目标不是解释 Helm 基础概念，而是提供一套稳定、可重复的本地操作顺序，覆盖两种本地 profile：

- `host`：worker 不挂载 `docker.sock`，适合默认本地联调。
- `docker-executor`：worker 挂载 `docker.sock`，适合验证 Kubernetes 内的 Docker executor 真执行链路。

## 前置条件

开始前先确认：

1. `kubectl config current-context` 返回 `docker-desktop`。
2. Helm 可用。
3. 本地镜像 `automation-hub:docker-local` 已存在。
4. 工作区 Python 环境可用，能运行 [scripts/release_check.py](../scripts/release_check.py)。

可复用入口：

- 脚本入口： [scripts/k8s_local_stack.ps1](../scripts/k8s_local_stack.ps1)
- values 文件： [deploy/charts/automation-hub/values-local-docker-desktop.yaml](../deploy/charts/automation-hub/values-local-docker-desktop.yaml)
- docker-executor values 文件： [deploy/charts/automation-hub/values-local-docker-desktop-docker-executor.yaml](../deploy/charts/automation-hub/values-local-docker-desktop-docker-executor.yaml)
- VS Code 任务： [../../.vscode/tasks.json](../../.vscode/tasks.json)

本地固定访问地址：

- API: `http://127.0.0.1:30000`
- 健康检查: `http://127.0.0.1:30000/health`

## 推荐选择

如果只是要确认本地 K8s 可用，优先用 `host` profile。

如果要验证 worker 在 Kubernetes 里真正走 Docker executor，才切到 `docker-executor` profile。

## Host Profile

### 推荐顺序

推荐直接执行 VS Code 聚合任务：

- `automation-hub: k8s-host-flow`

这个 flow 顺序等价于：

1. `automation-hub: k8s-host-preflight`
2. `automation-hub: k8s-host-install`
3. `automation-hub: k8s-host-live`
4. `automation-hub: k8s-host-worker-live`
5. `automation-hub: k8s-host-status`

命令行等价入口：

```powershell
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action preflight -Profile host
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action install -Profile host
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action live -Profile host
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action live-worker -Profile host -LiveWorkerExecutor host
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action status -Profile host
```

### 适用场景

- 本地 chart 安装验证
- API 联调
- worker 基础消费链路验证
- 不需要 `docker.sock` 的本地开发场景

### 当前约束

- 该 profile 默认不挂载 `docker.sock`
- `DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK=true`
- `live-worker` 只应该配 `-LiveWorkerExecutor host`

## Docker-Executor Profile

### 推荐顺序

推荐直接执行 VS Code 聚合任务：

- `automation-hub: k8s-docker-flow`

这个 flow 顺序等价于：

1. `automation-hub: k8s-docker-preflight`
2. `automation-hub: k8s-docker-install`
3. `automation-hub: k8s-docker-live`
4. `automation-hub: k8s-docker-worker-live`
5. `automation-hub: k8s-docker-status`

命令行等价入口：

```powershell
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action preflight -Profile docker-executor
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action install -Profile docker-executor
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action live -Profile docker-executor
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action live-worker -Profile docker-executor -LiveWorkerExecutor docker
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action status -Profile docker-executor
```

### 适用场景

- 验证 Kubernetes 内 worker 的 Docker executor
- 检查 `docker.sock` 挂载和 host fallback 关闭后的真实执行路径
- 对齐未来生产 Docker executor 环境的行为预期

### 当前约束

- worker 必须挂载 `docker.sock`
- `DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK=false`
- worker 需要 root/privileged 运行
- 当前集群必须允许 `docker.sock` hostPath 挂载

## 常用动作

### 状态检查

查看 release、pod、service、deployment、PVC 和当前推断 profile：

```powershell
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action status -Profile host
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action status -Profile docker-executor
```

脚本会同时输出：

- `selected profile`
- `current release profile`
- NodePort 访问地址

### 重建

如果本地 PVC 数据、SQLite 状态或 release 资源需要整体重置，使用：

```powershell
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action reset -Profile host
pwsh -File automation-hub/scripts/k8s_local_stack.ps1 -Action reset -Profile docker-executor
```

`reset` 会：

1. 卸载本地 release
2. 删除 release PVC
3. 按传入 profile 重装
4. 等待 `/health` 恢复

注意：该动作会清空 PVC 内的 SQLite 数据和运行产物。

### 单步排障

如果不想跑完整 flow，可以只跑以下单步：

- `preflight`：检查 context、values、secret、Redis、docker socket 前置条件
- `install`：安装或升级 release
- `live`：做 API 验收
- `live-worker`：做 worker 验收
- `status`：查看当前运行态

## 验收标准

### API live 通过标准

至少同时满足：

1. 输出包含 `live preflight passed`
2. 输出包含 `all checks passed`
3. `verify_system.py` 汇总 0 失败

### Worker live 通过标准

至少同时满足：

1. 低风险 tool-run 进入成功终态
2. 高风险 tool-run 在审批通过后进入成功终态
3. 输出包含 `all checks passed`

留档模板：

- [live-acceptance-template.md](live-acceptance-template.md)
- [worker-live-acceptance-template.md](worker-live-acceptance-template.md)

已验证记录：

- [live-acceptance-2026-03-10-local.md](live-acceptance-2026-03-10-local.md)
- [worker-live-acceptance-2026-03-10-local.md](worker-live-acceptance-2026-03-10-local.md)
- [worker-live-acceptance-2026-03-10-docker-local.md](worker-live-acceptance-2026-03-10-docker-local.md)

## 常见判断

### 什么时候用 host

当你只是想确认本地 Helm 安装、API、worker 基础链路和默认 K8s 开发路径是否正常时，用 `host`。

### 什么时候用 docker-executor

当你需要确认 worker 不会退回 host fallback，而是确实在 Kubernetes 内走 Docker executor 时，用 `docker-executor`。

### 什么时候先 reset

当 status 显示 release profile 与预期不一致，或者本地 SQLite/PVC 状态明显脏了时，先 `reset` 再重跑 flow。

## 文档关系

- 仓库级总览： [../../README.md](../../README.md)
- 主应用说明： [../README.md](../README.md)
- Helm 部署说明： [../../docs/helm-deployment.md](../../docs/helm-deployment.md)
