# Production Checklist

## Secrets

- [ ] `SECRET_KEY` — generate with `python -c "import secrets; print(secrets.token_hex(32))"`
- [ ] `INTERNAL_API_KEY` — minimum 32 characters, randomly generated
- [ ] `MCP_SECRET` — minimum 32 characters, randomly generated
- [ ] `CHROMA_TOKEN` — strong random token
- [ ] `POSTGRES_PASSWORD` — strong random password
- [ ] `NEO4J_PASSWORD` — strong random password

## Application

- [ ] `ENVIRONMENT=production` — disables `/api/docs` (Swagger UI)
- [ ] Reverse proxy in front of the app (nginx or Traefik); terminate TLS there
- [ ] `COOKIE_SECURE=true` once TLS is terminated — otherwise leave `false` or the session cookie will be silently dropped by browsers
- [ ] MCP server port not exposed on the host
- [ ] `ACCESS_TOKEN_EXPIRE_MINUTES` tuned to session requirements

## Database

- [ ] PostgreSQL running with WAL enabled
- [x] Regular `pg_dump` backups with retention policy — the `backup` service in `docker-compose.yml` runs `docker/backup.sh` daily by default (`BACKUP_INTERVAL_SECONDS`, default 86400) and prunes dumps older than `BACKUP_RETENTION_DAYS` (default 14). Dumps land in the `backup_data` volume. Restore with `docker compose exec backup /restore.sh /backups/<file>.dump` — verified end-to-end against a scratch database during development (see CHANGELOG).
  - Only PostgreSQL is backed up. Neo4j (GOP exclusion graph) and ChromaDB (embeddings) are fully regenerable from the KBV catalog PDF via the admin import feature (`/admin` → Import), so a separate backup of derived data was judged not worth the operational complexity — Neo4j Community edition doesn't support online backup, only an offline `neo4j-admin database dump`, which would mean stopping the service on every backup cycle for data that can be rebuilt from source anyway.
- [x] `audit_logs` table has no `UPDATE`/`DELETE` grants for the app user — enforced at the database role level (`ebm_app` has only `SELECT`/`INSERT` on `audit_logs`, see `alembic/versions/..._restrict_app_role_audit_log_to_append_.py`), not just by the application code never issuing those statements.
- [x] Cross-tenant data leaks have a database-level backstop beyond application-layer filtering — Postgres row-level security on `patients`/`case_files`/`gop_suggestions`/`audit_logs`, enforced via a dedicated `NOSUPERUSER`/`NOBYPASSRLS` role (`ebm_app`) the app connects as at runtime. Verified with a real second-tenant isolation test, not just that policies exist — see CHANGELOG.

## Monitoring

- [ ] `/health` endpoint polled by the health check system
- [ ] `/api/system/status` monitored for degraded state (returns HTTP 207)
- [ ] Log aggregation configured (app logs to stdout in structured format)
- [x] `GET /metrics` exposes Prometheus-format request counts/latencies/status codes (unauthenticated at the ASGI level — restrict via reverse proxy/network if exposed beyond the internal network)
- [x] Error tracking available via `SENTRY_DSN` — a no-op if unset, so this is opt-in. Point it at a self-hosted Sentry/GlitchTip instance to stay sovereign, not Sentry SaaS.

## Network

- [ ] All internal services (postgres, valkey, chromadb, neo4j, mcp_server) on a private Docker network with no host port mappings
- [ ] Firewall allows inbound only on the reverse proxy port (443)
- [ ] TLS certificate valid and auto-renewed

## Data protection (GDPR)

- [ ] Data processing agreement (DPA) in place with all infrastructure providers
- [ ] `report_text` and patient data stored in an EU data centre
- [ ] Valkey persistence disabled or encrypted if `report_text` flows through instant sessions
- [ ] Audit log retention policy defined and documented
