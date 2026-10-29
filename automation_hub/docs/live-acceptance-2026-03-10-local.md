# Live 验收记录 2026-03-10（本地）

## 基本信息

- 验收环境：本地开发环境
- API 地址：http://127.0.0.1:8765
- 执行时间：2026-03-10
- 执行人：GitHub Copilot
- 触发方式：命令行
- release-check 模式：live
- token 来源：临时 bootstrap token 文件

## 前置检查

- [x] API 已启动
- [ ] Worker 已单独启动
- [x] GET /health 返回 200
- [x] GET /auth/me 返回 200
- [x] token 未过期且具备管理员能力

说明：本次 live 验收覆盖 API、鉴权、工具注册、tool-run 入队、审批与审计链路；未单独启动 worker，因此不覆盖队列消费执行阶段。

## 执行命令

```bash
python scripts/release_check.py --mode live --live-api-base http://127.0.0.1:8765 --live-token-file .tmp_live_token.txt
```

## 预检结果

- [x] token 文件存在
- [x] live preflight 输出通过
- [x] 未出现 /health 不可达
- [x] 未出现 /auth/me 401 / 403

关键输出摘要：

```text
[release-check] live preflight api: http://127.0.0.1:8765
[release-check] live preflight token file: .tmp_live_token.txt
[release-check] live preflight passed
```

## 验收结果

- [x] [release-check] live preflight passed
- [x] [release-check] all checks passed
- [x] verify_system.py 汇总失败数为 0

结果判定：通过

## 覆盖摘要

- 获取当前用户信息：通过
- Token 管理：通过
- 创建工具：通过
- 列出工具：通过
- 禁用工具：通过
- 执行低风险工具：通过，状态为 queued
- 执行高风险工具：通过，返回 approval_id
- 列出审批请求：通过
- 查询审计日志：通过

## 处置记录

- 初始失败现象：低风险 tool-run 在 live 模式下返回 500
- 根因：RQ 入队调用把业务参数 args 作为保留关键字传入，导致 enqueue 创建 job 时把 dict 误判为 args 列表
- 处置：修复 [api/routes/tool_runs.py](../api/routes/tool_runs.py) 的 enqueue 调用为位置参数，并补充 [tests/test_api_execution_flow.py](../tests/test_api_execution_flow.py) 的回归断言
- 修复后结果：live 通过，regression 通过（38 passed）

## 结论

- 是否允许继续发布：是
- 备注：若要把这份记录作为部署环境放行依据，仍建议在真实部署环境再执行一次同样的 live 门禁，并单独覆盖 worker 消费执行链路。

## 补充记录：本地主库重建后复验

- 验收环境：本地开发环境（Windows，主库重建后）
- API 地址：http://127.0.0.1:8000
- 执行时间：2026-03-10
- 执行人：GitHub Copilot
- token 来源：bootstrap 后立即签发的 `admin_ops` token，随后已撤销原始 bootstrap token

执行命令：

```bash
python scripts/release_check.py --mode live --live-api-base http://127.0.0.1:8000 --live-token-file .tmp_live_token.txt
```

结果摘要：

- [x] live preflight 通过
- [x] verify_system.py 10 项全部通过
- [x] 低风险 tool-run 成功创建并进入 queued
- [x] 高风险 tool-run 成功创建并返回 approval_id
- [x] 审计查询返回成功

补充结论：本地主库重建、bootstrap、管理员 token 补发与旧 bootstrap token 撤销后的 API 主链路可正常工作。