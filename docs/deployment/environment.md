# Environment Variables

Copy `.env.example` to `.env` and adjust all values before first start.

---

## Application (`app`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | yes | — | JWT signing key (min 32 chars, random) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | no | `480` | JWT lifetime in minutes |
| `ENVIRONMENT` | no | `development` | `development` \| `production` |
| `INTERNAL_API_KEY` | yes | `changeme_internal_ebm_key_min32` | Shared secret for MCP→FastAPI internal channel |
| `APP_INTERNAL_URL` | no | `http://app:8000` | Base URL used by MCP server to reach the app |
| `COOKIE_SECURE` | no | `false` | Set `true` once TLS is terminated in front of the app — marks the session cookie `Secure` (HTTPS-only) |

### Database

`POSTGRES_USER`/`PASSWORD` is the cluster bootstrap superuser — used only for running Alembic migrations (DDL like `CREATE POLICY` needs elevated privileges). The running app queries through a separate, restricted role instead: a Postgres superuser bypasses row-level security unconditionally, so the app must **not** connect as one for the RLS tenant-isolation policies to actually apply.

| Variable | Required | Description |
|---|---|---|
| `POSTGRES_USER` | yes | PostgreSQL superuser name (migrations only) |
| `POSTGRES_PASSWORD` | yes | PostgreSQL superuser password |
| `POSTGRES_DB` | yes | Database name |
| `DATABASE_URL` | yes | Superuser DSN, used by `alembic upgrade head` at container startup |
| `APP_DB_PASSWORD` | yes | Password for the restricted `ebm_app` role — set on this role at every container start (see `app-entrypoint.sh`), never embedded in a migration file |
| `APP_DATABASE_URL` | yes | Restricted-role DSN — what the running app actually queries through |

### Valkey (Redis-compatible)

| Variable | Required | Description |
|---|---|---|
| `VALKEY_URL` | yes | e.g. `redis://valkey:6379/0` |

### ChromaDB

| Variable | Required | Description |
|---|---|---|
| `CHROMA_HOST` | yes | ChromaDB hostname |
| `CHROMA_PORT` | no | `8000` |
| `CHROMA_TOKEN` | yes | ChromaDB auth token |

### LLM provider

| Variable | Required | Description |
|---|---|---|
| `LLM_PROVIDER` | yes | `ollama` |
| `OLLAMA_BASE_URL` | yes | e.g. `http://ollama:11434` or `https://api.moe-sovereign.org` |
| `OLLAMA_MODEL` | yes | Default model, e.g. `qwen3:35b` or `qwen3.6:35b@N04-RTX` |

!!! note "OpenAI-compatible APIs"
    Set `LLM_PROVIDER=ollama` even for OpenAI-compatible APIs. The service auto-detects the wire format via a one-time probe of `/api/tags`.

### MCP client (app → MCP server)

| Variable | Required | Description |
|---|---|---|
| `MCP_SERVER_URL` | yes | e.g. `http://mcp_server:8001` |
| `MCP_SECRET` | yes | Shared secret for X-MCP-Secret header |

---

## MCP Server (`mcp_server`)

| Variable | Required | Description |
|---|---|---|
| `MCP_SECRET` | yes | Must match the app's `MCP_SECRET` |
| `MCP_PORT` | no | `8001` |
| `LOG_LEVEL` | no | `INFO` |
| `NEO4J_URI` | yes | e.g. `bolt://neo4j:7687` |
| `NEO4J_USER` | yes | Neo4j username |
| `NEO4J_PASSWORD` | yes | Neo4j password |
| `VALKEY_URL` | yes | Same as app |
| `POSTGRES_DSN` | yes | PostgreSQL DSN for `asyncpg` (no `+asyncpg` prefix) |
| `APP_INTERNAL_URL` | yes | e.g. `http://app:8000` |
| `INTERNAL_API_KEY` | yes | Must match the app's `INTERNAL_API_KEY` |
