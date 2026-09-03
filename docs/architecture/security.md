# Security Model

## Authentication

### JWT via httpOnly session cookie (web UI)

- Algorithm: HS256, key from `SECRET_KEY` env variable.
- Token lifetime: `ACCESS_TOKEN_EXPIRE_MINUTES` (default 480 min).
- Claims: `sub` (user UUID), `tenant_id`, `exp`.
- Issued by `POST /api/auth/token` (OAuth2 password flow), which sets the JWT as an **httpOnly, `SameSite=Lax` cookie** (`ebm_session`) rather than returning it for client-side storage. JavaScript never has access to the token — it cannot be exfiltrated via XSS and is attached automatically by the browser on same-origin requests.
- `POST /api/auth/logout` clears the cookie server-side.
- `COOKIE_SECURE` (env, default `false`): set to `true` once TLS is terminated in front of the app — otherwise the `Secure` cookie attribute would cause browsers to silently drop the cookie over plain HTTP.
- Non-browser clients may still authenticate with `Authorization: Bearer <token>` (the endpoint also returns the token in the JSON body for this purpose); `get_current_user` accepts either the cookie or the header, checking the cookie first.

### Server-side page guards

Every protected page route (`/dashboard`, `/analyse`, `/patienten`, `/fallakten`, `/admin`) validates the session cookie **before** rendering the Jinja2 template (`src/app/main.py`, dependency `get_current_user_optional`). An unauthenticated request receives an HTTP 302 to `/login` and never receives the protected page's HTML at all — there is no client-side-only redirect that could be raced or paused via browser dev tools. `/admin` additionally verifies `role == "admin"`, redirecting other roles to `/dashboard`. `/login` itself redirects an already-authenticated session straight to `/dashboard`.

### API keys (external programs)

- Format: `ebm_live_<32-byte urlsafe token>` (prefix + `secrets.token_urlsafe(32)`).
- Storage: only SHA-256 hash stored in `api_keys.key_hash`; plaintext returned once at creation.
- Lookup: `SELECT … WHERE key_hash = sha256(raw_key)` on every request.
- Expiry: optional `expires_at`; expired keys are rejected.
- Revocation: soft-delete (`is_active = False`, `revoked_at` set).
- Scopes: `analysis`, `interop`, `patients`, `cases` — enforced by `require_scope()` dependency.

### Internal channel

`POST /api/interop/internal/analyze` is authenticated via a shared secret (`INTERNAL_API_KEY` env variable, minimum 32 characters). Intended for container-internal calls from the MCP server only. Not exposed on the public network in production deployments.

---

## Tenant isolation

Every database table carries `tenant_id`. Application code adds a `WHERE tenant_id = current_user.tenant_id` clause to every query. There is no row-level security in PostgreSQL (no per-user RLS policies). Consequence: a bug that leaks `tenant_id` across authentication would expose cross-tenant data — defence-in-depth is the auth layer.

---

## Password storage

bcrypt via `passlib`. Work factor follows passlib defaults (~12 rounds). Passwords are never logged.

---

## Audit trail

Every GOP decision (accept/reject) and every analysis run creates an immutable row in `audit_logs`. The table has no `UPDATE`/`DELETE` grants in production deployments. It satisfies § 203 StGB (medical confidentiality) and GDPR Article 5(2) accountability requirements.

---

## Data minimisation

- Instant analysis results (Valkey) expire after 24 hours (configurable).
- GDT export cache expires after 1 hour.
- `report_text` is stored in `case_files` in plaintext. For deployments with heightened confidentiality requirements, consider PostgreSQL column-level encryption or an external KMS.

---

## Network exposure

In the default Docker Compose setup:

| Service | Exposed port | Notes |
|---|---|---|
| FastAPI app | 4244 (host) | Reverse-proxy target |
| MCP server | internal only | No host port mapping |
| PostgreSQL | internal only | |
| ChromaDB | internal only | |
| Neo4j | internal only | |
| Valkey | internal only | |

Only the FastAPI app port is exposed to the host. All other services communicate over the internal Docker network.

---

## Secrets checklist

| Variable | Description | Must change before production |
|---|---|---|
| `SECRET_KEY` | JWT signing key | Yes |
| `COOKIE_SECURE` | Marks the session cookie `Secure` (HTTPS-only) | Yes — once TLS is terminated |
| `INTERNAL_API_KEY` | MCP → FastAPI shared secret | Yes |
| `MCP_SECRET` | Client → MCP server shared secret | Yes |
| `CHROMA_TOKEN` | ChromaDB auth token | Yes |
| `POSTGRES_PASSWORD` | Database password | Yes |
| `NEO4J_PASSWORD` | Neo4j password | Yes |
