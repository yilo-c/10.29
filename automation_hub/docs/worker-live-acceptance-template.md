# Worker Live 验收记录模板

用于记录一次真实环境的 worker 消费执行验收结果，建议在部署环境至少保留一份。

## 基本信息

- 验收环境：
- API 地址：
- 执行时间：
- 执行人：
- 触发方式：命令行 / GitHub Actions workflow_dispatch
- release-check 模式：live-worker
- 执行器类型：host / docker
- token 来源：本地文件 / GitHub Secret

## 前置检查

- [ ] API 已启动
- [ ] Worker 已启动
- [ ] Redis 队列可用
- [ ] GET /health 返回 200
- [ ] GET /auth/me 返回 200
- [ ] token 未过期且具备管理员能力

## 执行命令

命令行示例：

```bash
python scripts/release_check.py --mode live-worker --live-api-base <目标地址> --live-token-file <token文件> --live-worker-executor docker
```

说明：

- 生产环境建议优先使用 `--live-worker-executor docker`
- 若当前环境只验证 host executor，可改为 `--live-worker-executor host`

## 验收结果

- [ ] 低风险 tool-run 进入 succeeded
- [ ] 高风险 tool-run 在 approve 后进入 succeeded
- [ ] run stdout_tail 含预期标记
- [ ] approval.decided 审计存在
- [ ] run.executed 审计存在
- [ ] `[release-check] all checks passed`

## 失败信息与处置

- 失败现象：
- 初步原因：
- 已采取处置：
- 是否阻塞发布：是 / 否

## 结论

- 是否允许继续发布：是 / 否
- 备注：