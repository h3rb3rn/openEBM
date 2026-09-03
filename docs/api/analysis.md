# Analysis API

Base path: `/api/analysis`

---

## GET /models

Returns the list of available LLM models and the configured default.

```http
GET /api/analysis/models
Authorization: Bearer <token>
```

**Response**
```json
{
  "models": [
    {"id": "qwen3:35b", "name": "Qwen3 35B"},
    {"id": "llama3.1:8b", "name": "Llama 3.1 8B"}
  ],
  "default_model": "qwen3:35b"
}
```

The model list is fetched live from the configured LLM provider.

---

## POST /instant

Instant (transient) analysis. Nothing is written to PostgreSQL. Results are stored in Valkey for 24 hours.

```http
POST /api/analysis/instant
Authorization: Bearer <token>   (or X-API-Key with scope "analysis")
Content-Type: application/json

{
  "report_text": "Patient klagt über...",
  "insurance_type": "GKV",
  "treatment_date": "2024-03-15",
  "patient_dob": "1975-04-20",
  "patient_gender": "m",
  "model": "qwen3:35b",
  "use_rag": true,
  "use_cache": true,
  "reasoning": false
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `report_text` | string | required | Physician report text |
| `insurance_type` | string | `"GKV"` | `GKV`, `PKV`, `SELBSTZAHLER`, `BG` |
| `treatment_date` | string | required | ISO-8601 date |
| `patient_dob` | string | null | Used for demographic checks |
| `patient_gender` | string | null | `m`, `w`, `d` |
| `model` | string | null | Override LLM model; null = use default |
| `use_rag` | bool | true | Enable ChromaDB retrieval |
| `use_cache` | bool | true | Store session in Valkey |
| `reasoning` | bool | false | Request chain-of-thought from LLM |

**Response** — `AnalysisResponse`
```json
{
  "mode": "instant",
  "session_id": "uuid-or-null",
  "case_file_id": null,
  "gop_results": [
    {
      "gop_code": "01435",
      "description": "Ärztlicher Brief",
      "source_text": "Befundbericht wurde erstellt",
      "start_char": 142,
      "end_char": 171,
      "confidence": 0.91,
      "color_bg": "#D1FAE5",
      "color_border": "#10B981",
      "status": "vorgeschlagen",
      "mcp_validated": true,
      "reasoning": null,
      "rejection_reason": null
    }
  ],
  "rejected_gops": [
    {"code": "01600", "reason": "Mutual exclusion with 01435"}
  ],
  "report_text": "Patient klagt über...",
  "treatment_date": "2024-03-15",
  "quartal": "2024Q1",
  "mcp_validation_summary": {...}
}
```

---

## POST /persistent

Persistent analysis. Creates a `CaseFile` + `GOPSuggestion` rows + `AuditLog` entry in PostgreSQL.

```http
POST /api/analysis/persistent
Authorization: Bearer <token>
Content-Type: application/json

{
  "patient_id": "uuid",
  "report_text": "...",
  "treatment_date": "2024-03-15",
  "model": null,
  "use_rag": true,
  "use_cache": true,
  "reasoning": false
}
```

Response: same `AnalysisResponse` schema with `mode: "persistent"` and `case_file_id` set.

---

## POST /cases/{case_file_id}/gops/{gop_id}/accept

Human-in-the-loop: accept a GOP suggestion.

```http
POST /api/analysis/cases/{case_file_id}/gops/{gop_id}/accept?reason=Klinisch+korrekt
Authorization: Bearer <token>
```

Creates an `audit_logs` entry with action `gop_akzeptiert`.

**Response**
```json
{"status": "ok", "gop_id": "uuid", "new_status": "akzeptiert"}
```

---

## POST /cases/{case_file_id}/gops/{gop_id}/reject

```http
POST /api/analysis/cases/{case_file_id}/gops/{gop_id}/reject?reason=Nicht+zutreffend
Authorization: Bearer <token>
```

Creates an `audit_logs` entry with action `gop_abgelehnt`.
