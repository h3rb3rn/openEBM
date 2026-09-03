# MCP External Tool

EBM Analyzer exposes the full analysis pipeline as an MCP (Model Context Protocol) tool. External AI agents in the clinic network can invoke this tool via the MCP server's HTTP/SSE transport.

## Tool: `analyze_clinical_text_for_ebm`

### Description

Full EBM billing analysis for a clinical text. Uses GraphRAG (ChromaDB + Neo4j), an LLM, and deterministic MCP validation. Returns validated EBM GOPs with confidence scores, text anchors and MCP audit report.

### Input schema

| Parameter | Type | Required | Description |
|---|---|---|---|
| `clinical_text` | string | yes | Free-text clinical document in German |
| `insurance_type` | string | no | `GKV`, `PKV`, `SELBSTZAHLER`, `BG` (default: `GKV`) |
| `treatment_date` | string | no | ISO-8601 date (default: today) |
| `patient_dob` | string | no | Date of birth for age restrictions |
| `patient_gender` | string | no | `m`, `w`, `d` for gender-specific GOPs |
| `use_rag` | bool | no | Enable knowledge-base retrieval (default: true) |
| `reasoning` | bool | no | Enable chain-of-thought (default: false) |

### Output

JSON string:
```json
{
  "final_gops": [
    {
      "gop_code": "01435",
      "description": "Ärztlicher Brief",
      "source_text": "Befundbericht erstellt",
      "start_char": 142,
      "end_char": 165,
      "confidence": 0.91,
      "reasoning": null
    }
  ],
  "rejected": [
    {"code": "01600", "reason": "Mutual exclusion with 01435"}
  ],
  "rag_candidates_count": 5,
  "mcp_validation": {...}
}
```

## Internal channel

When the MCP tool is called, it forwards the request to `POST /api/interop/internal/analyze` on the FastAPI app using a shared secret (`X-Internal-Key`). This is a container-internal HTTP call — the MCP server must be able to reach `http://app:8000` (or the configured `APP_INTERNAL_URL`).

```
AI agent
  → MCP call: analyze_clinical_text_for_ebm({...})
  → MCP server: _call_analyze_clinical_text()
  → HTTP POST http://app:8000/api/interop/internal/analyze
    X-Internal-Key: <INTERNAL_API_KEY>
  → FastAPI: _run_analysis_pipeline()
  → Response serialised as JSON TextContent
  → returned to AI agent
```

## MCP server transport

The MCP server exposes:

| Path | Description |
|---|---|
| `GET /health` | Health probe (no auth) |
| `GET /sse` | SSE endpoint for MCP clients |
| `POST /messages/` | SSE message channel |

Authentication: `X-MCP-Secret` header with value from `MCP_SECRET` env variable.

## Validation tools

In addition to the full analysis tool, the MCP server exposes three deterministic validation tools:

| Tool | Description |
|---|---|
| `ebm_validator_exclusions` | Mutual exclusion check (§ 1 Abs. 3 EBM) |
| `ebm_validator_time_budget` | Daily time budget check (§ 46 BMV-Ä) |
| `ebm_validator_demographics` | Age/gender/insurance restrictions |

These are called internally by `MCPClient.validate_full()` in the FastAPI app, but are also available to external AI agents that want fine-grained validation control.
