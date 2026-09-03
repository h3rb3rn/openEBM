"""Self-service profile endpoints: change password, manage 2FA (TOTP)."""
import base64
import hashlib
import io
import logging
import secrets

import pyotp
import qrcode
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import get_current_user, hash_password, verify_password, CurrentUser
from ..database import get_db
from ..models.base import User

logger = logging.getLogger(__name__)
router = APIRouter()

TOTP_ISSUER = "openEBM"
BACKUP_CODE_COUNT = 8
BACKUP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/l


def _hash_backup_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _generate_backup_codes() -> list[str]:
    return [
        "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(10))
        for _ in range(BACKUP_CODE_COUNT)
    ]


async def consume_totp_or_backup_code(user: User, code: str, db: AsyncSession) -> bool:
    """
    Verify a live TOTP code or a single-use backup code. Consumes (removes)
    the backup code on match so it cannot be replayed. Shared by the login
    2FA step and the "disable 2FA" flow.
    """
    code = code.strip().replace(" ", "")
    if user.totp_secret and pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
        return True

    hashed = _hash_backup_code(code.upper())
    remaining = user.totp_backup_codes or []
    if hashed in remaining:
        user.totp_backup_codes = [c for c in remaining if c != hashed]
        await db.commit()
        return True

    return False


async def _get_user(current_user: CurrentUser, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Benutzer nicht gefunden")
    return user


# ─── Password ─────────────────────────────────────────────────────────

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.patch("/password")
async def change_password(
    data: ChangePasswordRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(current_user, db)
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Aktuelles Passwort ist falsch")
    if len(data.new_password) < 8:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Neues Passwort muss mindestens 8 Zeichen lang sein")

    user.hashed_password = hash_password(data.new_password)
    await db.commit()
    logger.info("User %s changed their password", user.email)
    return {"status": "ok"}


# ─── Two-factor authentication ────────────────────────────────────────

class TwoFactorStatusResponse(BaseModel):
    enabled: bool


class TwoFactorSetupResponse(BaseModel):
    secret: str
    otpauth_url: str
    qr_code_png_base64: str


class TwoFactorEnableRequest(BaseModel):
    code: str


class TwoFactorEnableResponse(BaseModel):
    backup_codes: list[str]


class TwoFactorDisableRequest(BaseModel):
    password: str
    code: str


@router.get("/2fa/status", response_model=TwoFactorStatusResponse)
async def get_2fa_status(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(current_user, db)
    return TwoFactorStatusResponse(enabled=user.totp_enabled)


@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
async def setup_2fa(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start 2FA setup: generates a new secret (not yet active) and a QR code."""
    user = await _get_user(current_user, db)
    if user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="2FA ist bereits aktiviert")

    secret = pyotp.random_base32()
    user.totp_secret = secret
    await db.commit()

    otpauth_url = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=TOTP_ISSUER)

    qr_img = qrcode.make(otpauth_url)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return TwoFactorSetupResponse(secret=secret, otpauth_url=otpauth_url, qr_code_png_base64=qr_b64)


@router.post("/2fa/enable", response_model=TwoFactorEnableResponse)
async def enable_2fa(
    data: TwoFactorEnableRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm 2FA setup with a live code from the authenticator app; returns one-time backup codes."""
    user = await _get_user(current_user, db)
    if user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="2FA ist bereits aktiviert")
    if not user.totp_secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kein 2FA-Setup gestartet. Bitte zuerst QR-Code anfordern.")

    if not pyotp.TOTP(user.totp_secret).verify(data.code.strip(), valid_window=1):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültiger Code")

    backup_codes = _generate_backup_codes()
    user.totp_backup_codes = [_hash_backup_code(c) for c in backup_codes]
    user.totp_enabled = True
    await db.commit()
    logger.info("User %s enabled 2FA", user.email)

    return TwoFactorEnableResponse(backup_codes=backup_codes)


@router.post("/2fa/disable")
async def disable_2fa(
    data: TwoFactorDisableRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(current_user, db)
    if not user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="2FA ist nicht aktiviert")
    if not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Passwort ist falsch")
    if not await consume_totp_or_backup_code(user, data.code, db):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültiger Code")

    user.totp_enabled = False
    user.totp_secret = None
    user.totp_backup_codes = None
    await db.commit()
    logger.info("User %s disabled 2FA", user.email)
    return {"status": "ok"}
