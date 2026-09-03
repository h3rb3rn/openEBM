# Admin API

Base path: `/api/admin`

All endpoints require role `admin`.

---

## User management

### GET /users

List all active and inactive users for the current tenant.

```http
GET /api/admin/users
Authorization: Bearer <token>
```

Response: array of `UserResponse`:
```json
[
  {
    "id": "uuid",
    "email": "arzt@praxis.de",
    "full_name": "Dr. Müller",
    "role": "arzt",
    "is_active": true,
    "created_at": "2024-01-15 09:00",
    "last_login": "2024-03-10 14:22"
  }
]
```

### POST /users

Create a user.

```json
{
  "email": "mfa@praxis.de",
  "full_name": "Anna Schmidt",
  "password": "secure123",
  "role": "mfa"
}
```

Returns 409 if email already exists.

### PATCH /users/{user_id}

Update a user. All fields optional.

```json
{
  "full_name": "Anna Schmidt-Meier",
  "role": "arzt",
  "is_active": true,
  "new_password": "newpassword"
}
```

Guards: admins cannot demote themselves or deactivate themselves.

### DELETE /users/{user_id}

Hard delete. Returns 409 if the user has audit-log entries (FK constraint). Deactivate instead.

---

## Statistics

### GET /stats

Aggregate statistics for the tenant.

```json
{
  "active_users": 5,
  "patients": 142,
  "case_files": 89,
  "accepted_gops": 312,
  "role_distribution": {"admin": 1, "arzt": 3, "mfa": 1}
}
```

### GET /metrics/quality

Suggestion-quality analytics, aggregated read-only from data already recorded on every `GOPSuggestion` (status, confidence, MCP validation flag) — no separate storage, no effect on the LLM or retrieval pipeline. Intended for a human to review (e.g. spot a GOP with a suspiciously low acceptance rate and check the prompt/RAG corpus for it), not as an automatic feedback signal into the model — GOP semantics change every quarter with the catalog, so nothing "learned" here should be allowed to silently persist across a catalog update.

```json
{
  "overall": {"total": 120, "accepted": 98, "rejected": 14, "pending": 8, "acceptance_rate": 0.875},
  "by_quarter": [
    {"quartal": "2026Q2", "accepted": 40, "rejected": 6, "pending": 0, "acceptance_rate": 0.87},
    {"quartal": "2026Q3", "accepted": 58, "rejected": 8, "pending": 8, "acceptance_rate": 0.879}
  ],
  "top_gops": [
    {"gop_code": "03230", "total": 14, "accepted": 12, "rejected": 1, "pending": 1, "acceptance_rate": 0.923}
  ],
  "mcp_conflicts": {"total_flagged": 6, "accepted_despite_flag": 2, "rejected_after_flag": 3, "pending": 1},
  "confidence_calibration": {"avg_confidence_accepted": 0.91, "avg_confidence_rejected": 0.62}
}
```

`mcp_conflicts.accepted_despite_flag` is the compliance-relevant figure: how often a human accepted a suggestion the deterministic MCP validator had flagged (e.g. an exclusion conflict). `acceptance_rate` fields are `null` where a code/quarter has no reviewed (accepted+rejected) suggestions yet.

---

## Catalog import

Two independent paths feed the same Neo4j/ChromaDB loader:

| Path | Endpoint(s) | Writes to DB |
|---|---|---|
| Local file (air-gapped) | `POST /import/trigger` | Immediately |
| KBV PDF (online) | `POST /import/kbv-fetch` → review → `POST /import/kbv-commit/{run_id}` | Only after explicit commit |

### GET /import/status

Returns catalog file metadata, database row counts, and the last import run status. `last_run.state` is one of `idle`, `running` (local-file import), `fetching` (KBV PDF download/parse), `preview_ready` (parsed, awaiting review — see `last_run.preview`), `committing`, `done`, `error`.

### POST /import/trigger

Starts the local-file EBM catalog ingestion pipeline (`EBM_CATALOG_PATH`) as a background task. Returns 409 if an import is already running.

### GET /import/kbv-source

Returns the currently configured KBV catalog PDF URL (admin-editable, stored in the `system_settings` table — not hard-coded) and the built-in default.

### PUT /import/kbv-source

Body: `{"url": "https://..."}`. Must be `https://`. Update this each quarter when the KBV publishes a new catalog PDF at a new URL.

### POST /import/kbv-fetch

Downloads the PDF from the configured source URL and parses it (`src/app/services/kbv_import.py`) — GOP code, description, point value, exclusions, age restrictions. **Does not write to Neo4j/ChromaDB.** The parser is a best-effort heuristic extraction against an unstructured legal document (not an official machine-readable feed), so results land in `last_run.preview` for review: `stats` (total GOPs, how many are missing a point value, how many have exclusions/age restrictions, how many are flagged `needs_review`), a `sample` of parsed entries, and `document_warnings`. Returns 409 if an import is already running.

### POST /import/kbv-commit/{run_id}

Writes a previously fetched-and-reviewed preview into Neo4j/ChromaDB, using the same loader as the local-file path. `run_id` comes from `last_run.preview.run_id`. Returns 409 if the preview has expired (1 hour TTL), was already committed, or another import is running.

---

## API keys

### GET /api-keys

List all API keys for the tenant (plaintext key is never returned after creation).

```json
[
  {
    "id": "uuid",
    "name": "CRM Integration",
    "key_prefix": "ebm_live_xK3mP...",
    "scopes": ["analysis", "patients"],
    "is_active": true,
    "created_at": "2024-03-01 10:00",
    "last_used_at": "2024-03-15 08:45",
    "expires_at": null
  }
]
```

### POST /api-keys

Create a new API key. The plaintext key is returned **once** in the response.

```json
{
  "name": "CRM Integration",
  "scopes": ["analysis", "patients"],
  "expires_in_days": 365
}
```

Response includes `"api_key": "ebm_live_..."` — store it securely; it cannot be retrieved again.

### DELETE /api-keys/{key_id}

Soft-revoke a key (`is_active = false`, `revoked_at` set). Returns 204 on success.
