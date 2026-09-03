# Architecture Overview

## Design principles

**Sovereign**: all data and model inference remain within the operator's infrastructure. No external API calls are required for core functionality.

**Split-brain**: probabilistic reasoning (RAG + LLM) and deterministic rule enforcement (MCP) are strictly separated. The LLM suggests; the MCP server decides. No LLM output can bypass billing rules.

**Multi-tenant**: every database row carries a `tenant_id`. Tenant isolation is enforced at the application layer on every query — there is no row-level security in PostgreSQL.

**Air-gap ready**: the system can operate without internet access if a local Ollama instance and a local ChromaDB/Neo4j cluster are available.

---

## Component map

```
Browser
  │  HTTPS
  ▼
FastAPI app (uvicorn, 2 workers)
  ├── HTML templates (Jinja2) + static assets
  ├── REST API  /api/*
  │     ├── /auth          — JWT login, /me
  │     ├── /analysis      — instant + persistent analysis, GOP accept/reject
  │     ├── /patients      — patient CRUD
  │     ├── /cases         — case file CRUD + audit log
  │     ├── /admin         — user management, catalog import, API keys
  │     └── /interop       — GDT, FHIR R4, internal MCP channel
  │
  ├── PostgreSQL  (tenants, users, patients, case_files, gop_suggestions,
  │                audit_logs, api_keys)
  ├── ChromaDB    (EBM GOP embeddings for semantic retrieval)
  ├── Neo4j       (GOP exclusion graph, demographic restriction nodes)
  └── Valkey      (instant analysis sessions, GDT export cache, time-budget
                   accumulators, exclusion rule cache)

MCP Validation Server (separate Starlette/uvicorn process)
  ├── ebm_validator_exclusions    — mutual exclusion check (Neo4j)
  ├── ebm_validator_time_budget   — time profile check (Neo4j + Valkey)
  ├── ebm_validator_demographics  — demographic check (Neo4j + PostgreSQL)
  └── analyze_clinical_text_for_ebm  — full pipeline proxy (calls FastAPI)

External clients
  ├── PVS software  →  GDT 2.1 file upload/download
  ├── EMR system    →  HL7 FHIR R4 $analyze-ebm operation
  └── AI agents     →  MCP tool via HTTP/SSE
```

---

## Analysis pipeline (split-brain)

```
report_text
    │
    ▼ Phase 1: RAG (optional, toggleable)
    ChromaDB semantic search
    → GOP candidates with description + embedding distance
    │
    ▼ Phase 2: LLM
    Prompt = system instructions + RAG context + report_text
    LLM extracts: gop_code, source_text, start_char, end_char,
                  confidence, reasoning
    │
    ▼ Phase 3: MCP validation (deterministic)
    ┌─ ebm_validator_exclusions   → allowed / banned matrix
    ├─ ebm_validator_time_budget  → budget check
    └─ ebm_validator_demographics → patient eligibility
    │
    ▼ Phase 4: Merge
    Intersection of LLM suggestions ∩ MCP allowed set
    → final_gops (persisted or cached depending on mode)
```

### Instant mode vs. persistent mode

| | Instant | Persistent |
|---|---|---|
| Storage | Valkey only (TTL 24 h) | PostgreSQL (`case_files`, `gop_suggestions`) |
| Audit log | No | Yes (`audit_logs`) |
| Patient link | Anonymous or inline override | Linked to `Patient` record |
| Use case | Ad-hoc text analysis | Case management workflow |

---

## LLM provider configuration

The LLM service supports two wire formats detected at startup:

1. **Native Ollama** (`/api/tags` returns models list): uses `/api/generate` with streaming.
2. **OpenAI-compatible** (`/api/tags` returns empty or 404): routes to `/v1/chat/completions` with `stream=True`.

`moe-sovereign.org` is an OpenAI-compatible API. It only supports `/v1/models` and `/v1/chat/completions`. The format probe is cached after the first call — no repeated probes during runtime.

!!! warning "Streaming is mandatory for moe-sovereign.org"
    Non-streaming chat completions hang indefinitely. The `stream=True` flag is always set in `_call_openai_compatible()`.

---

## Authentication

Two authentication mechanisms coexist:

| Mechanism | Transport | Used by |
|---|---|---|
| JWT session cookie | httpOnly `ebm_session` cookie, `SameSite=Lax` | Web UI (browser) |
| JWT Bearer token | `Authorization: Bearer <token>` | Non-browser clients that cannot store cookies |
| API key | `X-API-Key: ebm_live_<token>` | External programs (CRM, PVS, agents) |
| Internal key | `X-Internal-Key: <secret>` | MCP server → FastAPI (container-internal) |

Page routes (`/dashboard`, `/analyse`, `/patienten`, `/fallakten`, `/admin`) are gated server-side: the session cookie is validated before the template is rendered, so an unauthenticated request never receives the protected page's HTML (see [Security Model](security.md)).

See [Authentication API reference](../api/authentication.md) for details.
