"""Case file API: read, status management, audit log."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import require_scope, get_tenant_db, CurrentUser
from ..models.base import CaseFile, GOPSuggestion, AuditLog, CaseStatus

logger = logging.getLogger(__name__)
router = APIRouter()


class CaseFileSummary(BaseModel):
    id: str
    patient_id: str
    patient_name: str | None
    treatment_date: str
    quartal: str
    status: str
    gop_count: int
    accepted_count: int
    rejected_count: int
    created_at: str


class CaseFileDetail(BaseModel):
    id: str
    patient_id: str
    treatment_date: str
    quartal: str
    report_text: str
    status: str
    notes: str | None
    gop_suggestions: list[dict]
    audit_trail: list[dict]


@router.get("/", response_model=list[CaseFileSummary])
async def list_cases(
    current_user: CurrentUser = Depends(require_scope("cases")),
    db: AsyncSession = Depends(get_tenant_db),
    patient_id: str | None = None,
    quartal: str | None = None,
    status: str | None = None,
):
    query = (
        select(CaseFile)
        .where(CaseFile.tenant_id == current_user.tenant_id)
        .options(selectinload(CaseFile.patient), selectinload(CaseFile.gop_suggestions))
        .order_by(CaseFile.created_at.desc())
    )
    if patient_id:
        query = query.where(CaseFile.patient_id == patient_id)
    if quartal:
        query = query.where(CaseFile.quartal == quartal)
    if status:
        query = query.where(CaseFile.status == CaseStatus(status))

    result = await db.execute(query)
    cases = result.scalars().all()
    return [_to_summary(c) for c in cases]


@router.get("/{case_id}", response_model=CaseFileDetail)
async def get_case(
    case_id: str,
    current_user: CurrentUser = Depends(require_scope("cases")),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(CaseFile)
        .where(CaseFile.id == case_id, CaseFile.tenant_id == current_user.tenant_id)
        .options(
            selectinload(CaseFile.gop_suggestions),
            selectinload(CaseFile.audit_logs),
        )
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Fallakte nicht gefunden")

    return CaseFileDetail(
        id=case.id,
        patient_id=case.patient_id,
        treatment_date=str(case.treatment_date),
        quartal=case.quartal,
        report_text=case.report_text,
        status=case.status.value,
        notes=case.notes,
        gop_suggestions=[_gop_to_dict(g) for g in case.gop_suggestions],
        audit_trail=[_audit_to_dict(a) for a in case.audit_logs],
    )


@router.patch("/{case_id}/close")
async def close_case(
    case_id: str,
    current_user: CurrentUser = Depends(require_scope("cases")),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(CaseFile).where(
            CaseFile.id == case_id, CaseFile.tenant_id == current_user.tenant_id
        )
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Fallakte nicht gefunden")

    case.status = CaseStatus.ABGESCHLOSSEN
    await db.commit()
    return {"status": "ok", "case_id": case_id, "new_status": "abgeschlossen"}


@router.get("/{case_id}/audit")
async def get_audit_log(
    case_id: str,
    current_user: CurrentUser = Depends(require_scope("cases")),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.case_file_id == case_id,
            AuditLog.tenant_id == current_user.tenant_id,
        )
        .order_by(AuditLog.created_at)
    )
    logs = result.scalars().all()
    return [_audit_to_dict(a) for a in logs]


def _to_summary(c: CaseFile) -> CaseFileSummary:
    from ..models.base import GOPStatus
    gops = c.gop_suggestions
    accepted = sum(1 for g in gops if g.status == GOPStatus.AKZEPTIERT)
    rejected = sum(1 for g in gops if g.status == GOPStatus.ABGELEHNT)
    patient_name = None
    if c.patient:
        patient_name = f"{c.patient.last_name}, {c.patient.first_name}"
    return CaseFileSummary(
        id=c.id,
        patient_id=c.patient_id,
        patient_name=patient_name,
        treatment_date=str(c.treatment_date),
        quartal=c.quartal,
        status=c.status.value,
        gop_count=len(gops),
        accepted_count=accepted,
        rejected_count=rejected,
        created_at=str(c.created_at),
    )


def _gop_to_dict(g: GOPSuggestion) -> dict:
    return {
        "id": g.id,
        "gop_code": g.gop_code,
        "gop_description": g.gop_description,
        "start_char": g.start_char,
        "end_char": g.end_char,
        "source_text": g.source_text,
        "confidence": g.confidence,
        "color_hex": g.color_hex,
        "status": g.status.value,
        "mcp_validated": g.mcp_validated,
        "reasoning": g.llm_reasoning,
        "created_at": str(g.created_at),
    }


def _audit_to_dict(a: AuditLog) -> dict:
    return {
        "id": a.id,
        "user_id": a.user_id,
        "action": a.action.value,
        "gop_code": a.gop_code,
        "reason": a.reason,
        "metadata": a.log_metadata,
        "created_at": str(a.created_at),
    }
