# Changelog

All notable changes to EBM Analyzer are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] – 2026-07-07

### Added

#### Postgres row-level security as a tenant-isolation backstop
- Tenant isolation was previously enforced only at the application layer — every query hand-filters by `tenant_id`, correctly, but a single missed filter in a future endpoint would be a silent cross-tenant data leak with nothing to catch it. Added Postgres RLS policies on `patients`, `case_files`, `gop_suggestions` (via a subquery against `case_files`, since it has no direct `tenant_id` column), and `audit_logs` as an independent second layer: `USING (tenant_id = current_setting('app.tenant_id', true))`, which **fails closed** — if the session's tenant context isn't set, the policy matches nothing rather than everything.
- New `get_tenant_db` FastAPI dependency (`api/auth.py`) sets that session variable before returning the DB session; swapped in wherever `patients.py`/`cases.py`/`analysis.py`/the `admin_stats` endpoint touch these tables. Deliberately **not** applied to `users`/`api_keys`: both are looked up across tenants by design (login resolves a user by email before the tenant is known; API-key auth resolves an actor by key hash the same way) — RLS keyed on a not-yet-known tenant would break login entirely. Those tables keep the existing explicit application-layer filtering only.
- **Two real, non-obvious bugs found while verifying this, neither caught by "it returns 200"**:
  1. `SET LOCAL app.tenant_id = $1` fails outright — Postgres doesn't accept bind parameters for `SET`/`SET LOCAL` at the wire-protocol level. Fixed by using `set_config('app.tenant_id', $1, true)`, the parameterized equivalent (third argument `true` = transaction-local, matching `SET LOCAL` semantics).
  2. **The RLS policies were completely inert against the app's actual database connection.** `POSTGRES_USER` is the Postgres cluster bootstrap role, which the official `postgres` Docker image always makes a superuser — and Postgres superusers bypass row-level security unconditionally, `FORCE ROW LEVEL SECURITY` notwithstanding. `\d+ patients` happily printed the policy; it was simply never evaluated. Found by actually attempting a cross-tenant read with a second synthetic tenant, not by checking that policies existed. Fixed with a second migration creating a `NOSUPERUSER`/`NOBYPASSRLS` role (`ebm_app`) that the running app now connects as for all runtime queries (`APP_DATABASE_URL`) — migrations still run as the superuser (`DATABASE_URL`), since `CREATE POLICY`/`ALTER TABLE` need elevated privileges the restricted role intentionally doesn't have. The new role's password is set idempotently at container startup from `APP_DB_PASSWORD`, never embedded in the version-controlled migration.
- One SQLAlchemy-specific caveat documented and handled: `SET LOCAL` is transaction-scoped and resets on `commit()`. Two endpoints call `db.refresh()` after an explicit `db.commit()` (a new transaction), which would otherwise get silently blocked by the now-active RLS policy — `set_tenant_context()` is re-applied right after those two commits (`patients.py::create_patient`, `analysis.py`'s persistent-mode audit write).
- **Verified with the actual acid test, not just HTTP status codes**: created a second synthetic tenant with its own patient, connected as the real restricted `ebm_app` role, and confirmed tenant A's session sees only tenant A's 2 patients, tenant B's session sees only its 1 patient, and a session with no tenant context set sees zero rows from either. Also re-ran the entire page-route + API regression checklist established after the earlier `TemplateResponse` incident, plus the full pytest suite — all green throughout, including the write path (`POST /api/patients/`, which exercises the commit-then-refresh case).

#### Observability: Prometheus metrics + optional error tracking
- `GET /metrics` (via `prometheus-fastapi-instrumentator`): request counts, latencies, and status codes in Prometheus text format, so the Prometheus/Grafana stack already running on the host can scrape this app the same way it scrapes everything else. Unauthenticated at the ASGI level, matching typical Prometheus scrape-target conventions — restrict at the reverse proxy if exposed beyond the internal network.
- Optional Sentry-compatible error tracking (`error_tracking.py`, `SENTRY_DSN` env var). A no-op if unset — previously the only way to see an exception was grepping container logs. Deliberately framed around self-hosted Sentry/GlitchTip, not Sentry's SaaS, to stay consistent with the project's sovereignty stance; `send_default_pii=False` and no performance tracing, error capture only.

### Fixed

#### Critical: every page route was returning HTTP 500 since the starlette 1.x bump
- The starlette 0.41→1.3 dependency bump changed `Jinja2Templates.TemplateResponse()`'s signature from the old `TemplateResponse(name, {"request": request})` to `TemplateResponse(request, name, context={})` — the `request` object moved from inside the context dict to the first positional argument. `main.py` still used the old form for all 7 page routes (`/login`, `/dashboard`, `/analyse`, `/profil`, `/patienten`, `/fallakten`, `/admin`), which meant the dict silently landed in the `name` parameter and Jinja2's internal template cache tried to use it as a cache key (`TypeError: unhashable type: 'dict'`).
- **This was not caught by the "full smoke test" claimed in the earlier Dependabot-fix commit** — that verification exercised the JSON API (login, stats, instant-analysis) but never actually requested an HTML page route, so a completely broken UI shipped and stayed broken through the Alembic, backup, and rate-limiting commits that followed. Found while visually checking the password-reset UI with Playwright for *this* change — curl on `/login` returned a bare 500 with no JSON body, which is what a JSON-only smoke test would never surface.
- Fixed all 7 calls to the new `TemplateResponse(request, "x.html")` form. Re-verified this time by curling every single page route (both unauthenticated → 302 redirects, and authenticated → 200 with real HTML body sizes) plus a Playwright screenshot, not just the API layer.

### Added

#### Password reset flow
- No self-service way to recover a forgotten password previously existed — only an admin could reset one via direct API/DB access (exactly the situation that caused user confusion earlier in this project's history). New "Passwort vergessen?" link on the login page, `POST /auth/password-reset/request` (always returns the same generic response regardless of whether the email is registered, to avoid leaking which addresses have accounts) and `POST /auth/password-reset/confirm` (single-use token, 1-hour TTL, stored in Valkey).
- New `email_service.py`: plain stdlib `smtplib`, no new dependency. If `SMTP_HOST` isn't configured, sending is a logged no-op rather than an error — the reset request still returns its generic success response either way, so air-gapped deployments keep working exactly as before, just without self-service reset until an admin configures SMTP.
- Rate-limited via the same `rate_limit_service` used for login (3 reset requests per email per hour) to prevent email-bombing a target address.
- Verified end-to-end against the running app, not just unit tests: requested a reset, pulled the real token out of Valkey, confirmed it, logged in with the new password, confirmed the old password was rejected, and confirmed the token could not be reused a second time.

#### Brute-force protection on login and 2FA
- `/auth/token` and `/auth/2fa/verify` had no rate limiting at all — an attacker could try passwords or 6-digit TOTP codes as fast as the network allowed. New `rate_limit_service.py` (Valkey-backed, same fixed-window-counter pattern as the rest of the codebase) adds two independent limits: **per-account** (5 failed attempts / 15 min locks that account, reset on success) and **per-IP** (20 failed attempts / 15 min across any accounts, catching credential-stuffing sweeps — deliberately never reset on a single success, since a shared/NAT IP could have other users still failing legitimately). 2FA codes get their own per-account counter (8 attempts), since a 6-digit TOTP is brute-forceable in under a million tries without one.
- Returns `429` with a `Retry-After` header, which the existing login-page error handling already displays correctly (no frontend change needed — it already renders whatever `detail` string the backend sends).
- Verified end-to-end, not just unit-tested: 5 real wrong-password requests against the running app, then confirmed the 6th request — even with the *correct* password — was rejected with `429` and `Retry-After: 899`.

#### Automated PostgreSQL backups
- New `backup` service (`docker-compose.yml` + `docker/backup.sh`): daily `pg_dump` (custom format) with configurable interval/retention (`BACKUP_INTERVAL_SECONDS`, `BACKUP_RETENTION_DAYS`), written to a dedicated `backup_data` volume. There was previously no backup mechanism at all for a store holding patient records, case files, and the compliance audit log.
- Deliberately scoped to PostgreSQL only. Neo4j and ChromaDB hold data that's fully regenerable from the KBV catalog PDF via the admin import feature — backing them up separately would mean maintaining a second copy of derived data, and Neo4j Community edition's offline-only `neo4j-admin database dump` would require stopping the service on every backup cycle for no real durability gain.
- `docker/restore.sh` for the recovery path, with an explicit confirmation prompt since it drops and recreates existing objects. Verified end-to-end during development: ran a real backup, restored it into a scratch database, confirmed row counts matched the source exactly.

### Changed

#### Alembic migrations replace ad-hoc schema bootstrap
- Schema was previously managed by `database.py:create_tables()` — `Base.metadata.create_all(checkfirst=True)` plus a manually-maintained, ever-growing list of raw `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements for every field added after initial launch (visible right there in the function's own docstring). This has no version history, no rollback, and depends on someone remembering to hand-write the right `ALTER TABLE` line every time a model gains a column — `create_all()` only creates missing tables, it never alters existing ones.
- Replaced with real Alembic migrations (`alembic/`, `alembic.ini`). The baseline migration was generated by autogenerating against a genuinely empty scratch database (not the live one, to get the true from-scratch schema) and the existing dev database was `alembic stamp`-ed to that revision — its schema already matched, so nothing was executed against it, only the version bookkeeping was set.
- Migrations run once via `docker/app-entrypoint.sh` (`alembic upgrade head`) **before** uvicorn spawns its worker processes, not from inside the app's FastAPI lifespan — running `alembic upgrade` concurrently from multiple workers would race on the `alembic_version` table the same way the old raw-SQL approach raced on `pg_type`.
- Future schema changes: `alembic revision --autogenerate -m "..."` against a real model change, review the generated script, commit it alongside the model change. No more hand-written `ALTER TABLE IF NOT EXISTS` lines.

### Added

#### Automated test suite + CI gate
- First automated tests in the repo (`tests/`, `pytest.ini`, `.github/workflows/tests.yml`, running on every push/PR to `main`). Deliberately scoped to pure-logic unit tests with no live Postgres/Neo4j/ChromaDB/Valkey dependency — `_extract_token`'s cookie-vs-header priority (a regression test for the earlier reload-loop production incident), password hashing, JWT creation/scope handling, the KBV PDF parser's regex/classification heuristics, and the Valkey-backed import status state machine (via `fakeredis`, guarding the multi-worker fix above).
- **Found two real bugs in the KBV parser while writing these tests, before they ever ran in CI**: `_EXCLUSION_SENTENCE_RE`'s target-code group used `Gebührenordnungspositionen?` (requires a literal "e" before the optional "n" — matches only the non-word "Gebührenordnungspositione" or the plural "…positionen", never the grammatically correct singular "Gebührenordnungsposition") — so an exclusion sentence naming exactly one other GOP in the singular was silently never matched. `_AGE_MIN_RE` required "vollendeten" even when the preceding article ("dem"/"Beginn des") was absent, but German grammar uses "vollendetem" in that case ("ab vollendetem 70. Lebensjahr") — so some age-minimum restrictions were silently missed. Both fixed; likely explains some under-extraction in the exclusion and age-restriction counts from the original KBV import validation run (854 with exclusions / 136 with age restrictions out of 3173 GOPs) — those numbers were probably a floor, not the true count.
- This is a starting foundation, not full coverage — no integration tests against live services yet (see roadmap).

### Security

#### Dependency vulnerability remediation
- Resolved all vulnerabilities Dependabot flagged on the default branch (1 critical, 16 high, 15 moderate, 6 low). GitHub's own alert API wasn't reachable from this environment (`gh auth` needs an interactive login this headless box can't provide), so this was done offline via `pip-audit` against every `requirements.txt` in the repo (`src/app`, `src/mcp_server`, `src/ingestion`) plus `npm audit` for the Tailwind build tooling (clean — all flagged packages were Python).
- Bumped: `fastapi` 0.115→0.139, `starlette` 0.41→1.3 (a real major-version jump — verified the app's actual starlette/fastapi usage still works via a full smoke test, not just import checks), `mcp` 1.3→1.23 (verified the exact import surface used by `mcp_server/main.py` and `app/services/mcp_client.py`), `pydantic`/`pydantic-settings` 2.10/2.7→2.13/2.14 (required by the mcp bump), `python-jose` 3.3→3.4, `python-multipart` 0.0.18→0.0.31, `jinja2` 3.1.4→3.1.6, `python-dotenv` 1.0→1.2, `lxml` 5.3→6.1 (ingestion container), `sentence-transformers` 3.3→5.6 and `transformers` 4.57→5.3 (paired major-version bump, verified end-to-end against the real embedding model, not just installed).
- **Found and fixed a real breaking change while verifying the starlette 1.x bump**: `mcp_server/main.py` constructed its `Starlette` app with the `on_startup`/`on_shutdown` keyword arguments, which starlette 1.x removed in favor of the `lifespan` context-manager parameter. The container failed to start after the bump (`TypeError: Starlette.__init__() got an unexpected keyword argument 'on_startup'`) — caught by an actual container-start smoke test, not by chance. Rewrote as an `@asynccontextmanager lifespan()`.
- Pinned `torch` explicitly to the CPU-only wheel index (`--extra-index-url https://download.pytorch.org/whl/cpu`, `torch==2.9.1+cpu`) in both `app` and `ingestion` requirements. Without this, plain `pip install` resolves the default PyPI `torch` build, which pulls the full CUDA toolkit (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, etc. — several GB) even though nothing in this stack runs on GPU; the actual LLM is external via Ollama/OpenAI-compatible API. This was also the direct cause of a build that appeared to hang for 20+ minutes.
- **One CVE has no upstream fix and was accepted as low real-world risk, not silently ignored**: `ecdsa` (PYSEC-2026-1325, pulled in transitively by `python-jose[cryptography]`) has no patched release. Verified the app only ever signs/verifies JWTs with `HS256` (`src/app/api/auth.py: ALGORITHM = "HS256"`) — a symmetric-secret algorithm that never invokes the vulnerable ECDSA code path. Documented here rather than left unexplained on a dependency scan.
- Verified end-to-end after all bumps: login (bcrypt/passlib unaffected), `/admin/stats`, `/admin/metrics/quality`, and a full instant-mode analysis exercising the real RAG/sentence-transformers embedding path, a live LLM call, and a full MCP validation round-trip (exclusions, time budget, demographics) — all returned correct results, not just HTTP 200s.

### Added

#### Suggestion quality metrics (admin → Metriken)
- New `GET /api/admin/metrics/quality` endpoint and a "Vorschlagsqualität" section on the admin Metriken tab: overall acceptance rate, acceptance rate by quarter, top-15 most-suggested GOPs ranked by volume with per-code acceptance rate, and — the compliance-relevant number — how often a human accepted a suggestion the deterministic MCP validator had flagged as conflicting.
- Pure read-only aggregation over data the app already tracked from day one (`GOPSuggestion.status`/`confidence`/`mcp_validated`, the audit log) — no new tables, no change to the LLM or retrieval pipeline.
- Deliberately does **not** feed this data back into the model automatically. This is a billing-compliance tool: an EBM code's meaning can change every quarter (see the KBV PDF import above), so anything a model "learned" about a code's semantics goes stale within months, and a self-adjusting suggester is harder to justify after the fact than a static RAG+rules pipeline that a human reviews. The metrics are for a human to read and act on — e.g. flag a GOP with a suspiciously low acceptance rate for prompt/RAG review — not an automatic retraining signal.

#### Mobile-responsive layout + PWA installability
- The sidebar never collapsed on small screens — on a phone it permanently ate ~256px of a ~380px viewport, leaving almost no room for content. It's now an off-canvas drawer below the `md` breakpoint (hamburger toggle in a new mobile top bar, backdrop, closes on nav-link tap) and reverts to the original always-visible column on tablet/desktop, unchanged.
- App is now installable as a standalone PWA: `manifest.webmanifest` (icons generated to match the existing in-app brand mark — sky-500 square, white "E" — at 192/512/maskable-512, plus a 180px Apple touch icon and favicon), and a minimal service worker (`static/sw.js`). The service worker deliberately caches only static assets (CSS/JS/i18n/icons) and never touches `/api/*` calls or page navigations — this is a clinical billing tool, so stale cached analysis results, case data, or auth state would be actively wrong, not just a stale-content inconvenience.
- Fixed several data tables (Fallakten, Patienten, Dashboard, Admin-Benutzer, API-Keys) that had no horizontal-scroll container — on mobile, columns past the viewport edge were simply unreachable, not just visually clipped. Each table body is now wrapped in `overflow-x-auto`.
- Fixed the dashboard header (title + two action buttons) and the instant-analysis anonymous-patient form (3-column grid) overflowing/crowding on narrow viewports; both now stack vertically below the `sm` breakpoint.

#### Real KBV catalog PDF import (preview → commit)
- **Configurable, admin-editable catalog source**: the KBV PDF URL shown in the admin UI was previously hard-coded plain text in `admin.html` with no connection to the actual import — quarterly updates required a code change. New `system_settings` key-value table (`src/app/models/base.py`) stores the URL, editable via `GET/PUT /api/admin/import/kbv-source`.
- **`src/app/services/kbv_import.py`**: downloads the KBV PDF and extracts GOP code, description, point value, exclusions, and age restrictions using `pdfplumber`. Key insight validated against the real ~2225-page KBV document: codes that start a genuine new entry are set in the regular (non-italic) font at the left content margin, while codes referenced inside "nicht neben ... berechnungsfähig" exclusion sentences are italicized — this font-based signal is far more reliable than line-position heuristics alone, which produced many false positives from codes referenced mid-paragraph in wrapped exclusion lists.
- **Preview-before-commit workflow**: because this is a best-effort heuristic extraction against an unstructured legal document — not an official machine-readable feed — nothing is written to Neo4j/ChromaDB until an admin reviews a preview (`POST /api/admin/import/kbv-fetch` → stats + sample entries + per-entry `warnings`) and explicitly commits (`POST /api/admin/import/kbv-commit/{run_id}`). A mis-parse here would be billing-relevant, so silent auto-commit was ruled out.
- Verified end-to-end against the real, current KBV EBM PDF (2225 pages, 15 MB): 3173 GOP entries extracted, 854 with exclusion relationships, 136 with age restrictions, committed successfully to both Neo4j (3173 nodes) and ChromaDB (3173 embeddings) in ~5 minutes fetch/parse + ~70s commit.
- **Known limitations**, surfaced via per-entry and document-level `warnings` rather than hidden: ~25% of entries have no directly extractable point value (multi-tier pricing tables like `Versichertenpauschale` aren't captured by a single-value regex); exclusions referencing an entire `Abschnitt` (section) rather than explicit GOP codes are flagged `section_level_exclusion_unresolved` rather than guessed at; official KBV `Kalkulationszeiten` (calculation times, published separately from the main catalog text) are not covered — `time_value_minutes` is only populated where the text explicitly states "je vollendete N Minuten".
- **Fixed a latent multi-worker bug** surfaced while building this: `import_service.py`'s progress status was kept in a plain in-process dict, but the app runs with `--workers 2` (separate OS processes, separate memory) — status polling could randomly show stale state depending on which worker handled a given request. Moved status and pending-preview storage to Valkey (same pattern as `session_service.py`), which also affects (and fixes) the pre-existing local-file import status polling.
- Local-file import (air-gapped path, `EBM_CATALOG_PATH`) is retained unchanged for offline deployments; the KBV PDF path is additive, not a replacement.

- **More screenshots** in `docs/screenshots.md`: Admin → System & Monitoring, Datenquellen, Import, and a refreshed Interoperabilität capture, plus a full case-file detail view (case `819cfd50-514e-4859-87b4-13a223880062`, 2026-07-06/2026Q3) with the physician report expanded and its four still-open GOP suggestions (accept/reject pending). All screenshots were retaken at a tall-enough viewport so the app's inner scrollable `<main>` doesn't clip content that Playwright's `full_page` option can't reach.

- **Verified LLM backend note**: documented that the full RAG → LLM → MCP pipeline was successfully tested end-to-end against a locally hosted `qwen3.6:35b` model (no cloud LLM API involved). Added to `README.md`, `docs/index.md`, and the public project site (openebm.org), backed by a real analysis run captured in the screenshots below.
- **Screenshots**: captured real UI screenshots (login, dashboard, a genuine analysis result with MCP-validated GOP highlighting, case files, admin interoperability status, profile/2FA) via Playwright against a live instance. Added `docs/screenshots.md` (linked from the MkDocs nav) and embedded two of them in `README.md`. The admin screenshot deliberately shows the Interop tab rather than the user-management tab to avoid publishing other accounts' names/emails.

### Fixed

- **Critical: browser stuck in a `/dashboard` ⇄ `/login` reload loop.** The cookie-session migration updated `get_current_user` to check the `ebm_session` cookie, but `get_current_actor()` (`src/app/api/auth.py`) — used by every `require_scope(...)`-guarded endpoint, i.e. all of `patients.py`, `cases.py`, the instant-analysis endpoint, and the GDT/FHIR interop endpoints — still only checked `X-API-Key` and the `Authorization` header, never the cookie. Since the frontend now sends only the cookie (no more `Authorization` header from `apiCall()`), every such request 401'd; the client redirected to `/login`, which saw the still-valid cookie and immediately redirected back to `/dashboard`, which called the same endpoint again — an infinite loop. Fixed by routing `get_current_actor()` through the same `_extract_token()` helper `get_current_user()` uses (cookie first, then `Authorization: Bearer` for non-browser clients). Verified against `/api/cases/`, `/api/patients/`, `/api/interop/status` with a cookie, and confirmed `Authorization: Bearer` still works unchanged for external API clients.

### Added

#### Self-service profile: password change & 2FA
- **New `/profil` page** (`profile.html`, route in `main.py`) — account info, password change form, and full TOTP two-factor-authentication management. Server-side auth-guarded like all other protected pages. Linked from the sidebar via the user avatar/name (`base.html`).
- **`PATCH /api/auth/password`** — change own password (requires current password, min. 8 characters).
- **TOTP 2FA** (`src/app/api/profile.py`, new `pyotp`/`qrcode` dependencies):
  - `POST /api/auth/2fa/setup` — generates a secret and a QR code (PNG, base64) for scanning with any TOTP authenticator app.
  - `POST /api/auth/2fa/enable` — confirms setup with a live code, activates 2FA, returns 8 one-time backup codes (shown once, stored only as SHA-256 hashes).
  - `POST /api/auth/2fa/disable` — requires current password **and** a valid TOTP/backup code.
  - `GET /api/auth/2fa/status` — whether 2FA is enabled for the current user.
  - Backup codes are single-use: consumed and removed from the stored list on successful verification.
- **Two-phase login** (`src/app/api/auth.py`): if a user has 2FA enabled, `POST /api/auth/token` no longer issues a session — it returns a short-lived (5 min) `pending_token` scoped `2fa_pending`. The session cookie is only issued after `POST /api/auth/2fa/verify` confirms a valid code. The pending token is explicitly rejected by `_user_from_jwt` if presented as a normal Bearer/session token, so it cannot be used to bypass the second factor.
- **`User` model** gained `totp_secret`, `totp_enabled`, `totp_backup_codes` columns. Since the `users` table already existed in deployed databases, `create_tables()` (`database.py`) now runs idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements in addition to `create_all()`, which only creates missing tables and never alters existing ones.
- `login.html` extended with an inline second step (TOTP/backup code input) shown when the server signals `requires_2fa`.
- 30 new i18n keys added to `de.json`/`en.json` (profile + 2FA + login second step), verified zero gaps between catalogs.

### Fixed

- **Removed the hardcoded demo-credentials hint and prefilled login values** (`admin@demo.local` / `demo1234`) from `login.html` — the input fields and the footer hint both displayed them by default, which is inappropriate once real accounts exist.

#### Repository-Grundlagen
- **`README.md`** (Deutsch) — Projektbeschreibung, Schnellstart, Betriebsmodi, Architekturübersicht, Verzeichnisstruktur, Sicherheitshinweise, Entwicklungs-Workflow.
- **`.gitignore`** — schließt `.env` (Secrets), `node_modules/`, Python-Cache-Verzeichnisse, das kompilierte `tailwind.css` (Build-Artefakt), das MkDocs-`site/`-Ausgabeverzeichnis, Editor-/OS-Dateien und lokale Docker-Compose-Overrides aus der Versionierung aus.
- **`.env.example`** ergänzt um zuvor undokumentierte Variablen: `COOKIE_SECURE`, `INTERNAL_API_KEY`, `APP_INTERNAL_URL`.
- **`docs/requirements.txt`** — gepinnte Versionen für den MkDocs-Build (`mkdocs`, `mkdocs-material`, `pymdown-extensions`).
- **`.github/workflows/docs.yml`** — GitHub-Actions-Workflow, der die MkDocs-Dokumentation bei jedem Push auf `main` (bei Änderungen unter `docs/` oder `mkdocs.yml`) automatisch baut und per `mkdocs gh-deploy` auf den `gh-pages`-Branch veröffentlicht. Zusätzlich manuell auslösbar (`workflow_dispatch`).
- `mkdocs.yml`: `repo_url`, `repo_name`, `site_url` und `edit_uri` auf das tatsächliche Repository `github.com/h3rb3rn/openEBM` gesetzt (vorher leer/Platzhalter).

#### Air-gap compliance: local Tailwind CSS build
- **Removed the Tailwind CDN script** (`https://cdn.tailwindcss.com`) from `base.html` and `login.html`. The Play CDN fetched and JIT-compiled CSS from an external host at runtime, breaking air-gap operation.
- **Local build pipeline**: added `package.json`, `tailwind.config.js`, and `src/app/static/css/input.css`. Tailwind CLI compiles a static `tailwind.css` from the actual class usage in templates and JS.
- **Docker multi-stage build** (`docker/app.Dockerfile`): new `frontend-build` stage (`node:20-slim`) runs `npm install` + `npm run build:css`; the compiled `tailwind.css` is copied into the final Python image. Node is not present in the runtime image — only used at build time.
- Verified: the running app now serves `/static/css/tailwind.css` locally; no request leaves the container for styling.

#### Security: server-side session guard (httpOnly cookie)
- **Replaced localStorage JWT with an httpOnly session cookie** (`ebm_session`, `SameSite=Lax`). The token is no longer readable or settable from JavaScript, closing a class of XSS-driven token theft and removing manual `Authorization` header wiring from every page (`base.html`, `login.html`, `admin.html`).
- **Server-side page guards** (`src/app/main.py`): `/dashboard`, `/analyse`, `/patienten`, `/fallakten`, `/admin` now check the session cookie *before* rendering the template and issue an HTTP 302 to `/login` for unauthenticated requests. Previously these routes always returned 200 with the full page shell; the client-side JS (`loadUserInfo()`) only redirected *after* the page had already rendered, producing a brief visible "flash" of the protected UI shell that could be paused/inspected via browser dev tools. `/admin` additionally redirects non-admin users to `/dashboard`. `/login` redirects already-authenticated users to `/dashboard`.
- **`POST /api/auth/logout`** added — clears the session cookie server-side (previously logout only cleared `localStorage`, leaving no way to invalidate a stolen cookie-equivalent client-side).
- `get_current_user` (`src/app/api/auth.py`) now reads the token from the cookie first, falling back to `Authorization: Bearer` for non-browser/API clients; `get_current_user_optional` added for the non-raising page-guard use case.
- New setting `COOKIE_SECURE` (`src/app/config.py`, default `false`): set to `true` once TLS is terminated in front of the app, otherwise browsers silently drop the cookie over plain HTTP.
- Note: this was a UX/defence-in-depth gap, not a data leak — all patient/case data is fetched client-side through JWT-protected API calls that already rejected unauthenticated requests with 401. The fix removes the unauthenticated page shell response entirely rather than relying on client-side redirect timing.

#### LLM / Analysis
- **OpenAI-compatible API support** (`src/app/services/llm_service.py`): Added `_primary_url_speaks_ollama()` — a one-time cached probe that tests whether the configured `OLLAMA_BASE_URL` responds to `/api/tags`. If it does not (e.g. `moe-sovereign.org`), all requests are routed to `_call_openai_compatible()` instead of `_call_ollama()`.
- **Mandatory streaming** for `_call_openai_compatible()`: `moe-sovereign.org` hangs indefinitely on non-streaming chat completions. Changed to `stream=True` with SSE chunk accumulation.
- **Model picker** on the analysis page: fetches `/api/analysis/models`, pre-selects the model from `.env`, and exposes toggles for Knowledge Base (RAG), Cache, and Reasoning (Chain-of-Thought).
- **Analysis options in persistent/case-file mode**: The same model picker and option toggles are now available when running analysis from a patient's case file detail view.

#### Physician report display
- Case file detail view now renders the full `report_text` with GOP annotation highlights (colored text anchors derived from `start_char`/`end_char` stored in `GOPSuggestion`).

#### Interoperability layer (`src/integration/`, `src/app/api/interop.py`)
- **GDT 2.1 bridge** (`src/integration/gdt_handler.py`): Parser for ISO 8859-1 encoded GDT files. Supports `Satzarten` 6310, 6311, 8200, 8220 and field identifiers 6205, 6220, 6228 (clinical text), 3101–3119 (patient demographics). Exports validated GOPs back to GDT format.
- **HL7 FHIR R4 endpoint** (`POST /api/interop/fhir/v4/$analyze-ebm`): Custom FHIR operation accepting `DocumentReference`, `Composition`, and `Parameters` resources. Returns a FHIR `Parameters` resource with validated EBM codes.
- **Internal MCP channel** (`POST /api/interop/internal/analyze`): Container-internal endpoint authenticated via `X-Internal-Key` shared secret. Used by the MCP server to call the full analysis pipeline.
- **Interop status endpoint** (`GET /api/interop/status`): Reports the state of all interop interfaces for the admin dashboard.
- **MCP external tool** `analyze_clinical_text_for_ebm` added to `src/mcp_server/main.py`: Exposes the full EBM analysis pipeline as an MCP tool callable by external AI agents.
- GDT export cache backed by Valkey (TTL 3600 s); download via `GET /api/interop/gdt/export/{session_id}`.
- Added `src/integration/` to `docker/app.Dockerfile` COPY directives.

#### API key management (admin area)
- **`ApiKey` model** (`src/app/models/base.py`): New table `api_keys` — stores key prefix, SHA-256 hash, scopes (JSON array), expiry, creation timestamp, last-used timestamp, and revocation state.
- **Admin CRUD endpoints** (`src/app/api/admin.py`): `GET /admin/api-keys`, `POST /admin/api-keys`, `DELETE /admin/api-keys/{id}` (soft-revoke).
- **API key authentication** (`src/app/api/auth.py`): `get_current_actor()` accepts either `Authorization: Bearer <JWT>` (web UI users) or `X-API-Key: ebm_live_<token>` (external programs). `require_scope(scope)` dependency factory enforces scope restrictions for API key callers.
- Available scopes: `analysis`, `interop`, `patients`, `cases`.
- Plaintext key is returned exactly once at creation time; only SHA-256 hash is persisted.

#### Internationalisation (i18n)
- **`src/app/static/js/i18n.js`**: Lightweight client-side i18n engine. Loads flat JSON catalogs from `/static/i18n/`, supports parameterised strings via `{{key}}` interpolation, falls back to English if a German key is missing.
- **Translation catalogs** `src/app/static/i18n/de.json` and `src/app/static/i18n/en.json`: 299 keys each covering all UI text. Verified zero gaps between the two catalogs.
- **Language picker** added to the sidebar in `base.html`; persists locale preference in `localStorage`.
- All 7 HTML templates fully annotated with `data-i18n` attributes and `t()` calls; inline text replaced.

#### Configuration
- `src/app/config.py`: Added `internal_api_key` (shared secret for internal channel) and `app_internal_url`.
- `src/mcp_server/config.py`: Added `app_internal_url` and `internal_api_key`.

### Fixed

- **502/503 on analysis requests**: `_call_ollama()` was posting to `/api/generate` which `moe-sovereign.org` does not implement. Fixed by format detection + fallback to `_call_openai_compatible()`.
- **Streaming hang on `moe-sovereign.org`**: Non-streaming chat completions never returned. Fixed by forcing `stream=True` in `_call_openai_compatible()`.
- **`ModuleNotFoundError: No module named 'src.integration'`**: `docker/app.Dockerfile` only copied `src/app`. Fixed by adding `COPY src/integration ./src/integration`.
- **`delete_user()` unhandled `IntegrityError`** (`src/app/api/admin.py`): Users with audit-log entries threw an FK violation on hard delete. Fixed with `try/except IntegrityError` returning HTTP 409 and directing the caller to deactivation instead.
- **DB startup race condition** (`src/app/main.py`): Two uvicorn workers could race on PostgreSQL ENUM type creation during startup. Non-fatal (schema exists after either wins); added an explanatory comment.

### Changed

#### Code language standardisation (refactoring)
All Python source files have been migrated to English-only developer-facing text (docstrings, inline comments, log messages). The following files were updated:

- `src/app/services/llm_service.py`
- `src/app/services/rag_service.py`
- `src/app/services/session_service.py`
- `src/app/services/mcp_client.py`
- `src/app/services/import_service.py`
- `src/app/api/auth.py`
- `src/app/api/admin.py`
- `src/app/api/analysis.py`
- `src/app/api/patients.py`
- `src/app/api/cases.py`
- `src/app/api/interop.py`
- `src/app/models/base.py`
- `src/app/config.py`
- `src/app/database.py`
- `src/app/main.py`
- `src/mcp_server/main.py`
- `src/mcp_server/neo4j_client.py`
- `src/mcp_server/tools/exclusions.py`
- `src/mcp_server/tools/time_budget.py`
- `src/mcp_server/tools/demographics.py`

Note: domain enum *values* (e.g. `"offen"`, `"akzeptiert"`) remain German — they are persisted in the database and are part of the API contract. Only developer-facing text was changed.

#### HTML templates
All 7 templates (`base.html`, `login.html`, `dashboard.html`, `patients.html`, `cases.html`, `analysis.html`, `admin.html`) migrated from hardcoded German strings to `data-i18n` / `t()` i18n calls. JavaScript comments translated to English.

---

## Prior history

Changes prior to this session are not documented here. See `git log` for the full history.
