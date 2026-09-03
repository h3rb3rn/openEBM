# Case Files API

Base path: `/api/cases`

Requires scope `cases` for API key callers.

---

## GET /

List case files. Supports filtering by `patient_id`, `quartal` (e.g. `2024Q1`), and `status`.

```http
GET /api/cases/?quartal=2024Q1&status=offen
Authorization: Bearer <token>
```

Response: array of `CaseFileSummary` (includes GOP counts).

---

## GET /{case_id}

Fetch full case file including all GOP suggestions and audit trail.

```json
{
  "id": "uuid",
  "patient_id": "uuid",
  "treatment_date": "2024-03-15",
  "quartal": "2024Q1",
  "report_text": "Patient klagt über...",
  "status": "in_bearbeitung",
  "notes": null,
  "gop_suggestions": [
    {
      "id": "uuid",
      "gop_code": "01435",
      "gop_description": "Ärztlicher Brief",
      "start_char": 142,
      "end_char": 171,
      "source_text": "Befundbericht wurde erstellt",
      "confidence": 0.91,
      "color_hex": "#D1FAE5",
      "status": "vorgeschlagen",
      "mcp_validated": true,
      "reasoning": null,
      "created_at": "2024-03-15 14:30:00"
    }
  ],
  "audit_trail": [
    {
      "id": "uuid",
      "user_id": "uuid",
      "action": "analyse_gestartet",
      "gop_code": null,
      "reason": null,
      "metadata": {"gop_count": 3, "rejected_count": 1},
      "created_at": "2024-03-15 14:30:00"
    }
  ]
}
```

---

## PATCH /{case_id}/close

Set case status to `abgeschlossen`.

```http
PATCH /api/cases/{case_id}/close
Authorization: Bearer <token>
```

---

## GET /{case_id}/audit

Fetch the full audit trail for a case file in chronological order.

---

## Status values

| Value | Description |
|---|---|
| `offen` | Newly created, no review started |
| `in_bearbeitung` | Analysis run, awaiting GOP decisions |
| `abgeschlossen` | All GOPs reviewed and case closed |
| `archiviert` | Archived |

## GOP suggestion status values

| Value | Description |
|---|---|
| `vorgeschlagen` | Suggested by LLM + validated by MCP |
| `akzeptiert` | Accepted by a human reviewer |
| `abgelehnt` | Rejected by a human reviewer |
