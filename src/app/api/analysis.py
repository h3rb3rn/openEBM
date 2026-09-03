"""
Core analysis orchestrator: processes medical reports and returns validated EBM GOPs.

Split-brain pipeline:
  1. RAG  — semantic GOP candidates from ChromaDB
  2. LLM  — probabilistic text extraction + character offsets
  3. MCP  — deterministic rule validation (exclusions, time profiles, demographics)
  4. Merge — consolidate results with colors; persist (persistent mode) or cache in Valkey (instant mode)
"""
import hashlib
import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import get_current_user, require_scope, get_tenant_db, set_tenant_context, CurrentUser
from ..models.base import (
    CaseFile, GOPSuggestion, AuditLog, Patient,
    CaseStatus, GOPStatus, AuditAction
)
from ..services.rag_service import retrieve_gop_candidates, build_rag_context
from ..services.llm_service import analyze_report, list_available_models
from ..services.mcp_client import MCPClient
from ..services.session_service import (
    create_instant_session, update_instant_session, get_instant_session
)

logger = logging.getLogger(__name__)
router = APIRouter()
mcp_client = MCPClient()

# Color palette for GOP codes — index derived deterministically from SHA-256 of the code
GOP_COLORS = [
    "#FEF3C7", "#D1FAE5", "#DBEAFE", "#EDE9FE", "#FCE7F3",
    "#FEE2E2", "#CCFBF1", "#FFF7ED", "#E0F2FE", "#F0FDF4",
]
GOP_BORDER_COLORS = [
    "#F59E0B", "#10B981", "#3B82F6", "#8B5CF6", "#EC4899",
    "#EF4444", "#14B8A6", "#F97316", "#0EA5E9", "#22C55E",
]


def assign_color(gop_code: str) -> tuple[str, str]:
    idx = int(hashlib.md5(gop_code.encode()).hexdigest(), 16) % len(GOP_COLORS)
    return GOP_COLORS[idx], GOP_BORDER_COLORS[idx]


def _quartal_from_date(d: date) -> str:
    q = (d.month - 1) // 3 + 1
    return f"{d.year}Q{q}"


# ─── Request/Response Schemas ────────────────────────────────────────

class AnalysisOptions(BaseModel):
    model: str | None = None
    use_rag: bool = True        # enable RAG / knowledge-base retrieval
    use_cache: bool = True      # enable Valkey session cache
    reasoning: bool = False     # enable thinking mode (chain-of-thought)


class InstantAnalysisRequest(BaseModel):
    report_text: str
    insurance_type: str = "GKV"
    treatment_date: str
    patient_dob: str | None = None
    patient_gender: str | None = None
    model: str | None = None
    use_rag: bool = True
    use_cache: bool = True
    reasoning: bool = False


class PersistentAnalysisRequest(BaseModel):
    patient_id: str
    report_text: str
    treatment_date: str
    model: str | None = None
    use_rag: bool = True
    use_cache: bool = True
    reasoning: bool = False


class GOPResult(BaseModel):
    gop_code: str
    description: str | None
    source_text: str | None
    start_char: int | None
    end_char: int | None
    confidence: float
    color_bg: str
    color_border: str
    status: str
    mcp_validated: bool
    reasoning: str | None
    rejection_reason: str | None = None


class AnalysisResponse(BaseModel):
    mode: str  # "instant" | "persistent"
    session_id: str | None
    case_file_id: str | None
    gop_results: list[GOPResult]
    rejected_gops: list[dict]
    report_text: str
    treatment_date: str
    quartal: str
    mcp_validation_summary: dict


# ─── Endpoints ───────────────────────────────────────────────────────

@router.get("/models")
async def get_available_models(current_user: CurrentUser = Depends(get_current_user)):
    """Return available models and the configured default model."""
    from ..config import get_settings as _gs
    s = _gs()
    default = s.ollama_model if s.llm_provider == "ollama" else s.openai_model
    return {"models": await list_available_models(), "default_model": default}


@router.post("/instant", response_model=AnalysisResponse)
async def instant_analysis(
    request: InstantAnalysisRequest,
    current_user: CurrentUser = Depends(require_scope("analysis")),
):
    """
    INSTANT / TRANSIENT MODE:
    Analysis runs entirely in-memory (Valkey). Nothing is written to PostgreSQL.
    """
    patient_override = None
    if request.patient_dob or request.patient_gender:
        patient_override = {
            "date_of_birth": request.patient_dob,
            "gender": request.patient_gender,
            "insurance_type": request.insurance_type,
        }

    session_id = None
    if request.use_cache:
        session_id = await create_instant_session(
            tenant_id=current_user.tenant_id,
            report_text=request.report_text,
            patient_data=patient_override,
        )

    result = await _run_analysis_pipeline(
        report_text=request.report_text,
        insurance_type=request.insurance_type,
        treatment_date=request.treatment_date,
        patient_id=None,
        patient_override=patient_override,
        session_id=session_id,
        model=request.model,
        use_rag=request.use_rag,
        reasoning=request.reasoning,
    )

    if request.use_cache and session_id:
        await update_instant_session(current_user.tenant_id, session_id, result)

    quartal = _quartal_from_date(date.fromisoformat(request.treatment_date))
    return _build_response(
        mode="instant",
        session_id=session_id,
        case_file_id=None,
        result=result,
        report_text=request.report_text,
        treatment_date=request.treatment_date,
        quartal=quartal,
    )


@router.post("/persistent", response_model=AnalysisResponse)
async def persistent_analysis(
    request: PersistentAnalysisRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """
    PERSISTENT MODE: Creates a case file in PostgreSQL, persists all GOPs
    and writes an audit log entry.
    """
    # Validate patient — tenant isolation check
    result = await db.execute(
        select(Patient).where(
            Patient.id == request.patient_id,
            Patient.tenant_id == current_user.tenant_id,
            Patient.is_active == True,
        )
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient nicht gefunden")

    treatment_date = date.fromisoformat(request.treatment_date)
    quartal = _quartal_from_date(treatment_date)

    # Run the analysis pipeline
    analysis_result = await _run_analysis_pipeline(
        report_text=request.report_text,
        insurance_type=patient.insurance_type.value,
        treatment_date=request.treatment_date,
        patient_id=request.patient_id,
        patient_override=None,
        session_id=None,
        model=request.model,
        use_rag=request.use_rag,
        reasoning=request.reasoning,
    )

    # Create the case file record
    case_file = CaseFile(
        tenant_id=current_user.tenant_id,
        patient_id=request.patient_id,
        created_by_id=current_user.id,
        treatment_date=treatment_date,
        report_text=request.report_text,
        status=CaseStatus.IN_BEARBEITUNG,
        quartal=quartal,
    )
    db.add(case_file)
    await db.flush()

    # Persist GOP suggestions
    for gop_data in analysis_result.get("final_gops", []):
        bg, border = assign_color(gop_data["gop_code"])
        suggestion = GOPSuggestion(
            case_file_id=case_file.id,
            gop_code=gop_data["gop_code"],
            gop_description=gop_data.get("description"),
            start_char=gop_data.get("start_char"),
            end_char=gop_data.get("end_char"),
            source_text=gop_data.get("source_text"),
            confidence=gop_data.get("confidence", 0.0),
            color_hex=bg,
            status=GOPStatus.VORGESCHLAGEN,
            mcp_validated=True,
            mcp_exclusion_flags=gop_data.get("mcp_flags"),
            llm_reasoning=gop_data.get("reasoning"),
        )
        db.add(suggestion)

    # Audit log: analysis started
    audit = AuditLog(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        case_file_id=case_file.id,
        action=AuditAction.ANALYSE_GESTARTET,
        log_metadata={
            "gop_count": len(analysis_result.get("final_gops", [])),
            "rejected_count": len(analysis_result.get("rejected", [])),
        },
    )
    db.add(audit)
    await db.commit()
    # SET LOCAL from get_tenant_db doesn't survive the commit above (new
    # transaction) — re-apply before this refresh SELECT, or RLS blocks it.
    await set_tenant_context(db, current_user.tenant_id)
    await db.refresh(case_file)

    return _build_response(
        mode="persistent",
        session_id=None,
        case_file_id=case_file.id,
        result=analysis_result,
        report_text=request.report_text,
        treatment_date=request.treatment_date,
        quartal=quartal,
    )


@router.post("/cases/{case_file_id}/gops/{gop_id}/accept")
async def accept_gop(
    case_file_id: str,
    gop_id: str,
    reason: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Human-in-the-loop: accept a GOP and write an audit log entry."""
    return await _update_gop_status(
        case_file_id, gop_id, GOPStatus.AKZEPTIERT, AuditAction.GOP_AKZEPTIERT,
        reason, current_user, db
    )


@router.post("/cases/{case_file_id}/gops/{gop_id}/reject")
async def reject_gop(
    case_file_id: str,
    gop_id: str,
    reason: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Human-in-the-loop: reject a GOP and write an audit log entry."""
    return await _update_gop_status(
        case_file_id, gop_id, GOPStatus.ABGELEHNT, AuditAction.GOP_ABGELEHNT,
        reason, current_user, db
    )


# ─── Internal helpers ────────────────────────────────────────────────

async def _run_analysis_pipeline(
    report_text: str,
    insurance_type: str,
    treatment_date: str,
    patient_id: str | None,
    patient_override: dict | None,
    session_id: str | None,
    model: str | None = None,
    use_rag: bool = True,
    reasoning: bool = False,
) -> dict:
    """Execute RAG → LLM → MCP and return a consolidated result dict."""

    # Phase 1: RAG (optional)
    if use_rag:
        candidates = await retrieve_gop_candidates(
            report_text=report_text,
            insurance_type=insurance_type,
            treatment_date=treatment_date,
        )
    else:
        candidates = []
        logger.info("RAG disabled — skipping knowledge-base retrieval")
    rag_context = build_rag_context(candidates)
    candidate_map = {c["code"]: c for c in candidates}

    # Phase 2: LLM
    llm_suggestions = await analyze_report(
        report_text=report_text,
        insurance_type=insurance_type,
        treatment_date=treatment_date,
        rag_context=rag_context,
        model=model,
        reasoning=reasoning,
    )

    proposed_codes = [s["gop_code"] for s in llm_suggestions if s.get("gop_code")]

    # Phase 3: MCP validation (deterministic rules)
    if proposed_codes:
        validation = await mcp_client.validate_full(
            gop_codes=proposed_codes,
            treatment_date=treatment_date,
            patient_id=patient_id,
            session_id=session_id,
            patient_override=patient_override,
        )
    else:
        validation = {"final_allowed": [], "rejected": []}

    allowed_set = set(validation.get("final_allowed", []))

    # Phase 4: merge results
    final_gops = []
    for s in llm_suggestions:
        code = s.get("gop_code")
        if code and code in allowed_set:
            final_gops.append({
                "gop_code": code,
                "description": candidate_map.get(code, {}).get("description"),
                "source_text": s.get("source_text"),
                "start_char": s.get("start_char"),
                "end_char": s.get("end_char"),
                "confidence": s.get("confidence", 0.0),
                "reasoning": s.get("reasoning"),
                "mcp_flags": None,
            })

    return {
        "final_gops": final_gops,
        "rejected": validation.get("rejected", []),
        "mcp_validation": validation,
        "rag_candidates_count": len(candidates),
    }


def _build_response(
    mode: str,
    session_id: str | None,
    case_file_id: str | None,
    result: dict,
    report_text: str,
    treatment_date: str,
    quartal: str,
) -> AnalysisResponse:
    gop_results = []
    for gop in result.get("final_gops", []):
        bg, border = assign_color(gop["gop_code"])
        gop_results.append(GOPResult(
            gop_code=gop["gop_code"],
            description=gop.get("description"),
            source_text=gop.get("source_text"),
            start_char=gop.get("start_char"),
            end_char=gop.get("end_char"),
            confidence=gop.get("confidence", 0.0),
            color_bg=bg,
            color_border=border,
            status="vorgeschlagen",
            mcp_validated=True,
            reasoning=gop.get("reasoning"),
        ))

    return AnalysisResponse(
        mode=mode,
        session_id=session_id,
        case_file_id=case_file_id,
        gop_results=gop_results,
        rejected_gops=result.get("rejected", []),
        report_text=report_text,
        treatment_date=treatment_date,
        quartal=quartal,
        mcp_validation_summary=result.get("mcp_validation", {}),
    )


async def _update_gop_status(
    case_file_id: str,
    gop_id: str,
    new_status: GOPStatus,
    audit_action: AuditAction,
    reason: str | None,
    current_user: CurrentUser,
    db: AsyncSession,
):
    result = await db.execute(
        select(GOPSuggestion)
        .join(CaseFile, CaseFile.id == GOPSuggestion.case_file_id)
        .where(
            GOPSuggestion.id == gop_id,
            CaseFile.id == case_file_id,
            CaseFile.tenant_id == current_user.tenant_id,
        )
    )
    gop = result.scalar_one_or_none()
    if not gop:
        raise HTTPException(status_code=404, detail="GOP-Eintrag nicht gefunden")

    gop.status = new_status
    audit = AuditLog(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        case_file_id=case_file_id,
        gop_suggestion_id=gop_id,
        action=audit_action,
        gop_code=gop.gop_code,
        reason=reason,
    )
    db.add(audit)
    await db.commit()
    return {"status": "ok", "gop_id": gop_id, "new_status": new_status.value}
