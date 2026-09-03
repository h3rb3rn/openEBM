"""
Interoperability router: GDT/BDT (legacy PVS) + HL7 FHIR R4 (modern EMR).

Endpoints:
  POST /interop/gdt/analyze          -> upload GDT file -> analysis -> GDT response
  GET  /interop/gdt/export/{sid}     -> download the GDT export for a session id
  POST /interop/fhir/v4/$analyze-ebm -> FHIR DocumentReference -> EBM analysis
  POST /interop/internal/analyze     -> internal channel for the MCP tool (X-Internal-Key)
  GET  /interop/status               -> interop service status for the admin UI
"""
import base64
import logging
import re
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File
from fastapi.responses import Response

from ..api.auth import get_current_user, require_scope, CurrentUser
from ..config import get_settings
from ...integration.gdt_handler import (
    parse_gdt_bytes, gop_results_to_gdt, GDTPatient,
)

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


# ─── GDT/BDT upload & export ─────────────────────────────────────────────────

@router.post("/gdt/analyze")
async def gdt_analyze(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_scope("interop")),
):
    """
    Accept a GDT file, extract patient data + clinical text, run the EBM
    analysis and return result JSON plus a GDT download URL.
    """
    raw = await file.read()
    if len(raw) > 1_000_000:
        raise HTTPException(status_code=413, detail="GDT-Datei zu groß (max 1 MB)")

    try:
        record = parse_gdt_bytes(raw)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"GDT-Parsing fehlgeschlagen: {e}")

    clinical_text = record.combined_clinical_text
    if not clinical_text:
        raise HTTPException(
            status_code=422,
            detail="Kein klinischer Text in GDT-Datei gefunden (Feldkennungen 6205/6220/6228).",
        )

    patient = record.patient
    patient_override = {
        "date_of_birth": patient.date_of_birth,
        "gender": patient.gender,
        "insurance_type": patient.insurance_type,
    }

    # Run the same pipeline as the instant analysis
    from ..api.analysis import _run_analysis_pipeline
    result = await _run_analysis_pipeline(
        report_text=clinical_text,
        insurance_type=patient.insurance_type,
        treatment_date=record.treatment_date,
        patient_id=None,
        patient_override={k: v for k, v in patient_override.items() if v},
        session_id=None,
        model=None,
        use_rag=True,
        reasoning=False,
    )

    gop_results = [
        {
            "gop_code": g["gop_code"],
            "description": g.get("description"),
            "confidence": g.get("confidence", 0.0),
        }
        for g in result.get("final_gops", [])
    ]
    rejected = result.get("rejected", [])

    # Build the GDT response bytes
    analysis_response = {
        "gop_results": gop_results,
        "rejected_gops": rejected,
        "treatment_date": record.treatment_date,
    }
    gdt_bytes = gop_results_to_gdt(analysis_response, patient)

    # Cache the GDT bytes in Valkey for the download endpoint
    session_id = await _cache_gdt_export(
        tenant_id=current_user.tenant_id,
        gdt_bytes=gdt_bytes,
        ttl=3600,
    )

    return {
        "status": "ok",
        "gop_count": len(gop_results),
        "rejected_count": len(rejected),
        "treatment_date": record.treatment_date,
        "patient": {
            "patient_number": patient.patient_number,
            "last_name": patient.last_name,
            "first_name": patient.first_name,
            "insurance_type": patient.insurance_type,
        },
        "gop_results": gop_results,
        "rejected_gops": rejected,
        "gdt_export_session_id": session_id,
        "gdt_download_url": f"/api/interop/gdt/export/{session_id}",
    }


@router.get("/gdt/export/{session_id}")
async def gdt_export_download(
    session_id: str,
    current_user: CurrentUser = Depends(require_scope("interop")),
):
    """Return the cached GDT response file as a download."""
    gdt_bytes = await _fetch_gdt_export(current_user.tenant_id, session_id)
    if gdt_bytes is None:
        raise HTTPException(status_code=404, detail="GDT-Export nicht gefunden oder abgelaufen.")

    return Response(
        content=gdt_bytes,
        media_type="application/x-gdt",
        headers={
            "Content-Disposition": f"attachment; filename=ebm_export_{session_id[:8]}.gdt",
            "Content-Length": str(len(gdt_bytes)),
        },
    )


# ─── FHIR R4 endpoint ────────────────────────────────────────────────────────

@router.post("/fhir/v4/$analyze-ebm")
async def fhir_analyze_ebm(
    body: dict,
    current_user: CurrentUser = Depends(require_scope("interop")),
):
    """
    HL7 FHIR R4 Custom Operation: $analyze-ebm

    Accepts:
      - FHIR DocumentReference (content[].attachment with base64 data)
      - FHIR Composition (section[].text.div)
      - FHIR Parameters (parameter with name=document, valueResource=...)

    Returns a FHIR Parameters resource containing the validated GOPs.
    """
    resource_type = body.get("resourceType", "")

    clinical_text, patient_override, treatment_date = _extract_fhir_content(body, resource_type)

    if not clinical_text:
        raise HTTPException(
            status_code=422,
            detail="Kein klinischer Text in FHIR-Ressource gefunden. "
                   "Erwartet: DocumentReference.content[].attachment.data (base64) "
                   "oder Composition.section[].text.div",
        )

    from ..api.analysis import _run_analysis_pipeline
    result = await _run_analysis_pipeline(
        report_text=clinical_text,
        insurance_type=patient_override.get("insurance_type", "GKV"),
        treatment_date=treatment_date,
        patient_id=None,
        patient_override=patient_override or None,
        session_id=None,
        model=None,
        use_rag=True,
        reasoning=False,
    )

    # Build the FHIR Parameters response resource
    return _build_fhir_parameters_response(result, treatment_date)


def _extract_fhir_content(body: dict, resource_type: str) -> tuple[str, dict, str]:
    """Extract clinical text, patient data and treatment date from a FHIR resource."""
    clinical_text = ""
    patient_override: dict = {}
    treatment_date = date.today().isoformat()

    if resource_type == "DocumentReference":
        # Text from content[].attachment.data (base64) or .title
        for content_item in body.get("content", []):
            att = content_item.get("attachment", {})
            if att.get("data"):
                try:
                    clinical_text = base64.b64decode(att["data"]).decode("utf-8", errors="replace")
                    break
                except Exception:
                    pass
            elif att.get("title"):
                clinical_text = att["title"]

        # Date from context.period.start
        ctx = body.get("context", {})
        period = ctx.get("period", {})
        if period.get("start"):
            treatment_date = period["start"][:10]

        # Insurance type from extensions (best effort)
        for ext in body.get("extension", []):
            if "insurance" in ext.get("url", "").lower():
                patient_override["insurance_type"] = ext.get("valueString", "GKV")

    elif resource_type == "Composition":
        # Text from sections, with XHTML tags stripped
        parts = []
        for section in body.get("section", []):
            div = section.get("text", {}).get("div", "")
            parts.append(re.sub(r"<[^>]+>", " ", div).strip())
        clinical_text = "\n".join(p for p in parts if p)

        if body.get("date"):
            treatment_date = body["date"][:10]

    elif resource_type == "Parameters":
        # FHIR Parameters envelope (e.g. an $operation call)
        for param in body.get("parameter", []):
            name = param.get("name", "")
            if name == "document" and param.get("resource"):
                inner = param["resource"]
                inner_text, inner_patient, inner_date = _extract_fhir_content(
                    inner, inner.get("resourceType", "")
                )
                clinical_text = clinical_text or inner_text
                patient_override.update(inner_patient)
                if inner_date != date.today().isoformat():
                    treatment_date = inner_date
            elif name == "insuranceType":
                patient_override["insurance_type"] = param.get("valueString", "GKV")
            elif name == "treatmentDate":
                treatment_date = param.get("valueDate", treatment_date)

    return clinical_text, patient_override, treatment_date


def _build_fhir_parameters_response(result: dict, treatment_date: str) -> dict:
    """Build a FHIR Parameters resource from the analysis result."""
    final_gops = result.get("final_gops", [])
    rejected = result.get("rejected", [])

    gop_params = []
    for g in final_gops:
        gop_params.append({
            "name": "ebmCode",
            "part": [
                {"name": "code", "valueCode": g.get("gop_code", "")},
                {"name": "display", "valueString": (g.get("description") or "")[:200]},
                {"name": "confidence", "valueDecimal": round(g.get("confidence", 0.0), 3)},
                {"name": "sourceText", "valueString": g.get("source_text") or ""},
                {"name": "mcpValidated", "valueBoolean": True},
            ],
        })

    return {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "status",
                "valueString": "validated",
            },
            {
                "name": "treatmentDate",
                "valueDate": treatment_date,
            },
            {
                "name": "validatedCodeCount",
                "valueInteger": len(final_gops),
            },
            {
                "name": "rejectedCodeCount",
                "valueInteger": len(rejected),
            },
            *gop_params,
        ],
    }


# ─── Internal channel for the MCP tool ───────────────────────────────────────

@router.post("/internal/analyze")
async def internal_analyze(
    body: dict,
    x_internal_key: str = Header(alias="X-Internal-Key"),
):
    """
    Internal analysis endpoint for the MCP tool `analyze_clinical_text_for_ebm`.
    Authenticated via X-Internal-Key (no JWT; container-internal calls only).
    NOT intended for external clients.
    """
    if x_internal_key != settings.internal_api_key:
        raise HTTPException(status_code=401, detail="Ungültiger Internal-Key")

    clinical_text = body.get("clinical_text", "").strip()
    if not clinical_text:
        raise HTTPException(status_code=422, detail="Kein klinischer Text übergeben.")

    from ..api.analysis import _run_analysis_pipeline
    result = await _run_analysis_pipeline(
        report_text=clinical_text,
        insurance_type=body.get("insurance_type", "GKV"),
        treatment_date=body.get("treatment_date", date.today().isoformat()),
        patient_id=None,
        patient_override=_build_patient_override(body),
        session_id=None,
        model=body.get("model"),
        use_rag=body.get("use_rag", True),
        reasoning=body.get("reasoning", False),
    )

    return {
        "final_gops": result.get("final_gops", []),
        "rejected": result.get("rejected", []),
        "rag_candidates_count": result.get("rag_candidates_count", 0),
        "mcp_validation": result.get("mcp_validation", {}),
    }


def _build_patient_override(body: dict) -> dict | None:
    override = {}
    if body.get("patient_dob"):
        override["date_of_birth"] = body["patient_dob"]
    if body.get("patient_gender"):
        override["gender"] = body["patient_gender"]
    if body.get("insurance_type"):
        override["insurance_type"] = body["insurance_type"]
    return override or None


# ─── Status endpoint for the admin UI ────────────────────────────────────────

@router.get("/status")
async def interop_status(current_user: CurrentUser = Depends(get_current_user)):
    """Return the status of all interop interfaces (for the admin dashboard)."""
    return {
        "gdt": {
            "enabled": True,
            "description": "GDT 2.1 Upload/Download (ISO 8859-1)",
            "upload_endpoint": "/api/interop/gdt/analyze",
            "export_endpoint": "/api/interop/gdt/export/{session_id}",
            "supported_satzarten": ["6310", "6311", "8200", "8220"],
            "supported_fields": ["6205", "6220", "6228", "3101-3119"],
        },
        "fhir": {
            "enabled": True,
            "version": "R4",
            "description": "HL7 FHIR R4 $analyze-ebm Custom Operation",
            "endpoint": "/api/interop/fhir/v4/$analyze-ebm",
            "supported_resources": ["DocumentReference", "Composition", "Parameters"],
        },
        "mcp_external_tool": {
            "enabled": True,
            "tool_name": "analyze_clinical_text_for_ebm",
            "description": "EBM-Analyse als externes MCP-Tool für KI-Agenten im Klinknetz",
            "transport": "HTTP/SSE",
        },
        "internal_channel": {
            "enabled": True,
            "description": "Interner MCP→FastAPI-Kanal (X-Internal-Key)",
        },
    }


# ─── Valkey helpers for the GDT export cache ─────────────────────────────────

async def _cache_gdt_export(tenant_id: str, gdt_bytes: bytes, ttl: int = 3600) -> str:
    import uuid
    import redis.asyncio as aioredis
    sid = str(uuid.uuid4())
    key = f"gdt_export:{tenant_id}:{sid}"
    try:
        r = aioredis.from_url(settings.valkey_url)
        await r.set(key, gdt_bytes, ex=ttl)
        await r.aclose()
    except Exception as e:
        logger.warning("Failed to cache GDT export: %s", e)
    return sid


async def _fetch_gdt_export(tenant_id: str, session_id: str) -> bytes | None:
    import redis.asyncio as aioredis
    key = f"gdt_export:{tenant_id}:{session_id}"
    try:
        r = aioredis.from_url(settings.valkey_url)
        data = await r.get(key)
        await r.aclose()
        return data
    except Exception as e:
        logger.warning("Failed to fetch GDT export: %s", e)
        return None
