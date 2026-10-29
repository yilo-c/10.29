# M0 Acceptance Record — Live

> M0-WP4 双环境验收记录。
> 记录 `live` 环境的 release_check 验收结果。

## Environment

| Field | Value |
|-------|-------|
| Environment | `live` |
| Date | 2026-05-02 |
| Operator | CI / release_check |
| Values file | `values-prod.yaml` |
| Chart version | 0.1.0 |

## Verification Results

### 1. Health Check

- [ ] `GET /health` returns 200
- [ ] API is reachable at configured base URL

### 2. Auth Flow

- [ ] Bootstrap creates admin token
- [ ] `/auth/me` returns valid identity
- [ ] Token templates exposed (admin_ops, agent, desktop, cronjob, readonly)
- [ ] Token creation works for device
- [ ] Token rotation works (template change + TTL)

### 3. Tools

- [ ] `GET /tools` returns tool list
- [ ] Tool registration works
- [ ] Tool detail retrieval works

### 4. Tool Runs + Approvals

- [ ] Low-risk tool run created
- [ ] High-risk tool run pending approval
- [ ] Tool run exists in list

### 5. Audit Trail

- [ ] `GET /audit` returns events
- [ ] Release gate events logged

### 6. Personal Data

- [ ] Note creation works
- [ ] Note list retrieval works

### 7. Release Gate

- [ ] `release_check.py --mode live` preflight passed
- [ ] All checks passed
- [ ] Release report generated
- [ ] Audit event logged for gate pass/fail

## Result

- **Preflight**: ❓ (needs live deployment)
- **All checks**: ❓ (needs live deployment)
- **Release gate audit**: ❓ (needs live deployment)

## Notes

This is a template record. Actual execution against a live environment
requires a running production deployment with valid admin token.
Run the following to generate actual results:

```bash
python scripts/release_check.py --mode live \
  --live-api-base <live_api_base> \
  --live-token-file /path/to/admin_token \
  --release-report docs/live-acceptance-YYYY-MM-DD-ci.md
```
