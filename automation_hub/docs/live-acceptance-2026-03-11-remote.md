# Live 验收记录 2026-03-11（真实部署环境）

状态：阻塞

本文件按 [remote-acceptance-plan.md](remote-acceptance-plan.md) 创建，用于承接一次真实部署环境的 `live` 验收结果。

当前仓库内还没有可核实的远端执行输出，因此本记录先整理为“可直接执行的预填版本”。拿到真实环境地址和管理员 token 后，直接替换本文中的待填写项并执行即可；只有实际跑通后，才能把状态改成“通过”或“失败”。

## 当前阻塞

2026-03-11 在当前工作站尝试进入真实部署环境 `live` 验收前，先检查了本机可见的 Kubernetes / Helm 环境与仓库内可复用入口，结论如下：

- 当前仅发现一个 kube context：`docker-desktop`
- 当前 `docker-desktop` API Server 也不可达，`kubectl get namespace` 与 `helm list -A` 均因 cluster unreachable 失败
- 当前仓库文档和工作区内没有可直接复用的真实环境 `live_api_base`
- 当前工作区也没有可直接复用的远端 token 文件或可见的 `AUTOMATION_HUB_LIVE_TOKEN` 注入入口

因此，本次只能先把远端 live 留档骨架和阻塞信息落盘，不能伪造执行结果。

2026-03-12 在仓库根目录补跑命令：

```text
d:/BaiduSyncdisk/个人/思忆集/思集/3.25/思忆集/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live
[release-check] live preflight api: http://127.0.0.1:30000
[release-check] live preflight token file: D:\BaiduSyncdisk\个人\思忆集\思集\3.25\思忆集\automation-hub\.admin_token
[release-check] live preflight failed: token file not found: D:\BaiduSyncdisk\个人\思忆集\思集\3.25\思忆集\automation-hub\.admin_token. use --live-token-file or set AUTOMATION_HUB_TOKEN_FILE
```

结论：阻塞原因已收敛为“远端地址与 token 文件均未提供”，需要按计划补齐 `--live-api-base` 与 `--live-token-file` 才能继续。

## 执行前替换项

执行前，先把以下占位值替换成真实环境信息：

- `<验收环境>`：例如 `staging` / `pre-prod`
- `<live_api_base>`：真实环境 API 地址
- `<执行人>`：本次执行责任人
- `<token文件>`：本地 token 文件路径
- `<values文件>`：本次部署对应的 Helm values 文件
- `<release>`：当前 Helm release 名称
- `<chart版本或提交>`：当前 chart 版本、镜像 tag 或提交号

如果采用 GitHub Actions `workflow_dispatch`，则把 `<token文件>` 替换为 `AUTOMATION_HUB_LIVE_TOKEN` 的 Secret 方案。

## 基本信息

- 验收环境：`<验收环境>`
- API 地址：`<live_api_base>`
- 执行时间：待填写
- 执行人：`<执行人>`
- 触发方式：命令行 / GitHub Actions workflow_dispatch
- release-check 模式：live
- token 来源：本地文件 / GitHub Secret
- 目标 Helm values 文件：`<values文件>`
- 当前 release / chart 版本：`<release>` / `<chart版本或提交>`

## 前置检查

- [ ] API 已启动
- [ ] Worker 已启动
- [ ] GET /health 返回 200
- [ ] GET /auth/me 返回 200
- [ ] token 未过期且具备管理员能力

## 执行命令

命令行：

```bash
D:/BaiduSyncdisk/个人/思忆集/思集/运维/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live --live-api-base <live_api_base> --live-token-file <token文件>
```

GitHub Actions：

1. 打开 [automation-hub/.github/workflows/ci.yaml](../.github/workflows/ci.yaml)
2. 选择 `workflow_dispatch`
3. 设置 `mode=live`
4. 填写 `live_api_base`
5. 确认仓库 Secret `AUTOMATION_HUB_LIVE_TOKEN` 已存在

本次实际执行：

```text
执行前请替换以下示例：

D:/BaiduSyncdisk/个人/思忆集/思集/运维/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live --live-api-base <live_api_base> --live-token-file <token文件>

若使用 GitHub Actions：
- mode=live
- live_api_base=<live_api_base>
- secret=AUTOMATION_HUB_LIVE_TOKEN

当前状态：未执行，等待真实环境地址与有效 token
```

补充实际执行（2026-03-12）：

```text
d:/BaiduSyncdisk/个人/思忆集/思集/3.25/思忆集/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live
exit code: 1
阻塞点: live preflight token file not found（automation-hub/.admin_token）
```

## 预检结果

- [ ] token 文件存在或 Secret 已注入
- [x] 已执行 live preflight（失败）
- [ ] 未出现 /health 不可达
- [ ] 未出现 /auth/me 401 / 403

关键输出摘要：

```text
[release-check] live preflight api: <live_api_base>
[release-check] live preflight token file: <token文件>
[release-check] live preflight passed
<verify_system.py 关键输出摘要>
[release-check] all checks passed

2026-03-12 实际输出摘要：
[release-check] live preflight api: http://127.0.0.1:30000
[release-check] live preflight token file: D:\BaiduSyncdisk\个人\思忆集\思集\3.25\思忆集\automation-hub\.admin_token
[release-check] live preflight failed: token file not found
```

## 验收结果

- [ ] [release-check] live preflight passed
- [ ] [release-check] all checks passed
- [ ] verify_system.py 汇总失败数为 0

结果判定：阻塞，未执行

## 失败信息与处置

- 失败现象：当前工作站没有可达的真实部署环境入口，且唯一可见的 `docker-desktop` context 也不可达
- 初步原因：缺少可用的远端 `live_api_base` / token，且当前 kube context 不是可用远端环境
- 已采取处置：已完成远端留档文件创建，并把当前阻塞信息写入本记录，等待补充真实环境地址与管理员 token 后继续执行
- 是否阻塞发布：是，当前无法形成真实部署环境 `live` 成功证据

## 结论

- 是否允许继续发布：否，直到真实部署环境 `live` 验收完成
- 备注：下一步需要提供可达的真实环境 API 地址，以及具备管理员能力的有效 token 文件或 GitHub Secret 注入路径

## 回填要求

执行完成后，至少补齐以下内容：

1. 目标环境名称和 API 地址
2. 实际执行命令或 workflow_dispatch 参数
3. preflight 关键输出
4. 最终结果判定
5. 若失败，记录失败现象、处置和是否阻塞发布

## 执行后最少回填内容

执行完成后，至少把下面这段替换成真实结果：

```text
执行时间：<YYYY-MM-DD HH:MM TZ>
执行人：<执行人>
API 地址：<live_api_base>
结果：通过 / 失败
关键输出：
[release-check] live preflight passed
<verify_system.py 摘要>
[release-check] all checks passed
```