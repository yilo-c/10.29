# Worker 端到端验收记录 2026-03-11（本机自部署 Docker 三容器栈）

## 基本信息

- 验收环境：本机 Docker Desktop 自部署三容器栈
- API 地址：http://127.0.0.1:8010
- 执行时间：2026-03-11
- 执行人：GitHub Copilot
- 验收目标：验证自部署 Docker API + Redis + worker 栈下，worker 的 Docker executor 实际消费闭环

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

## 验收命令

```bash
python automation-hub/scripts/release_check.py --mode live-worker --live-api-base http://127.0.0.1:8010 --live-token-file D:/BaiduSyncdisk/个人/思忆集/思集/运维/automation-hub/.tmp_self_deployed_live_token.txt --live-worker-executor docker
```

## 验收结果

- [x] live preflight 通过
- [x] 低风险 tool-run 真实消费执行通过
- [x] 高风险 tool-run 审批后执行通过
- [x] worker 验收脚本 2 项全部通过
- [x] [release-check] all checks passed

结果判定：通过

## 关键结果摘要

```text
执行器模式: docker

低风险 tool-run 真实消费执行
- 运行ID: 30bbc876-c1dc-4e58-a91a-782b39d1b24f

高风险 tool-run 审批后执行
- 运行ID: 18de8d31-97c4-49d2-ae82-f7893f675afa
- 审批ID: 21db6624-d70a-4b32-bc87-6db8ab75e134

测试完成
✅ 通过: 2
❌ 失败: 0
[release-check] all checks passed
```

## 结论

- 本机自部署 Docker 三容器栈下，worker 的 Docker executor 实际消费链路已经闭环
- 当前脚本入口 [scripts/docker_local_stack.ps1](../scripts/docker_local_stack.ps1) 可直接复用为本机 Docker 验收环境
- 这份记录可作为本机自部署 Docker worker 验收留档；若要关闭真实远端环境待办，仍需单独回填远端验收文件