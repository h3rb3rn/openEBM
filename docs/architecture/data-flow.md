# Data Flow

## Analysis pipeline

```
Input: report_text, insurance_type, treatment_date
       [optional: patient_id | patient_override]
       [options: use_rag, use_cache, model, reasoning]

Phase 1 — RAG (skipped when use_rag=False)
────────────────────────────────────────────
  report_text
    → embed (ChromaDB default embedding function)
    → cosine similarity search against EBM GOP collection
    → top-k candidates: [{code, description, distance}]
    → build_rag_context() formats candidates into prompt section

Phase 2 — LLM
────────────────────────────────────────────
  Prompt composition:
    [system] EBM billing assistant instructions + output format spec
    [user]   RAG context (if available) + report_text

  LLM call:
    if OLLAMA_BASE_URL responds to /api/tags  → native Ollama /api/generate
    else                                      → OpenAI-compatible /v1/chat/completions
                                                  with stream=True

  Parse response:
    Expect JSON array of:
      {gop_code, source_text, start_char, end_char, confidence, reasoning}

Phase 3 — MCP validation
────────────────────────────────────────────
  MCP client sends proposed_codes to MCP server via SSE transport:

  ┌── validate_full(gop_codes, treatment_date, patient_id, patient_override)
  │     1. ebm_validator_exclusions(codes, date)
  │          → Neo4j: MATCH (g:GOP)-[:EXCLUDES]->()
  │          → Valkey cache (24 h TTL)
  │          → greedy allowed/banned matrix
  │
  │     2. ebm_validator_time_budget(codes, date)
  │          → Neo4j: time_value_minutes per code
  │          → Valkey accumulator for daily total
  │          → budget_exceeded flag + flagged_codes
  │
  │     3. ebm_validator_demographics(codes, date, patient)
  │          → Neo4j: DemographicRestriction nodes
  │          → PostgreSQL: patient.date_of_birth, gender, insurance_type
  │          → age / gender / insurance checks
  │
  └── final_allowed = exclusions.allowed ∩ budget_ok_codes ∩ demographics.allowed

Phase 4 — Merge
────────────────────────────────────────────
  for each LLM suggestion where code ∈ final_allowed:
    build GOPResult with color (deterministic hash), mcp_validated=True

  rejected = MCP rejected codes + reason strings

Output
───────────���────────────────────────────────
  Instant mode:  JSON response + Valkey session update
  Persistent mode: PostgreSQL insert (CaseFile + GOPSuggestion rows + AuditLog)
```

---

## GDT interop flow

```
PVS software
  → writes .gdt file (ISO 8859-1, GDT 2.1 format)
  → POST /api/interop/gdt/analyze  (multipart/form-data)

FastAPI
  → parse_gdt_bytes(): extract patient demographics + clinical text
  → call _run_analysis_pipeline() (same as above)
  → gop_results_to_gdt(): encode result back to GDT bytes
  → cache in Valkey (TTL 1 h)
  → return JSON with gdt_download_url

PVS software
  → GET /api/interop/gdt/export/{session_id}
  → receives .gdt attachment with validated GOPs
  → imports into PVS patient record
```

---

## FHIR R4 interop flow

```
EMR system
  → POST /api/interop/fhir/v4/$analyze-ebm
    Body: DocumentReference | Composition | Parameters (FHIR R4 JSON)

FastAPI
  → _extract_fhir_content(): parse resource type
      DocumentReference → content[].attachment.data (base64 decode)
      Composition       → section[].text.div (strip XHTML tags)
      Parameters        → unwrap inner resource
  → call _run_analysis_pipeline()
  → _build_fhir_parameters_response(): wrap result as FHIR Parameters

Response: FHIR Parameters {
  status, treatmentDate, validatedCodeCount, rejectedCodeCount,
  [ebmCode {code, display, confidence, sourceText, mcpValidated}]
}
```

---

## MCP internal channel flow

```
MCP server (external AI agent calls analyze_clinical_text_for_ebm tool)
  → _call_analyze_clinical_text()
  → POST http://app:8000/api/interop/internal/analyze
    headers: {X-Internal-Key: INTERNAL_API_KEY}
    body: {clinical_text, insurance_type, treatment_date, ...}

FastAPI internal endpoint
  → validates X-Internal-Key
  → call _run_analysis_pipeline()
  → return {final_gops, rejected, rag_candidates_count, mcp_validation}

MCP server
  → serialize result as JSON TextContent
  → return to AI agent caller
```
