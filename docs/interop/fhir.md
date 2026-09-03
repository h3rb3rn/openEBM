# HL7 FHIR R4 Interface

EBM Analyzer exposes a FHIR R4 custom operation `$analyze-ebm` for integration with modern EMR systems.

## Endpoint

```
POST /api/interop/fhir/v4/$analyze-ebm
```

Authentication: `Authorization: Bearer <token>` or `X-API-Key` with scope `interop`.

## Supported resource types

### DocumentReference

Clinical text is extracted from `content[].attachment.data` (base64-encoded UTF-8).

```json
{
  "resourceType": "DocumentReference",
  "status": "current",
  "content": [
    {
      "attachment": {
        "contentType": "text/plain;charset=UTF-8",
        "data": "UGF0aWVudCBrb..."
      }
    }
  ],
  "context": {
    "period": {"start": "2024-03-15T08:00:00Z"}
  },
  "extension": [
    {
      "url": "https://example.org/fhir/insurance-type",
      "valueString": "GKV"
    }
  ]
}
```

Fallback: if `data` is absent, `attachment.title` is used as the clinical text.

### Composition

Clinical text is extracted from all `section[].text.div` elements. XHTML tags are stripped.

```json
{
  "resourceType": "Composition",
  "status": "final",
  "date": "2024-03-15",
  "section": [
    {
      "title": "Anamnese",
      "text": {
        "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\">Patient berichtet...</div>"
      }
    }
  ]
}
```

### Parameters

Wraps any of the above as `parameter[name=document].resource`. Additional parameters:

| Parameter name | Type | Description |
|---|---|---|
| `document` | resource | Inner `DocumentReference` or `Composition` |
| `insuranceType` | valueString | `GKV`, `PKV`, etc. |
| `treatmentDate` | valueDate | ISO-8601 date |

```json
{
  "resourceType": "Parameters",
  "parameter": [
    {
      "name": "document",
      "resource": { "resourceType": "DocumentReference", ... }
    },
    {"name": "insuranceType", "valueString": "PKV"},
    {"name": "treatmentDate", "valueDate": "2024-03-15"}
  ]
}
```

## Response

FHIR R4 `Parameters` resource:

```json
{
  "resourceType": "Parameters",
  "parameter": [
    {"name": "status",              "valueString": "validated"},
    {"name": "treatmentDate",       "valueDate": "2024-03-15"},
    {"name": "validatedCodeCount",  "valueInteger": 2},
    {"name": "rejectedCodeCount",   "valueInteger": 1},
    {
      "name": "ebmCode",
      "part": [
        {"name": "code",          "valueCode": "01435"},
        {"name": "display",       "valueString": "Ärztlicher Brief"},
        {"name": "confidence",    "valueDecimal": 0.91},
        {"name": "sourceText",    "valueString": "Befundbericht..."},
        {"name": "mcpValidated",  "valueBoolean": true}
      ]
    }
  ]
}
```

## Implementation

Source: `src/app/api/interop.py`

Functions:
- `_extract_fhir_content(body, resource_type)` — recursive content extractor
- `_build_fhir_parameters_response(result, treatment_date)` — result serialiser
