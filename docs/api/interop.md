# Interoperability API

Base path: `/api/interop`

All endpoints require authentication. GDT and FHIR endpoints require scope `interop` for API key callers.

---

## GET /status

Returns the current state of all interop interfaces. Useful for the admin dashboard.

```http
GET /api/interop/status
Authorization: Bearer <token>
```

**Response**
```json
{
  "gdt": {
    "enabled": true,
    "description": "GDT 2.1 Upload/Download (ISO 8859-1)",
    "upload_endpoint": "/api/interop/gdt/analyze",
    "export_endpoint": "/api/interop/gdt/export/{session_id}",
    "supported_satzarten": ["6310", "6311", "8200", "8220"],
    "supported_fields": ["6205", "6220", "6228", "3101-3119"]
  },
  "fhir": {
    "enabled": true,
    "version": "R4",
    "description": "HL7 FHIR R4 $analyze-ebm Custom Operation",
    "endpoint": "/api/interop/fhir/v4/$analyze-ebm",
    "supported_resources": ["DocumentReference", "Composition", "Parameters"]
  },
  "mcp_external_tool": {
    "enabled": true,
    "tool_name": "analyze_clinical_text_for_ebm",
    "description": "EBM analysis as external MCP tool for AI agents",
    "transport": "HTTP/SSE"
  },
  "internal_channel": {
    "enabled": true,
    "description": "Internal MCP→FastAPI channel (X-Internal-Key)"
  }
}
```

---

## POST /gdt/analyze

Upload a GDT 2.1 file, run EBM analysis and receive result JSON plus a GDT download URL.

```http
POST /api/interop/gdt/analyze
Authorization: Bearer <token>   (or X-API-Key with scope "interop")
Content-Type: multipart/form-data

file=@/path/to/patient.gdt
```

**Response**
```json
{
  "status": "ok",
  "gop_count": 3,
  "rejected_count": 1,
  "treatment_date": "2024-03-15",
  "patient": {
    "patient_number": "P-001",
    "last_name": "Mustermann",
    "first_name": "Max",
    "insurance_type": "GKV"
  },
  "gop_results": [...],
  "rejected_gops": [...],
  "gdt_export_session_id": "uuid",
  "gdt_download_url": "/api/interop/gdt/export/uuid"
}
```

Error codes:

| Code | Condition |
|---|---|
| 413 | File larger than 1 MB |
| 422 | GDT parse error or no clinical text found (fields 6205/6220/6228) |

---

## GET /gdt/export/{session_id}

Download the GDT response file for a completed analysis.

```http
GET /api/interop/gdt/export/{session_id}
Authorization: Bearer <token>   (or X-API-Key with scope "interop")
```

Returns the GDT bytes as `application/x-gdt` with `Content-Disposition: attachment; filename=ebm_export_*.gdt`.

Session expires after 1 hour. Returns 404 if expired or not found.

---

## POST /fhir/v4/$analyze-ebm

HL7 FHIR R4 custom operation. Accepts any of:

- `DocumentReference` (clinical text in `content[].attachment.data` as base64)
- `Composition` (clinical text in `section[].text.div`, XHTML stripped)
- `Parameters` (wraps any of the above as `parameter[name=document].resource`)

```http
POST /api/interop/fhir/v4/$analyze-ebm
Authorization: Bearer <token>   (or X-API-Key with scope "interop")
Content-Type: application/json

{
  "resourceType": "DocumentReference",
  "status": "current",
  "content": [
    {
      "attachment": {
        "contentType": "text/plain",
        "data": "UGF0aWVudCBrb..."
      }
    }
  ],
  "context": {
    "period": {"start": "2024-03-15"}
  }
}
```

**Response** — FHIR R4 `Parameters` resource

```json
{
  "resourceType": "Parameters",
  "parameter": [
    {"name": "status", "valueString": "validated"},
    {"name": "treatmentDate", "valueDate": "2024-03-15"},
    {"name": "validatedCodeCount", "valueInteger": 2},
    {"name": "rejectedCodeCount", "valueInteger": 0},
    {
      "name": "ebmCode",
      "part": [
        {"name": "code", "valueCode": "01435"},
        {"name": "display", "valueString": "Ärztlicher Brief"},
        {"name": "confidence", "valueDecimal": 0.91},
        {"name": "sourceText", "valueString": "Befundbericht..."},
        {"name": "mcpValidated", "valueBoolean": true}
      ]
    }
  ]
}
```

---

## POST /internal/analyze

Container-internal endpoint. Used by the MCP server to invoke the full analysis pipeline. Not intended for external callers.

```http
POST /api/interop/internal/analyze
X-Internal-Key: <INTERNAL_API_KEY>
Content-Type: application/json

{
  "clinical_text": "Patient klagt über...",
  "insurance_type": "GKV",
  "treatment_date": "2024-03-15",
  "patient_dob": null,
  "patient_gender": null,
  "use_rag": true,
  "reasoning": false,
  "model": null
}
```

**Response**
```json
{
  "final_gops": [...],
  "rejected": [...],
  "rag_candidates_count": 5,
  "mcp_validation": {...}
}
```
