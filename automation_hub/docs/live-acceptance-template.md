# Live 验收记录模板

用于记录一次真实环境的 live 门禁执行结果，建议每次部署或联调验收后都保留一份副本。

## 基本信息

- 验收环境：
- API 地址：
- 执行时间：
- 执行人：
- 触发方式：命令行 / GitHub Actions workflow_dispatch
- release-check 模式：live
- token 来源：本地文件 / GitHub Secret

## 前置检查

- [ ] API 已启动
- [ ] Worker 已启动
- [ ] `GET /health` 返回 200
- [ ] `GET /auth/me` 返回 200
- [ ] token 未过期且具备管理员能力

## 执行命令

命令行示例：

```bash
python scripts/release_check.py --mode live --live-api-base <目标地址> --live-token-file <token文件>
```

GitHub Actions 手动触发示例：

1. 打开 `Automation Hub Regression` 工作流。
2. 点击 `Run workflow`。
3. 选择 `mode=live`。
4. 填写 `live_api_base`。
5. 确认仓库 Secret `AUTOMATION_HUB_LIVE_TOKEN` 已配置。

本次实际执行：

```text
<粘贴本次实际命令或 workflow_dispatch 参数>
```

## 预检结果

- [ ] token 文件存在或 Secret 已注入
- [ ] live preflight 输出通过
- [ ] 未出现 `/health` 不可达
- [ ] 未出现 `/auth/me` 401 / 403

关键输出摘要：

```text
<粘贴 preflight 关键输出>
```

## 验收结果

- [ ] `[release-check] live preflight passed`
- [ ] `[release-check] all checks passed`
- [ ] `verify_system.py` 汇总失败数为 0

结果判定：通过 / 失败

## 失败信息与处置

- 失败现象：
- 初步原因：
- 已采取处置：
- 是否阻塞发布：是 / 否

## 结论

- 是否允许继续发布：是 / 否
- 备注：
