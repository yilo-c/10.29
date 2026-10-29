# Worker Live 验收记录 2026-03-11（真实部署环境）

状态：阻塞

本文件按 [remote-acceptance-plan.md](remote-acceptance-plan.md) 创建，用于承接一次真实部署环境的 `live-worker --live-worker-executor docker` 验收结果。

当前仓库内还没有可核实的远端 worker 验收输出，因此本记录先整理为“可直接执行的预填版本”。拿到真实环境地址、管理员 token 和执行器前提后，直接替换本文中的待填写项并执行即可；只有实际跑通后，才能把状态改成“通过”或“失败”。

## 当前阻塞

2026-03-11 当前工作站已经完成本机自部署 Docker 三容器栈上的 `live-worker --live-worker-executor docker` 验证，但这份结果只能证明本机脚本和本地 Docker 运行时可用，不能替代真实部署环境的 worker 放行证据。

当前仍缺少以下远端条件：

- 可达的真实部署环境 `live_api_base`
- 对应目标环境的有效管理员 token
- 可核实的目标 worker 节点运行时信息与 `docker.sock` 挂载条件

因此，本记录继续保持为“阻塞”，等待真实环境执行后再回填。

2026-03-12 在仓库根目录补跑命令：

```text
d:/BaiduSyncdisk/个人/思忆集/思集/3.25/思忆集/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live-worker --live-worker-executor docker
[release-check] live preflight api: http://127.0.0.1:30000
[release-check] live preflight token file: D:\BaiduSyncdisk\个人\思忆集\思集\3.25\思忆集\automation-hub\.admin_token
[release-check] live preflight failed: token file not found: D:\BaiduSyncdisk\个人\思忆集\思集\3.25\思忆集\automation-hub\.admin_token. use --live-token-file or set AUTOMATION_HUB_TOKEN_FILE
```

结论：当前仍未进入 worker 验收阶段，阻塞点与 live 一致，均为 token 文件缺失与远端地址未配置。

## 执行前替换项

执行前，先把以下占位值替换成真实环境信息：

- `<验收环境>`：例如 `staging` / `pre-prod`
- `<live_api_base>`：真实环境 API 地址
- `<执行人>`：本次执行责任人
- `<token文件>`：本地 token 文件路径
- `<values文件>`：本次部署对应的 Helm values 文件
- `<release>`：当前 Helm release 名称
- `<chart版本或提交>`：当前 chart 版本、镜像 tag 或提交号

同时额外确认：

- 目标 worker 节点支持 `docker.sock` hostPath
- 部署 values 已关闭 `DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK`

## 基本信息

- 验收环境：`<验收环境>`
- API 地址：`<live_api_base>`
- 执行时间：待填写
- 执行人：`<执行人>`
- 触发方式：命令行 / GitHub Actions workflow_dispatch
- release-check 模式：live-worker
- 执行器类型：docker
- token 来源：本地文件 / GitHub Secret
- 目标 Helm values 文件：`<values文件>`
- 当前 release / chart 版本：`<release>` / `<chart版本或提交>`

## 前置检查

- [ ] API 已启动
- [ ] Worker 已启动
- [ ] Redis 队列可用
- [ ] GET /health 返回 200
- [ ] GET /auth/me 返回 200
- [ ] token 未过期且具备管理员能力
- [ ] worker 节点支持 docker.sock hostPath
- [ ] 部署 values 已关闭 DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK

## 执行命令

命令行：

```bash
D:/BaiduSyncdisk/个人/思忆集/思集/运维/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live-worker --live-api-base <live_api_base> --live-token-file <token文件> --live-worker-executor docker
```

GitHub Actions：

1. 打开 [automation-hub/.github/workflows/ci.yaml](../.github/workflows/ci.yaml)
2. 选择 `workflow_dispatch`
3. 设置 `mode=live-worker`
4. 填写 `live_api_base`
5. 设置 `live_worker_executor=docker`
6. 确认仓库 Secret `AUTOMATION_HUB_LIVE_TOKEN` 已存在

本次实际执行：

```text
执行前请替换以下示例：

D:/BaiduSyncdisk/个人/思忆集/思集/运维/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live-worker --live-api-base <live_api_base> --live-token-file <token文件> --live-worker-executor docker

若使用 GitHub Actions：
- mode=live-worker
- live_api_base=<live_api_base>
- live_worker_executor=docker
- secret=AUTOMATION_HUB_LIVE_TOKEN

当前状态：未执行，等待真实环境地址、有效 token 与执行器前提确认
```

补充实际执行（2026-03-12）：

```text
d:/BaiduSyncdisk/个人/思忆集/思集/3.25/思忆集/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live-worker --live-worker-executor docker
exit code: 1
阻塞点: live preflight token file not found（automation-hub/.admin_token）
```

## 验收结果

- [ ] 低风险 tool-run 进入 succeeded
- [ ] 高风险 tool-run 在 approve 后进入 succeeded
- [ ] run stdout_tail 含预期标记
- [ ] approval.decided 审计存在
- [ ] run.executed 审计存在
- [x] 已执行 release-check（preflight 阶段失败）

结果判定：阻塞，未执行

## 关键运行证据

- 低风险 run_id：待填写
- 低风险 run 终态：待填写
- 高风险 run_id：待填写
- 高风险 approval_id：待填写
- 高风险 run 终态：待填写
- stdout_tail 摘要：待填写
- 审计摘要：待填写

## 失败信息与处置

- 失败现象：当前没有可达的真实部署环境入口，无法生成远端 worker live 证据
- 初步原因：缺少目标环境 API 地址、管理员 token，以及可核实的远端执行器前提
- 已采取处置：已先完成本机自部署 Docker 三容器栈的 worker 验证，记录见 [worker-live-acceptance-2026-03-11-docker-self-deployed.md](worker-live-acceptance-2026-03-11-docker-self-deployed.md)；本文件继续保留为远端待回填记录
- 是否阻塞发布：是，当前无法形成真实部署环境 `live-worker --live-worker-executor docker` 成功证据

## 结论

- 是否允许继续发布：否，直到真实部署环境 worker live 完成
- 备注：本机自部署 Docker 验证可作为本地复验记录，但不能关闭远端环境待办

## 回填要求

执行完成后，至少补齐以下内容：

1. 真实执行命令或 workflow_dispatch 参数
2. 低风险 run 与高风险 run 的 ID、终态和关键输出
3. approval.decided 与 run.executed 的审计摘要
4. 最终是否允许继续发布

## 执行后最少回填内容

执行完成后，至少把下面这段替换成真实结果：

```text
执行时间：<YYYY-MM-DD HH:MM TZ>
执行人：<执行人>
API 地址：<live_api_base>
低风险 run_id：<run_id>
高风险 run_id：<run_id>
approval_id：<approval_id>
结果：通过 / 失败
关键输出：
<stdout_tail 摘要>
<approval.decided 审计摘要>
<run.executed 审计摘要>
[release-check] all checks passed
```