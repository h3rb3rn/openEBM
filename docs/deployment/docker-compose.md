# Docker Compose Deployment

## Services

| Service | Image | Default port |
|---|---|---|
| `app` | Custom (Python 3.12) | 4244 (host) |
| `mcp_server` | Custom (Python 3.12) | internal only |
| `postgres` | postgres:16 | internal only |
| `chromadb` | chromadb/chroma | internal only |
| `neo4j` | neo4j:5 | internal only |
| `valkey` | valkey/valkey | internal only |

## Build and start

```bash
# First run
docker compose build
docker compose up -d

# Rebuild only the app after code changes
docker compose build app
docker compose up -d app

# Rebuild MCP server
docker compose build mcp_server
docker compose up -d mcp_server
```

## Logs

```bash
docker compose logs -f app
docker compose logs -f mcp_server
```

## Health checks

```bash
# App health
curl http://localhost:4244/health

# System status (all services)
curl -H "Authorization: Bearer <token>" http://localhost:4244/api/system/status

# MCP server health
docker compose exec mcp_server curl http://localhost:8001/health
```

## Data volumes

| Volume | Service | Contents |
|---|---|---|
| `postgres_data` | postgres | All relational data |
| `chroma_data` | chromadb | EBM GOP embeddings |
| `neo4j_data` | neo4j | GOP exclusion graph |
| `valkey_data` | valkey | Session cache (not critical — ephemeral) |

## Demo credentials

The app seeds a demo tenant on first startup:

- **Email**: `admin@demo.local`
- **Password**: `demo1234`
- **Tenant**: `demo`

!!! warning "Production"
    Change all secrets before deploying to production. See [Environment Variables](environment.md) and [Production Checklist](production.md).
