# Sprint 1 部署检查清单

## 数据库初始化（如需手动执行）

- [ ] 确认 schema 已创建以下表：
    - [ ] devices
    - [ ] api_tokens
    - [ ] tools
    - [ ] tool_versions
    - [ ] approval_requests
    - [ ] audit_events

- [ ] 保存管理员 token
- [ ] 测试 `/auth/me` 验证 token 有效
- [ ] 运行 live 验收：`python scripts/release_check.py --mode live --live-api-base <目标地址> --live-token-file <token文件>`
- [ ] 确认输出包含 `[release-check] live preflight passed` 与 `[release-check] all checks passed`
- [ ] 运行 worker live 验收：`python scripts/release_check.py --mode live-worker --live-api-base <目标地址> --live-token-file <token文件> --live-worker-executor docker`
- [ ] 确认低风险与高风险 tool-run 都由 worker 消费执行成功
- [ ] 执行 `helm lint deploy/charts/automation-hub`，确认 chart 语法通过
- [ ] 执行 `helm template` 渲染 dev/prod values，确认关键对象与环境变量都正确展开
- [ ] 至少额外渲染以下生产模板并检查结果：
    - [ ] `values-prod-external-redis.yaml`
    - [ ] `values-prod-docker-executor.yaml`
- [ ] 若使用 SQLite `DATABASE_PATH`，确认 API `replicaCount=1`
- [ ] 若关闭内置 Redis，确认 `REDIS_URL` 已显式配置
- [ ] 若 chart 不创建 Secret，确认 `existingSecretName` 已显式配置
## 基础功能测试

### 认证模块

- [ ] 创建新设备
- [ ] 创建新 token（带不同 scopes）
- [ ] 查看 token 模板：`GET /auth/token-templates`
- [ ] 测试 token 轮换：`POST /auth/tokens/{token_id}/rotate`
- [ ] 测试权限检查（访问需要特定 scope 的接口）
- [ ] 吊销 token
- [ ] 验证吊销后无法使用

#### Token 生命周期（推荐）

- [ ] 生产建议设置 `TOKEN_DEFAULT_TTL_DAYS` 并确认新 token 默认过期生效
- [ ] 建立轮换节奏（例如 30 天），避免长期使用 bootstrap/admin token

### 工具注册

- [ ] 注册测试工具（read 级别）
- [ ] 注册高风险工具（exec_high/write 级别）
- [ ] 列出所有工具
- [ ] 查看工具详情
- [ ] 禁用/启用工具

- [ ] 执行 read 级别工具
- [ ] 验证直接入队
- [ ] 查看运行状态
- [ ] 检查 stdout/stderr 日志
- [ ] 验证审计日志记录

#### 高风险工具（需审批）

- [ ] 执行 exec_high 工具
- [ ] 验证返回 pending_approval 状态
- [ ] 查看待审批列表
- [ ] 批准审批请求
- [ ] 验证 Worker 执行
- [ ] 查看审计日志

### 审批系统

- [ ] 创建审批请求
- [ ] 列出待审批
- [ ] 批准请求
- [ ] 拒绝请求
- [ ] 验证状态转换正确
- [ ] 检查审计记录

#### 审批超时（推荐）

- [ ] 生产建议设置 `APPROVAL_TTL_SECONDS`（例如 3600/86400）
- [ ] 建议同时设置 `APPROVAL_EXPIRER_INTERVAL_SECONDS=60`，避免过期审批长期滞留
- [ ] 验证超时 pending 审批会自动标记为 expired，并将相关 run 置为 denied

#### 产物保留与清理（推荐）

- [ ] 生产建议设置 `RUN_ARTIFACT_RETENTION_DAYS=14`，限制 tool-run 产物长期占盘
- [ ] 生产建议设置 `PROPOSAL_ARTIFACT_RETENTION_DAYS=30`，兼顾 proposal 追溯与磁盘占用
- [ ] 建议设置 `ARTIFACT_CLEANER_INTERVAL_SECONDS=3600`，按小时运行后台清理
- [ ] 先执行 `python scripts/cleanup_artifacts.py --dry-run`，确认命中范围符合预期
- [ ] 启用后验证旧 run/proposal 产物会被自动删除，新产物不会误删

### 审计系统

- [ ] 查询所有审计事件
- [ ] 按事件类型筛选
- [ ] 按资源类型筛选
- [ ] 按时间范围筛选
- [ ] 验证关键操作都有记录

## Worker 测试

- [ ] 启动 Worker 服务
- [ ] 执行简单工具
- [ ] 执行需要审批的工具
- [ ] 验证 Host Executor
- [ ] 验证 Docker Executor（docker CLI/socket/image 自检）
- [ ] 测试超时处理
- [ ] 测试失败处理

### DockerExecutor 生产建议

- [ ] 生产建议设置 `DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK=false`
- [ ] worker 启动自检 docker CLI/socket/image；失败应直接退出（避免队列堆积）

## 策略引擎测试

- [ ] 测试 scope 检查
- [ ] 测试工具禁用检查
- [ ] 测试风险级别评估
- [ ] 测试 JSON Schema 验证
- [ ] 测试路径权限检查

## 安全测试

- [ ] 无 token 访问被拒绝
- [ ] 无效 token 被拒绝
- [ ] 已吊销 token 被拒绝
- [ ] 权限不足被拒绝
- [ ] 禁用工具无法执行
- [ ] 未批准的高风险操作被阻止

## 性能测试

- [ ] 并发执行多个工具
- [ ] 长时间运行的工具
- [ ] 大量审计日志查询
- [ ] 工具列表性能

## 文档验证

- [ ] README 准确描述功能
- [ ] RBAC 文档完整
- [ ] 工具规范清晰
- [ ] 审批流程文档准确
- [ ] API 示例可用

## 部署验证

真实环境如果需要一套从镜像、Secret、Helm 到 live / live-worker 的命令顺序，优先使用 [docs/real-env-deployment-commands.md](docs/real-env-deployment-commands.md)。

### Live 验收标准

- [ ] API 与 Worker 均已启动，`GET /health` 返回 200
- [ ] 验证 `GET /auth/me` 返回 200，且 token 未过期
- [ ] 使用 live 模式运行固定门禁入口
- [ ] 使用 live-worker 模式验证 worker 消费低风险与审批后高风险 run
- [ ] 记录本次验收环境地址、执行时间、执行人和结果

真实部署环境建议按固定顺序执行：先 `live`，再 `live-worker --live-worker-executor docker`。完整执行步骤、前置条件和完成定义见 [docs/remote-acceptance-plan.md](docs/remote-acceptance-plan.md)

如果当前目标是直接推进阶段 0，优先使用 [docs/remote-acceptance-checklist.md](docs/remote-acceptance-checklist.md) 逐项执行和留档。

说明：本地 Windows、Docker Desktop 本地栈和本机自部署 Docker 三容器的验收记录，只能作为本机复验依据，不能替代真实部署环境留档。

留档模板： [docs/live-acceptance-template.md](docs/live-acceptance-template.md)

Worker 留档模板： [docs/worker-live-acceptance-template.md](docs/worker-live-acceptance-template.md)

已存在的本机复验记录： [docs/live-acceptance-2026-03-10-local.md](docs/live-acceptance-2026-03-10-local.md)、[docs/worker-live-acceptance-2026-03-10-local.md](docs/worker-live-acceptance-2026-03-10-local.md)、[docs/worker-live-acceptance-2026-03-10-docker-local.md](docs/worker-live-acceptance-2026-03-10-docker-local.md)、[docs/live-acceptance-2026-03-11-docker-self-deployed.md](docs/live-acceptance-2026-03-11-docker-self-deployed.md)、[docs/worker-live-acceptance-2026-03-11-docker-self-deployed.md](docs/worker-live-acceptance-2026-03-11-docker-self-deployed.md)

### Docker

- [ ] 构建 Docker 镜像
- [ ] 运行容器
- [ ] 数据持久化
- [ ] 环境变量配置
- [ ] 若本地 Docker 栈使用非默认 tag，确认 worker 的 `DOCKER_EXECUTOR_IMAGE` 与实际构建镜像一致
- [ ] 若 worker 挂载 docker.sock，确认目标节点运行时确实支持 docker.sock hostPath
- [ ] 若启用产物清理，确认 `data/runs/` 与 `data/proposals/` 挂载在持久卷上，并按保留策略预留容量

本地 Docker Desktop 复用入口：`pwsh -File scripts/docker_local_stack.ps1 -Action up|down|status`

最近一次 Docker 本地栈验收记录： [docs/worker-live-acceptance-2026-03-10-docker-local.md](docs/worker-live-acceptance-2026-03-10-docker-local.md)

### Kubernetes（可选）

- [ ] 应用 K8s 配置
- [ ] 验证服务可访问
- [ ] PVC 挂载正确
- [ ] ConfigMap/Secret 正确
- [ ] 若使用 `workflow_dispatch` 执行 `live-worker`，确认输入的执行器类型与目标集群实际运行时一致
- [ ] 若使用 [deploy/argocd/automation-hub.yaml](deploy/argocd/automation-hub.yaml)，确认默认 values 文件与目标环境一致

## 监控和日志

- [ ] 应用日志正常输出
- [ ] Worker 日志可查看
- [ ] 错误日志包含足够信息
- [ ] 审计日志完整

## CI 与发布门禁

- [ ] Pull Request 已通过 GitHub Actions 中的 Helm 校验和 `smoke`
- [ ] 合并到 `main` 前确认 `regression` 通过
- [ ] 若使用 `workflow_dispatch` 触发 `live-worker`，确认 `live_api_base`、`AUTOMATION_HUB_LIVE_TOKEN` 和 `live_worker_executor` 均已正确提供

## 回滚准备

- [ ] 数据库备份
- [ ] 配置文件备份
- [ ] 回滚步骤文档化

## 已知问题和限制

记录当前版本的已知问题：

- DockerExecutor：需要集群允许挂载 docker.sock 且容器内有 docker CLI；生产建议禁用 host fallback 并启用 preflight
- SQLite：当前 Helm 生产模板仍以 SQLite 文件数据库为默认基线，因此 API 不应扩容到多副本；若需要多副本，应先切换到外部数据库
- Token 生命周期：已支持 expires_at 与轮换；建议启用默认 TTL 并定期轮换（见 docs/token-management.md）
- 产物清理：已支持 run / proposal 目录保留策略；生产建议在 Helm values 中显式设置保留天数并先 dry-run 校验
- 通知机制未实现

## 下一步计划

### Sprint 2

- [ ] 迁移现有脚本到工具注册
- [ ] 审批撤销/重试与后台 expirer
- [ ] runs/tool_runs 产物清理与配额
- [ ] 添加工具依赖管理

### Sprint 3

- [ ] 实现 Repos 索引
- [ ] 集成代码搜索（ripgrep）
- [ ] 语法树解析

### Sprint 4

- [ ] 完善 Proposals 工作流（patch/verify/回滚）
