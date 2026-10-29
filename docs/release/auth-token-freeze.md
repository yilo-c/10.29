# Auth/Token Freeze Contract (M3)

> Freeze date: 2026-05-02
> Status: **FROZEN** — no auth/device/token semantic changes without M3+ approval

## 1. device_id

- `device_id` is a UUID v4 string, assigned at device registration.
- Desktop: generated once during first `POST /auth/bootstrap` or `POST /auth/devices`.
- Stored in `devices` table with `(id, user_id, name, platform, created_at, last_seen_at)`.
- Used in `api_tokens` as `device_id` FK to scope tokens to a device.

## 2. Token Scope Model

- **Scope format**: `resource:action` (e.g., `"note:read"`, `"note:write"`).
- Token templates (admin_ops, agent, cronjob, readonly) define scope bundles.
- Custom scopes per-token via `POST /auth/tokens` with explicit `scopes` array.
- All scopes are validated at token creation; unknown scopes are silently accepted for forward compatibility.

### Bootstrap Scopes

```
news:read, news:write, note:read, note:write, task:read, task:write,
translation:read, translation:write, vocabulary:read, vocabulary:write,
tool:read, tool:write, tool:execute, approval:read, approval:decide,
audit:read, proposal:read, proposal:write, repo:read, repo:write,
agent:use, user:admin
```

### Token Templates

| Template | Scopes |
|----------|--------|
| `admin_ops` | All bootstrap scopes |
| `agent` | agent:use, tool:execute, tool:read |
| `cronjob` | tool:execute, tool:read |
| `readonly` | news:read, note:read, task:read, translation:read, vocabulary:read, tool:read, approval:read, audit:read, repo:read, proposal:read |

## 3. Token Refresh (Rotation)

- **Endpoint**: `POST /auth/tokens/{token_id}/rotate`
- **Semantics**: Revoke old token, issue new token with (potentially different) scopes and expiry.
- Old token's `revoked_at` is set to current time; new token is created with fresh `token_hash`.
- Audit event `auth.token_rotated` is emitted linking old and new token IDs.

## 4. Token Revocation

- **Endpoint**: `DELETE /auth/tokens/{token_id}`
- **Semantics**: Sets `revoked_at` on the target token row.
- Subsequent requests using a revoked token are rejected by `get_current_token` dependency.
- Audit event `auth.token_revoked` is emitted.

## 5. Desktop Token Lifecycle

- Desktop stores token in settings as `automation_hub_api_token`.
- On boot, `automation_hub_client.py` reads token from settings or env vars (`SIYI_AUTOMATION_HUB_API_TOKEN`, `AUTOMATION_HUB_API_TOKEN`).
- Health check uses `/health` (no auth), identity check uses `/auth/me` (Bearer token).
- Token expiry is checked by the server; desktop receives 401 on expired/revoked tokens.

## Freeze Rules

- No new token templates without documented approval.
- No change to scope format (`resource:action`) without M3+ review.
- No change to device_id UUID v4 semantics.
- Token rotation/revocation paths must remain audited.
