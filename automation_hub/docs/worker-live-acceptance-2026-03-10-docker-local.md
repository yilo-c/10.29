# Worker 端到端验收记录 2026-03-10（Docker 本地栈）

## 基本信息

- 验收环境：本机 Docker Desktop 本地栈
- API 地址：http://127.0.0.1:8010
- 执行时间：2026-03-10
- 执行人：GitHub Copilot
- 验收目标：验证 Docker 化 API + Redis + worker 栈下，worker 对 host/docker 两种执行器的真实消费闭环

## 部署形态

- 镜像：automation-hub:docker-local
- Redis 容器：automation-hub-docker-redis
- API 容器：automation-hub-docker-api
- Worker 容器：automation-hub-docker-worker
- Network：automation-hub-docker-net
- Volumes：automation-hub-docker-data、automation-hub-docker-workspace

## 关键配置

- worker 以 root 身份运行，用于访问 docker.sock
- worker 挂载 `/var/run/docker.sock:/var/run/docker.sock`
- `DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK=false`
- `DOCKER_EXECUTOR_IMAGE=automation-hub:docker-local`

说明：如果不显式设置 `DOCKER_EXECUTOR_IMAGE=automation-hub:docker-local`，worker 预检会默认检查 `automation-hub:latest`，并因本地不存在该 tag 而在监听队列前直接退出。

## 验收命令

API live：

```bash
python scripts/release_check.py --mode live --live-api-base http://127.0.0.1:8010 --live-token-file .tmp_docker_live_token.txt
```

worker live，host executor：

```bash
python scripts/release_check.py --mode live-worker --live-api-base http://127.0.0.1:8010 --live-token-file .tmp_docker_live_token.txt --live-worker-executor host
```

worker live，docker executor：

```bash
python scripts/release_check.py --mode live-worker --live-api-base http://127.0.0.1:8010 --live-token-file .tmp_docker_live_token.txt --live-worker-executor docker
```

## 验收结果

结果判定：通过

关键摘要：

```text
[release-check] live preflight passed
[release-check] all checks passed

host executor:
- 低风险 tool-run 真实消费执行：通过
- 高风险 tool-run 审批后执行：通过

docker executor:
- 低风险 tool-run 真实消费执行：通过
- 高风险 tool-run 审批后执行：通过
```

## 暴露并修复的问题

### 1. 运行镜像缺少 docker CLI

- 现象：容器内执行 `docker version` 提示 `docker: not found`
- 根因：运行阶段安装的是 `docker.io`，镜像里只有 `dockerd` 相关组件，没有 `docker` 客户端
- 修复：将 [Dockerfile](../Dockerfile) 运行阶段依赖改为安装 `docker-cli`

### 2. root 模式 worker 无法导入 redis/rq

- 现象：worker 以 root 启动后直接报 `ModuleNotFoundError: No module named 'redis'`
- 根因：Python 依赖原先只复制到 `appuser` 私有目录，切到 root 后模块不可见
- 修复：将 [Dockerfile](../Dockerfile) 中构建产物复制到全局 `/usr/local`

### 3. worker 预检错误拉取默认镜像 tag

- 现象：worker 启动即退出，日志提示 `pull access denied for automation-hub`
- 根因：Docker executor 默认镜像仍是 `automation-hub:latest`，与本地实际构建 tag `automation-hub:docker-local` 不一致
- 修复：在本地 Docker 栈 worker 配置中显式固定 `DOCKER_EXECUTOR_IMAGE=automation-hub:docker-local`

### 4. docker.sock 权限不足

- 现象：worker 预检报 `permission denied while trying to connect to the Docker daemon socket`
- 根因：默认 `appuser` 无权限访问挂载进容器的 docker.sock
- 修复：本地 Docker 栈 worker 改为 root 运行，并保留 `DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK=false`

## 结论

- 本机 Docker Desktop 环境下，Docker 化 API、Redis、worker 栈已经可复用
- host executor 与 docker executor 两条 worker 真实消费链路均已闭环
- 当前本地复用入口为 [scripts/docker_local_stack.ps1](../scripts/docker_local_stack.ps1)