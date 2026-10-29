# Deployment Ownership — Live / Live-Worker Truth Source

> M0-WP1 产物。固定 `live`、`live-worker` 的部署归属、values 选型和 `live_api_base` 真源。
> 后续 release_check 不再依赖人工猜测环境 → values 组合。

## Environment Matrix

| Environment     | Chart Values File                       | Redis      | Docker Executor | Token Template | Scope Set                          |
|-----------------|-----------------------------------------|------------|-----------------|----------------|------------------------------------|
| `live`          | `values-prod.yaml`                      | Internal   | Disabled        | admin_ops      | Full bootstrap scopes              |
| `live-worker`   | `values-prod-docker-executor.yaml`      | External   | Docker (sock)   | agent          | agent:use, tool:execute, tool:read |

## Selection Rules

1. **Each environment maps to exactly one values file** — never combine two prod values files as equals.
2. `values-prod-external-redis.yaml` and `values-prod-minimal.yaml` are **supplemental overrides only**, not standalone environments. They must be combined with a base values file (e.g., `--values values-prod.yaml --values values-prod-external-redis.yaml`).
3. **`live_api_base` truth source:** The API base URL for each environment is stored in the deployment config (Helm values `env.API_BASE_URL` or via external configuration). For release_check:
   - `live` → `verify_system.py --api-base <live_api_base>`
   - `live-worker` → `verify_worker_system.py --api-base <live_api_base> --executor docker`
4. The `live` environment is always verified first. Only after `live` passes should `live-worker` be verified.

## Deployment Commands

### live (API-only)

```bash
helm upgrade --install automation-hub deploy/charts/automation-hub \
  --values deploy/charts/automation-hub/values-prod.yaml \
  --set secret.existingSecretName=automation-hub-prod-secret
```

### live-worker (Docker executor)

```bash
helm upgrade --install automation-hub deploy/charts/automation-hub \
  --values deploy/charts/automation-hub/values-prod-docker-executor.yaml \
  --set secret.existingSecretName=automation-hub-prod-secret
```

## Verification Commands

### live

```bash
python scripts/release_check.py --mode live \
  --live-api-base <live_api_base> \
  --live-token-file /path/to/admin_token
```

### live-worker

```bash
python scripts/release_check.py --mode live-worker \
  --live-api-base <live_api_base> \
  --live-token-file /path/to/admin_token \
  --live-worker-executor docker
```

## When to Add a New Environment

1. A new values file must declare `# 环境归属：` in its header comment.
2. This matrix must be updated with the new row.
3. If the new environment is not a full alias of `live` or `live-worker`, it gets its own row.
