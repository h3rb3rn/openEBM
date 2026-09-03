"""
All SQLAlchemy models in one file so create_all() needs a single import.

Multi-tenancy: every table carries a tenant_id and is filtered at application
level (no PostgreSQL row-level security).

Note: domain enum VALUES stay German ('offen', 'akzeptiert', ...) because they
are persisted in the database and part of the API contract; the frontend
translates them for display via the i18n layer.
"""
import uuid
import enum
from datetime import datetime, date

from sqlalchemy import (
    String, Text, Boolean, DateTime, Date, Enum as SAEnum,
    ForeignKey, Integer, Float, JSON, UniqueConstraint, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from ..database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


# ─────────────────────────── Enums ──────────────────────────────────

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    ARZT = "arzt"
    MFA = "mfa"
    READONLY = "readonly"


class InsuranceType(str, enum.Enum):
    GKV = "GKV"
    PKV = "PKV"
    SELBSTZAHLER = "SELBSTZAHLER"
    BG = "BG"


class Gender(str, enum.Enum):
    MAENNLICH = "m"
    WEIBLICH = "w"
    DIVERS = "d"


class CaseStatus(str, enum.Enum):
    OFFEN = "offen"
    IN_BEARBEITUNG = "in_bearbeitung"
    ABGESCHLOSSEN = "abgeschlossen"
    ARCHIVIERT = "archiviert"


class GOPStatus(str, enum.Enum):
    VORGESCHLAGEN = "vorgeschlagen"
    AKZEPTIERT = "akzeptiert"
    ABGELEHNT = "abgelehnt"


class AuditAction(str, enum.Enum):
    GOP_AKZEPTIERT = "gop_akzeptiert"
    GOP_ABGELEHNT = "gop_abgelehnt"
    GOP_VORGESCHLAGEN = "gop_vorgeschlagen"
    FALLAKTE_ERSTELLT = "fallakte_erstellt"
    FALLAKTE_ABGESCHLOSSEN = "fallakte_abgeschlossen"
    ANALYSE_GESTARTET = "analyse_gestartet"


# ─────────────────────────── Tenant ─────────────────────────────────

class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    users: Mapped[list["User"]] = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    patients: Mapped[list["Patient"]] = relationship("Patient", back_populates="tenant", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Tenant {self.slug}>"


# ─────────────────────────── User ───────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.ARZT, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Two-factor authentication (TOTP, RFC 6238). totp_secret is populated as
    # soon as setup starts but only takes effect once totp_enabled is True
    # (confirmed with a valid code). Backup codes are stored as SHA-256
    # hashes, single-use, consumed on verification.
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_backup_codes: Mapped[list | None] = mapped_column(JSON, nullable=True)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="users")
    audit_logs: Mapped[list["AuditLog"]] = relationship("AuditLog", back_populates="user")

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),
    )


# ─────────────────────────── Patient ────────────────────────────────

class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    patient_number: Mapped[str] = mapped_column(String(50), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[Gender] = mapped_column(SAEnum(Gender), nullable=False)
    insurance_type: Mapped[InsuranceType] = mapped_column(SAEnum(InsuranceType), nullable=False)
    insurance_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    insurance_company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="patients")
    case_files: Mapped[list["CaseFile"]] = relationship("CaseFile", back_populates="patient", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tenant_id", "patient_number", name="uq_patient_tenant_number"),
        Index("ix_patient_tenant", "tenant_id"),
    )


# ─────────────────────────── CaseFile ───────────────────────────────

class CaseFile(Base):
    __tablename__ = "case_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    patient_id: Mapped[str] = mapped_column(String(36), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    treatment_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_text: Mapped[str] = mapped_column(Text, nullable=False)
    report_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[CaseStatus] = mapped_column(SAEnum(CaseStatus), default=CaseStatus.OFFEN, nullable=False)
    quartal: Mapped[str] = mapped_column(String(6), nullable=False)  # z.B. "2024Q1"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    patient: Mapped["Patient"] = relationship("Patient", back_populates="case_files")
    gop_suggestions: Mapped[list["GOPSuggestion"]] = relationship(
        "GOPSuggestion", back_populates="case_file", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship("AuditLog", back_populates="case_file")

    __table_args__ = (
        Index("ix_casefile_tenant_patient", "tenant_id", "patient_id"),
        Index("ix_casefile_quartal", "tenant_id", "quartal"),
    )


# ─────────────────────────── GOPSuggestion ──────────────────────────

class GOPSuggestion(Base):
    """A single suggested EBM GOP per case file, including its review status."""
    __tablename__ = "gop_suggestions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    case_file_id: Mapped[str] = mapped_column(String(36), ForeignKey("case_files.id", ondelete="CASCADE"), nullable=False)
    gop_code: Mapped[str] = mapped_column(String(10), nullable=False)
    gop_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    color_hex: Mapped[str] = mapped_column(String(7), default="#FEF3C7", nullable=False)
    status: Mapped[GOPStatus] = mapped_column(SAEnum(GOPStatus), default=GOPStatus.VORGESCHLAGEN, nullable=False)
    mcp_validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mcp_exclusion_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    llm_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    case_file: Mapped["CaseFile"] = relationship("CaseFile", back_populates="gop_suggestions")

    __table_args__ = (
        Index("ix_gop_case_file", "case_file_id"),
        Index("ix_gop_code_case", "gop_code", "case_file_id"),
    )


# ─────────────────────────── AuditLog ───────────────────────────────

class AuditLog(Base):
    """
    Immutable human-in-the-loop audit log.
    Every manual GOP decision is recorded here permanently.
    Satisfies § 203 StGB / GDPR accountability requirements.
    """
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    case_file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("case_files.id"), nullable=True)
    gop_suggestion_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("gop_suggestions.id"), nullable=True)
    action: Mapped[AuditAction] = mapped_column(SAEnum(AuditAction), nullable=False)
    gop_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["User | None"] = relationship("User", back_populates="audit_logs")
    case_file: Mapped["CaseFile | None"] = relationship("CaseFile", back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_case_file", "case_file_id"),
    )


# ─────────────────────────── ApiKey ─────────────────────────────────

class ApiKey(Base):
    """
    API key for external programs (CRM, PVS, AI agents).
    The plaintext key is shown exactly once at creation time;
    only its SHA-256 hash is stored.
    """
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_apikey_hash", "key_hash"),
        Index("ix_apikey_tenant", "tenant_id"),
    )


# ─────────────────────────── SystemSetting ───────────────────────────

class SystemSetting(Base):
    """
    Global (non-tenant-scoped) operational key/value settings that admins
    can change at runtime without a redeploy — e.g. the EBM catalog source
    URL. Not for secrets (those stay in environment variables).
    """
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    updated_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
