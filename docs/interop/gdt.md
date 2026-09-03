# GDT 2.1 Interface

GDT (Gerätedaten-Träger) is the standard data exchange format between medical devices/software in German medical practices. EBM Analyzer implements GDT 2.1 to integrate with PVS (Praxisverwaltungssoftware) systems.

## Format

- Encoding: ISO 8859-1 (Latin-1)
- Line format: `LLL FFFF content \r\n`
  - `LLL`: 3-digit line length (including length and field identifier fields)
  - `FFFF`: 4-digit field identifier
  - `content`: field content
- Supported `Satzarten` (record types): `6310`, `6311`, `8200`, `8220`

## Supported field identifiers

### Patient demographics

| Field | Description |
|---|---|
| `3101` | Last name |
| `3102` | First name |
| `3103` | Date of birth (DDMMYYYY) |
| `3104` | Title |
| `3110` | Gender (`1`=male, `2`=female) |
| `3119` | Patient number |

### Clinical text

| Field | Description |
|---|---|
| `6205` | Free text (Freitext) |
| `6220` | Finding text (Befundtext) |
| `6228` | Additional clinical information |

All three clinical text fields are concatenated and used as `report_text` for analysis.

### Treatment date

| Field | Description |
|---|---|
| `6200` | Treatment date (DDMMYYYY) |

### Insurance

| Field | Description |
|---|---|
| `4111` | Insurance type (`1`=GKV, `5`=PKV) |

## Upload flow

1. PVS software generates a `.gdt` file and sends it to `POST /api/interop/gdt/analyze`.
2. EBM Analyzer parses the file, runs the analysis pipeline, and caches the GDT response.
3. The response includes `gdt_download_url` pointing to the cached result.
4. PVS software fetches the `.gdt` file and imports the validated GOPs.

## GDT response format

The exported GDT file contains:

- `Satzart` 6310 (device data)
- One or more field 6220 entries with validated GOP codes and descriptions
- One or more field 6220 entries with rejected codes and rejection reasons

## Error handling

| HTTP code | Condition |
|---|---|
| 413 | File exceeds 1 MB limit |
| 422 | Parse error (invalid GDT format) |
| 422 | No clinical text found (fields 6205/6220/6228 all empty) |

## Implementation

Source: `src/integration/gdt_handler.py`

Key functions:
- `parse_gdt_bytes(data: bytes) -> GDTRecord` — parse a GDT file
- `gop_results_to_gdt(analysis_response, patient) -> bytes` — build a GDT response
- `GDTRecord.combined_clinical_text` — concatenated clinical text from fields 6205/6220/6228
