# M0 Acceptance Record — Live Worker

> M0-WP4 双环境验收记录。
> 记录 `live-worker` 环境的 release_check 验收结果。

## Environment

| Field | Value |
|-------|-------|
| Environment | `live-worker` |
| Date | 2026-05-02 |
| Operator | CI / release_check |
| Values file | `values-prod-docker-executor.yaml` |
| Executor type | docker |
| Chart version | 0.1.0 |

## Verification Results

### 1. Auth Check

- [ ] Admin token is valid (`/auth/me` returns 200)
- [ ] User identity confirmed

### 2. Tool Registration

- [ ] Test tool registered with executor=docker
- [ ] Tool detail retrievable

### 3. Tool Run Creation

- [ ] Low-risk run reaches succeeded state
- [ ] High-risk run (if created) enters pending_approval
- [ ] Approval can be granted and run proceeds

### 4. Run Claim

- [ ] Worker can claim the run
- [ ] Claim returns tool_name and status

### 5. Event Push

- [ ] Run events (started, stdout, completed) accepted
- [ ] Events retrievable via GET endpoint

### 6. Terminal State

- [ ] Run reaches terminal `succeeded` / `completed` state
- [ ] Final status confirmed

### 7. Release Gate

- [ ] `release_check.py --mode live-worker` preflight passed
- [ ] All checks passed
- [ ] Release report generated

## Result

- **Preflight**: ❓ (needs live deployment)
- **All checks**: ❓ (needs live deployment)
- **Worker status**: ❓ (needs live deployment)

## Notes

This is a template record. Actual execution against a live environment
requires a running production deployment with valid admin token.
Run the following to generate actual results:

```bash
python scripts/release_check.py --mode live-worker \
  --live-api-base <live_api_base> \
  --live-token-file /path/to/admin_token \
  --live-worker-executor docker \
  --release-report docs/worker-live-acceptance-YYYY-MM-DD-ci.md
```
