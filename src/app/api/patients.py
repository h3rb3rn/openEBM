"""Patient CRUD endpoints with strict tenant isolation."""
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import require_scope, get_tenant_db, set_tenant_context, CurrentUser
from ..models.base import Patient, InsuranceType, Gender

logger = logging.getLogger(__name__)
router = APIRouter()


class PatientCreate(BaseModel):
    patient_number: str
    first_name: str
    last_name: str
    date_of_birth: date
    gender: str
    insurance_type: str
    insurance_id: str | None = None
    insurance_company: str | None = None


class PatientResponse(BaseModel):
    id: str
    patient_number: str
    first_name: str
    last_name: str
    date_of_birth: date
    gender: str
    insurance_type: str
    insurance_id: str | None
    insurance_company: str | None
    is_active: bool


@router.get("/", response_model=list[PatientResponse])
async def list_patients(
    current_user: CurrentUser = Depends(require_scope("patients")),
    db: AsyncSession = Depends(get_tenant_db),
    search: str | None = None,
):
    query = select(Patient).where(
        Patient.tenant_id == current_user.tenant_id,
        Patient.is_active == True,
    )
    if search:
        query = query.where(
            (Patient.last_name.ilike(f"%{search}%")) |
            (Patient.first_name.ilike(f"%{search}%")) |
            (Patient.patient_number.ilike(f"%{search}%"))
        )
    result = await db.execute(query.order_by(Patient.last_name))
    patients = result.scalars().all()
    return [_to_response(p) for p in patients]


@router.post("/", response_model=PatientResponse, status_code=201)
async def create_patient(
    data: PatientCreate,
    current_user: CurrentUser = Depends(require_scope("patients")),
    db: AsyncSession = Depends(get_tenant_db),
):
    patient = Patient(
        tenant_id=current_user.tenant_id,
        patient_number=data.patient_number,
        first_name=data.first_name,
        last_name=data.last_name,
        date_of_birth=data.date_of_birth,
        gender=Gender(data.gender),
        insurance_type=InsuranceType(data.insurance_type),
        insurance_id=data.insurance_id,
        insurance_company=data.insurance_company,
    )
    db.add(patient)
    await db.commit()
    # SET LOCAL from get_tenant_db doesn't survive the commit above (new
    # transaction) — re-apply before this refresh SELECT, or RLS blocks it.
    await set_tenant_context(db, current_user.tenant_id)
    await db.refresh(patient)
    return _to_response(patient)


@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(
    patient_id: str,
    current_user: CurrentUser = Depends(require_scope("patients")),
    db: AsyncSession = Depends(get_tenant_db),
):
    result = await db.execute(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.tenant_id == current_user.tenant_id,
        )
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient nicht gefunden")
    return _to_response(patient)


def _to_response(p: Patient) -> PatientResponse:
    return PatientResponse(
        id=p.id,
        patient_number=p.patient_number,
        first_name=p.first_name,
        last_name=p.last_name,
        date_of_birth=p.date_of_birth,
        gender=p.gender.value,
        insurance_type=p.insurance_type.value,
        insurance_id=p.insurance_id,
        insurance_company=p.insurance_company,
        is_active=p.is_active,
    )
