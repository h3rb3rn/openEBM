"""Admin API: user management, metrics, catalog import and API keys (ADMIN role only, tenant-scoped)."""
import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import (
    get_current_user, get_tenant_db, CurrentUser, hash_password,
    hash_api_key, API_KEY_PREFIX, AVAILABLE_SCOPES,
)
from ..database import get_db
from ..models.base import User, UserRole, AuditLog, AuditAction, ApiKey
from ..services import import_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Guard: ADMIN role only ──────────────────────────────────────────

def require_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if current_user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nur Administratoren haben Zugriff.")
    return current_user


# ─── Schemas ─────────────────────────────────────────────────────────

class UserCreateRequest(BaseModel):
    email: str
    full_name: str
    password: str
    role: str = "arzt"


class UserUpdateRequest(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    new_password: str | None = None


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: str
    last_login: str | None


# ─── Endpoints ───────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserResponse])
async def list_users(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User)
        .where(User.tenant_id == admin.tenant_id)
        .order_by(User.created_at.desc())
    )
    return [_to_response(u) for u in result.scalars().all()]


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    data: UserCreateRequest,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Reject duplicate email within the tenant
    existing = await db.execute(
        select(User).where(User.email == data.email, User.tenant_id == admin.tenant_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"E-Mail '{data.email}' existiert bereits.")

    try:
        role = UserRole(data.role)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Ungültige Rolle: {data.role}")

    user = User(
        tenant_id=admin.tenant_id,
        email=data.email,
        full_name=data.full_name,
        hashed_password=hash_password(data.password),
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("Admin %s created user %s (%s)", admin.email, user.email, role.value)
    return _to_response(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    data: UserUpdateRequest,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user_or_404(user_id, admin.tenant_id, db)

    # Admins must not lock themselves out
    if user.id == admin.id and data.role and data.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=400, detail="Sie können sich selbst nicht die Admin-Rolle entziehen.")
    if user.id == admin.id and data.is_active is False:
        raise HTTPException(status_code=400, detail="Sie können sich selbst nicht deaktivieren.")

    if data.full_name is not None:
        user.full_name = data.full_name
    if data.role is not None:
        try:
            user.role = UserRole(data.role)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Ungültige Rolle: {data.role}")
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.new_password:
        user.hashed_password = hash_password(data.new_password)

    await db.commit()
    await db.refresh(user)
    logger.info("Admin %s updated user %s", admin.email, user.email)
    return _to_response(user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user_or_404(user_id, admin.tenant_id, db)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Sie können sich selbst nicht löschen.")
    try:
        await db.delete(user)
        await db.commit()
    except IntegrityError:
        # Users referenced by audit-log entries cannot be hard-deleted
        # (append-only compliance trail). Deactivation is the supported path.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Benutzer hat Audit-Log-Einträge und kann nicht gelöscht werden. "
                "Bitte stattdessen deaktivieren."
            ),
        )
    logger.info("Admin %s deleted user %s", admin.email, user.email)


@router.get("/stats")
async def admin_stats(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Aggregate statistics for the admin dashboard."""
    from ..models.base import Patient, CaseFile, GOPSuggestion, GOPStatus

    user_count = await db.scalar(
        select(func.count()).where(User.tenant_id == admin.tenant_id, User.is_active == True)
    )
    patient_count = await db.scalar(
        select(func.count()).where(Patient.tenant_id == admin.tenant_id, Patient.is_active == True)
    )
    case_count = await db.scalar(
        select(func.count()).where(CaseFile.tenant_id == admin.tenant_id)
    )
    accepted_gops = await db.scalar(
        select(func.count(GOPSuggestion.id))
        .join(CaseFile, CaseFile.id == GOPSuggestion.case_file_id)
        .where(CaseFile.tenant_id == admin.tenant_id, GOPSuggestion.status == GOPStatus.AKZEPTIERT)
    )
    # Role distribution
    role_rows = await db.execute(
        select(User.role, func.count().label("n"))
        .where(User.tenant_id == admin.tenant_id, User.is_active == True)
        .group_by(User.role)
    )
    roles = {r.role.value: r.n for r in role_rows}

    return {
        "active_users": user_count,
        "patients": patient_count,
        "case_files": case_count,
        "accepted_gops": accepted_gops,
        "role_distribution": roles,
    }


@router.get("/metrics/quality")
async def suggestion_quality_metrics(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Aggregates the accept/reject/MCP-flag history already recorded on every
    GOPSuggestion into quality signals — no model change, no new storage,
    just surfacing data that was tracked from day one for the audit log.

    Deliberately does NOT feed anything back into the LLM or retrieval:
    the catalog (and therefore GOP semantics) changes every quarter, so
    anything "learned" about a code's meaning goes stale within months.
    This is read-only analytics for a human to review, matching the
    split-brain design (probabilistic suggestion, deterministic check,
    human decides).
    """
    from ..models.base import CaseFile, GOPSuggestion, GOPStatus

    tenant_filter = CaseFile.tenant_id == admin.tenant_id

    # ─── Overall accept/reject/pending counts ──────────────────────────
    status_rows = await db.execute(
        select(GOPSuggestion.status, func.count())
        .join(CaseFile, CaseFile.id == GOPSuggestion.case_file_id)
        .where(tenant_filter)
        .group_by(GOPSuggestion.status)
    )
    status_counts = {row[0].value: row[1] for row in status_rows}
    accepted = status_counts.get(GOPStatus.AKZEPTIERT.value, 0)
    rejected = status_counts.get(GOPStatus.ABGELEHNT.value, 0)
    pending = status_counts.get(GOPStatus.VORGESCHLAGEN.value, 0)
    reviewed = accepted + rejected
    overall = {
        "total": accepted + rejected + pending,
        "accepted": accepted,
        "rejected": rejected,
        "pending": pending,
        "acceptance_rate": round(accepted / reviewed, 3) if reviewed else None,
    }

    # ─── Acceptance rate by quarter (catalog-version-aware) ────────────
    quarter_rows = await db.execute(
        select(CaseFile.quartal, GOPSuggestion.status, func.count())
        .join(CaseFile, CaseFile.id == GOPSuggestion.case_file_id)
        .where(tenant_filter)
        .group_by(CaseFile.quartal, GOPSuggestion.status)
    )
    by_quarter: dict[str, dict[str, int]] = {}
    for quartal, status, n in quarter_rows:
        by_quarter.setdefault(quartal, {"accepted": 0, "rejected": 0, "pending": 0})
        by_quarter[quartal][
            {"akzeptiert": "accepted", "abgelehnt": "rejected", "vorgeschlagen": "pending"}[status.value]
        ] = n
    quarters = [
        {
            "quartal": q,
            **counts,
            "acceptance_rate": round(counts["accepted"] / (counts["accepted"] + counts["rejected"]), 3)
            if (counts["accepted"] + counts["rejected"]) else None,
        }
        for q, counts in sorted(by_quarter.items())
    ]

    # ─── Per-GOP acceptance rate, ranked by suggestion volume ──────────
    gop_rows = await db.execute(
        select(GOPSuggestion.gop_code, GOPSuggestion.status, func.count())
        .join(CaseFile, CaseFile.id == GOPSuggestion.case_file_id)
        .where(tenant_filter)
        .group_by(GOPSuggestion.gop_code, GOPSuggestion.status)
    )
    by_gop: dict[str, dict[str, int]] = {}
    for code, status, n in gop_rows:
        by_gop.setdefault(code, {"accepted": 0, "rejected": 0, "pending": 0})
        by_gop[code][
            {"akzeptiert": "accepted", "abgelehnt": "rejected", "vorgeschlagen": "pending"}[status.value]
        ] = n
    top_gops = sorted(
        (
            {
                "gop_code": code,
                "total": sum(counts.values()),
                **counts,
                "acceptance_rate": round(counts["accepted"] / (counts["accepted"] + counts["rejected"]), 3)
                if (counts["accepted"] + counts["rejected"]) else None,
            }
            for code, counts in by_gop.items()
        ),
        key=lambda r: r["total"],
        reverse=True,
    )[:15]

    # ─── MCP conflicts: suggestions the deterministic validator flagged,
    #     broken down by what the human ultimately decided. "accepted"
    #     here is the compliance-relevant number — a human overrode a
    #     rule-based exclusion/validation flag. ───────────────────────
    mcp_rows = await db.execute(
        select(GOPSuggestion.status, func.count())
        .join(CaseFile, CaseFile.id == GOPSuggestion.case_file_id)
        .where(tenant_filter, GOPSuggestion.mcp_validated == False)  # noqa: E712
        .group_by(GOPSuggestion.status)
    )
    mcp_counts = {row[0].value: row[1] for row in mcp_rows}
    mcp_conflicts = {
        "total_flagged": sum(mcp_counts.values()),
        "accepted_despite_flag": mcp_counts.get(GOPStatus.AKZEPTIERT.value, 0),
        "rejected_after_flag": mcp_counts.get(GOPStatus.ABGELEHNT.value, 0),
        "pending": mcp_counts.get(GOPStatus.VORGESCHLAGEN.value, 0),
    }

    # ─── Confidence calibration: does a higher LLM confidence score
    #     actually correlate with human acceptance? ────────────────────
    conf_rows = await db.execute(
        select(GOPSuggestion.status, func.avg(GOPSuggestion.confidence))
        .join(CaseFile, CaseFile.id == GOPSuggestion.case_file_id)
        .where(tenant_filter, GOPSuggestion.status != GOPStatus.VORGESCHLAGEN)
        .group_by(GOPSuggestion.status)
    )
    conf_avgs = {row[0].value: round(row[1], 3) if row[1] is not None else None for row in conf_rows}
    confidence_calibration = {
        "avg_confidence_accepted": conf_avgs.get(GOPStatus.AKZEPTIERT.value),
        "avg_confidence_rejected": conf_avgs.get(GOPStatus.ABGELEHNT.value),
    }

    return {
        "overall": overall,
        "by_quarter": quarters,
        "top_gops": top_gops,
        "mcp_conflicts": mcp_conflicts,
        "confidence_calibration": confidence_calibration,
    }


# ─── Catalog import ──────────────────────────────────────────────────

@router.get("/import/status")
async def import_status(admin: CurrentUser = Depends(require_admin)):
    """Return catalog file info, database status and the last import run."""
    catalog_info = import_service.get_catalog_info()
    db_stats     = await import_service.get_db_stats()
    run_status   = await import_service.get_import_status()
    return {
        "catalog_file": catalog_info,
        "database":     db_stats,
        "last_run":     run_status,
    }


@router.post("/import/trigger")
async def trigger_import(admin: CurrentUser = Depends(require_admin)):
    """Start the local-file ingestion pipeline as a background task."""
    started = await import_service.start_import_background()
    if not started:
        raise HTTPException(status_code=409, detail="Import läuft bereits.")
    return {"status": "gestartet", "message": "Import wurde im Hintergrund gestartet."}


class KbvSourceRequest(BaseModel):
    url: str


@router.get("/import/kbv-source")
async def get_kbv_source(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return the currently configured KBV catalog PDF URL (admin-editable, not hard-coded)."""
    url = await import_service.get_kbv_source_url(db)
    return {"url": url, "default_url": import_service.DEFAULT_KBV_SOURCE_URL}


@router.put("/import/kbv-source")
async def set_kbv_source(
    data: KbvSourceRequest,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if not data.url.lower().startswith("https://"):
        raise HTTPException(status_code=422, detail="URL muss mit https:// beginnen.")
    await import_service.set_kbv_source_url(db, data.url, admin.id)
    logger.info("Admin %s set KBV catalog source URL to %s", admin.email, data.url)
    return {"status": "ok", "url": data.url}


@router.post("/import/kbv-fetch")
async def trigger_kbv_fetch(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Download and parse the KBV catalog PDF from the configured source URL.
    Does NOT write to Neo4j/ChromaDB — produces a preview for review first,
    since the PDF parser is a heuristic best-effort extraction against an
    unstructured legal document (see kbv_import.py for details).
    """
    url = await import_service.get_kbv_source_url(db)
    started = await import_service.start_kbv_fetch_background(url)
    if not started:
        raise HTTPException(status_code=409, detail="Import läuft bereits.")
    return {"status": "gestartet", "message": "PDF-Download und Analyse gestartet.", "source_url": url}


@router.post("/import/kbv-commit/{run_id}")
async def commit_kbv_import(run_id: str, admin: CurrentUser = Depends(require_admin)):
    """Write a previously parsed-and-reviewed KBV preview into Neo4j/ChromaDB."""
    started = await import_service.start_kbv_commit_background(run_id)
    if not started:
        raise HTTPException(status_code=409, detail="Vorschau nicht gefunden, abgelaufen, oder ein Import läuft bereits.")
    logger.info("Admin %s committed KBV import run %s", admin.email, run_id)
    return {"status": "gestartet", "message": "Übernahme in Neo4j/ChromaDB gestartet."}


# ─── API keys for external integrations ─────────────────────────────

class ApiKeyCreateRequest(BaseModel):
    name: str
    scopes: list[str] = AVAILABLE_SCOPES
    expires_in_days: int | None = None  # None = never expires


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    is_active: bool
    created_at: str
    last_used_at: str | None
    expires_at: str | None


class ApiKeyCreatedResponse(ApiKeyResponse):
    api_key: str  # plaintext key, returned exactly once at creation time


@router.get("/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.tenant_id == admin.tenant_id)
        .order_by(ApiKey.created_at.desc())
    )
    return [_apikey_to_response(k) for k in result.scalars().all()]


@router.post("/api-keys", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    data: ApiKeyCreateRequest,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    invalid_scopes = set(data.scopes) - set(AVAILABLE_SCOPES)
    if invalid_scopes:
        raise HTTPException(status_code=422, detail=f"Ungültige Scopes: {', '.join(invalid_scopes)}")
    if not data.scopes:
        raise HTTPException(status_code=422, detail="Mindestens ein Scope erforderlich.")

    raw_key = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    expires_at = None
    if data.expires_in_days:
        from datetime import timedelta
        expires_at = datetime.utcnow() + timedelta(days=data.expires_in_days)

    api_key = ApiKey(
        tenant_id=admin.tenant_id,
        name=data.name,
        key_prefix=raw_key[:len(API_KEY_PREFIX) + 8],
        key_hash=hash_api_key(raw_key),
        scopes=data.scopes,
        created_by_id=admin.id,
        expires_at=expires_at,
        is_active=True,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    logger.info("Admin %s created API key '%s' (scopes: %s)", admin.email, data.name, data.scopes)

    resp = _apikey_to_response(api_key).model_dump()
    resp["api_key"] = raw_key
    return ApiKeyCreatedResponse(**resp)


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == admin.tenant_id)
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=404, detail="API-Key nicht gefunden.")

    api_key.is_active = False
    api_key.revoked_at = datetime.utcnow()
    await db.commit()
    logger.info("Admin %s revoked API key '%s'", admin.email, api_key.name)


def _apikey_to_response(k: ApiKey) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=k.id,
        name=k.name,
        key_prefix=k.key_prefix,
        scopes=k.scopes or [],
        is_active=k.is_active,
        created_at=str(k.created_at)[:16],
        last_used_at=str(k.last_used_at)[:16] if k.last_used_at else None,
        expires_at=str(k.expires_at)[:16] if k.expires_at else None,
    )


# ─── Helpers ─────────────────────────────────────────────────────────

async def _get_user_or_404(user_id: str, tenant_id: str, db: AsyncSession) -> User:
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden.")
    return user


def _to_response(u: User) -> UserResponse:
    return UserResponse(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        role=u.role.value,
        is_active=u.is_active,
        created_at=str(u.created_at)[:16],
        last_login=str(u.last_login)[:16] if u.last_login else None,
    )
