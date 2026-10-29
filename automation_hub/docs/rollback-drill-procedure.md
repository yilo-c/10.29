# Rollback Drill Procedure

> M0-WP4 产物。记录回滚演练的标准操作流程。
> 每次发布前应至少执行一次回滚演练。

## When to Run a Rollback Drill

- Before any production release
- After infrastructure changes (e.g., secrets, PVC, RBAC)
- After chart changes (e.g., new `values-*.yaml`)

## Rollback Steps

### Phase 1: Snapshot (Pre-Change)

```bash
# 1. Record current state
kubectl get pods -n <namespace>
kubectl get helmrelease -n <namespace>
kubectl get pvc -n <namespace>

# 2. Database snapshot (SQLite on PVC)
# If direct access is available:
kubectl cp <namespace>/<pod>:/data/automation_hub.sqlite3 ./pre_rollback_snapshot.sqlite3
```

### Phase 2: Apply Change

```bash
# Deploy new version
helm upgrade --install automation-hub deploy/charts/automation-hub \
  --values deploy/charts/automation-hub/values-prod.yaml \
  --set secret.existingSecretName=automation-hub-prod-secret

# Verify change was applied
kubectl rollout status deployment/automation-hub
```

### Phase 3: Rollback

```bash
# Option A: Helm rollback
helm rollback automation-hub 1

# Option B: Reinstall previous version
helm upgrade --install automation-hub deploy/charts/automation-hub \
  --values deploy/charts/automation-hub/values-prod.yaml \
  --set secret.existingSecretName=automation-hub-prod-secret \
  --version <previous-version>

# Restore database
# kubectl cp ./pre_rollback_snapshot.sqlite3 <namespace>/<pod>:/data/automation_hub.sqlite3
kubectl delete pod <pod>  # Force pod restart
```

### Phase 4: Verify

```bash
# 1. Health check
curl -f http://<api-base>/health

# 2. Auth check
python scripts/release_check.py --mode live \
  --live-api-base <api-base> \
  --live-token-file /path/to/admin_token

# 3. Verify data integrity
python scripts/release_check.py --mode smoke
```

## Acceptance Test (test_rollback_drill.py)

The test `tests/test_rollback_drill.py::test_rollback_drill_snapshot_apply_restore_verify` automates:

1. **Snapshot**: Copies the database before changes
2. **Apply**: Registers a new tool
3. **Rollback**: Restores database snapshot + resets session factory
4. **Verify**: Confirms the new tool is gone and audit event count is unchanged

### Running the acceptance test

```bash
cd automation-hub
python -m pytest tests/test_rollback_drill.py -v
```

## Rollback Drill Record

After each drill, record:

| Field | Value |
|-------|-------|
| Date | YYYY-MM-DD |
| Environment | live / live-worker |
| Operator | Name |
| Snapshots taken | Yes/No |
| Rollback method | helm rollback / reinstall |
| Verification result | Pass/Fail |
| Notes | Any issues encountered |

## Database Rollback Advisory

- SQLite on PVC: rollback is coarse (full file restore)
- For PostgreSQL migrations, rollback must include migration reversal
- Always test rollback on non-production first
-->

## Rollback Drill ─ Automation Hub

> Rollback procedure and drill record.

### Procedure

1. **Snapshot**: Copy SQLite DB (`/data/automation_hub.sqlite3`) to backup location.
2. **Apply change**: Deploy new version or register new tool.
3. **Verify change**: Confirm the change is active.
4. **Rollback**: Restore snapshot DB and reset API state.
5. **Verify rollback**: Confirm the tool/version is gone and data integrity is intact.

### Drill Record

| Step | Action | Status | Notes |
|------|--------|--------|-------|
| 1 | Snapshot DB | | |
| 2 | Register new tool | | |
| 3 | Verify tool visible | | |
| 4 | Restore DB snapshot | | |
| 5 | Verify tool removed | | |

### Automated Test

```bash
python -m pytest tests/test_rollback_drill.py -v
```
