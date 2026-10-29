# Release Gate Evidence

> M0-WP3 产物。记录 release gate 的可复验输出与审计证据。
> 至少一条 live 放行链路留有可复验 gate 输出与审计证据。

## Gate Components

| Component | Location | Output Type | Verification |
|-----------|----------|-------------|--------------|
| Smoke tests | `release_check.py --mode smoke` | pytest exit code + report | `tests/test_release_check.py` |
| Full regression | `release_check.py --mode full` | pytest exit code + report | All `tests/` pass |
| Live API verification | `release_check.py --mode live` | verify_system.py output + report | `verify_system.py` exit code 0 |
| Live worker verification | `release_check.py --mode live-worker` | verify_worker_system.py output + report | `verify_worker_system.py` exit code 0 |
| Desktop gate | `release_check.py --with-desktop-gate` | desktop_quality_gate.py output | Suite exit code 0 |
| Audit trail | POST `/audit/events` (release.gate) | Audit event in DB | Queryable via GET `/audit` |

## Reproducible Gate Output

Every `release_check.py` execution produces:

1. **Exit code**: 0 = all checks passed, 1 = pytest failure, 2 = preflight/runtime failure
2. **Release report**: `release_report.md` (configurable via `--release-report`)
3. **Audit event**: `release.gate` event logged to API audit trail (live modes only)
4. **Stdout**: Structured output with `[release-check]` prefix for log collection

## Audit Evidence Traceability

### What generates audit events

| Action | Event Type | Source |
|--------|------------|--------|
| Release gate preflight passed | `release.gate` | `release_check.py` → POST `/audit/events` |
| Release gate passed | `release.gate` | `release_check.py` → POST `/audit/events` |
| Release gate failed | `release.gate` | `release_check.py` → POST `/audit/events` |
| Token created | `auth.token_created` | API auth router |
| Token rotated | `auth.token_rotated` | API auth router |
| Tool run executed | `run.executed` | API runs router |
| Proposal applied | `proposal.applied` | API proposals router |
| Approval decided | `approval.decided` | API approvals router |

### Querying audit evidence

```bash
# Query release gate events
curl -H "Authorization: Bearer $TOKEN" "$API_BASE/audit?event_type=release.gate"

# Query all events for a release
curl -H "Authorization: Bearer $TOKEN" "$API_BASE/audit?resource_type=release&limit=50"
```

## Live Release Chain Example

A complete reproducible release chain:

1. `release_check.py --mode live --live-api-base <url> --live-token-file ./admin_token`
2. Script runs preflight (health + auth check) → logs `release.gate` preflight_passed
3. Script runs `verify_system.py` → API operations generate audit events
4. Script runs desktop gate → logs individual check results
5. Script emits release report → `release_report.md`
6. Script logs `release.gate` passed/failed → queryable via GET `/audit`

All evidence is reproducible by re-running the same command against the same environment.
