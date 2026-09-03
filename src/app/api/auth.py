"""JWT authentication, tenant isolation and API-key access for external systems."""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..models.base import User, Tenant, ApiKey
from ..services import rate_limit_service
from ..services.email_service import send_password_reset_email

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"
SESSION_COOKIE_NAME = "ebm_session"


# ─── Schemas ────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"
    tenant_id: str | None = None
    user_id: str | None = None
    role: str | None = None
    requires_2fa: bool = False
    pending_token: str | None = None


class TwoFactorVerifyRequest(BaseModel):
    pending_token: str
    code: str


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


class CurrentUser(BaseModel):
    id: str
    tenant_id: str
    email: str
    full_name: str
    role: str
    auth_type: str = "user"          # "user" (JWT) | "api_key"
    scopes: list[str] | None = None  # only relevant when auth_type == "api_key"


# ─── API-key constants ───────────────────────────────────────────────

API_KEY_PREFIX = "ebm_live_"
AVAILABLE_SCOPES = ["analysis", "interop", "patients", "cases"]


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


# ─── Helpers ────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def create_2fa_pending_token(data: dict) -> str:
    """Short-lived token issued after password verification when 2FA is
    enabled. Carries scope=2fa_pending so it is rejected by every endpoint
    that expects a full session (see _user_from_jwt) — it can only be
    redeemed at POST /auth/2fa/verify."""
    to_encode = data.copy()
    to_encode["scope"] = "2fa_pending"
    to_encode["exp"] = datetime.now(timezone.utc) + timedelta(minutes=5)
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


async def _user_from_jwt(token: str, db: AsyncSession) -> CurrentUser:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Ungültige Anmeldedaten",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        tenant_id: str = payload.get("tenant_id")
        if not user_id or not tenant_id:
            raise credentials_exc
        if payload.get("scope") == "2fa_pending":
            # Not a full session — must be redeemed via /auth/2fa/verify first.
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise credentials_exc

    return CurrentUser(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
    )


def _extract_token(request: Request) -> str | None:
    """
    Session token lookup order: httpOnly cookie (browser/web UI) first,
    then Authorization: Bearer header (non-browser / scripted clients).
    """
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        return cookie_token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:]
    return None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentifizierung erforderlich",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await _user_from_jwt(token, db)


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CurrentUser | None:
    """
    Non-raising variant for server-side page guards: returns None instead of
    a 401 so page routes can issue a redirect to /login without ever
    rendering the protected template for an unauthenticated request.
    """
    token = _extract_token(request)
    if not token:
        return None
    try:
        return await _user_from_jwt(token, db)
    except HTTPException:
        return None


async def _actor_from_api_key(raw_key: str, db: AsyncSession) -> CurrentUser:
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültiger API-Key")

    key_hash = hash_api_key(raw_key)
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()

    if not api_key or not api_key.is_active or api_key.revoked_at is not None:
        raise unauthorized
    if api_key.expires_at and api_key.expires_at < datetime.utcnow():
        raise unauthorized

    api_key.last_used_at = datetime.utcnow()
    await db.commit()

    return CurrentUser(
        id=api_key.id,
        tenant_id=api_key.tenant_id,
        email="",
        full_name=f"API-Key: {api_key.name}",
        role="api",
        auth_type="api_key",
        scopes=api_key.scopes or [],
    )


async def get_current_actor(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """
    Authentication for external integrations: accepts the session cookie or
    an Authorization Bearer token (web UI) or an X-API-Key header (external
    programs).
    """
    if x_api_key:
        return await _actor_from_api_key(x_api_key, db)

    token = _extract_token(request)
    if token:
        return await _user_from_jwt(token, db)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentifizierung erforderlich (Bearer-Token oder X-API-Key).",
    )


async def set_tenant_context(db: AsyncSession, tenant_id: str) -> None:
    """
    Sets the Postgres session variable the row-level-security policies key
    on (see alembic/versions/..._enable_row_level_security.py).

    SET LOCAL is transaction-scoped — it auto-resets on commit/rollback and
    can never leak into a different request that later reuses the same
    pooled connection (a real risk with a non-LOCAL SET). The tradeoff:
    if an endpoint calls db.commit() and then issues more RLS-relevant
    queries afterward (e.g. db.refresh() re-reading the just-committed
    row), that runs in a *new* transaction and needs this called again —
    see the call sites in patients.py/analysis.py for the pattern.
    """
    # SET/SET LOCAL don't accept bind parameters at the wire protocol level
    # (Postgres requires a literal there) — set_config() is the parameterized
    # equivalent; its third argument (true) makes it transaction-local, same
    # as SET LOCAL.
    await db.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})


async def get_tenant_db(
    actor: CurrentUser = Depends(get_current_actor),
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    """
    Drop-in replacement for `Depends(get_db)` in routers that query
    tenant-scoped tables (patients, case_files, gop_suggestions,
    audit_logs) — sets the RLS tenant context on the session before
    returning it. This is a defense-in-depth backstop; existing explicit
    `WHERE tenant_id = ...` filtering in these routers is unchanged and
    remains the primary mechanism.
    """
    await set_tenant_context(db, actor.tenant_id)
    return db


def require_scope(scope: str):
    """
    Dependency factory enforcing a scope for API-key actors only.
    JWT users are already authorized through their role.
    """
    async def _checker(actor: CurrentUser = Depends(get_current_actor)) -> CurrentUser:
        if actor.auth_type == "api_key" and scope not in (actor.scopes or []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API-Key hat keinen Zugriff auf Bereich '{scope}'.",
            )
        return actor
    return _checker


# ─── Endpoints ──────────────────────────────────────────────────────

@router.post("/token", response_model=TokenResponse)
async def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    ip_key = f"ip:{rate_limit_service.client_ip(request)}"
    account_key = f"account:{form_data.username.lower()}"

    if await rate_limit_service.is_blocked(ip_key, rate_limit_service.MAX_IP_ATTEMPTS):
        retry_after = await rate_limit_service.retry_after_seconds(ip_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Zu viele Anmeldeversuche von dieser Adresse. Bitte später erneut versuchen.",
            headers={"Retry-After": str(retry_after)},
        )
    if await rate_limit_service.is_blocked(account_key, rate_limit_service.MAX_ACCOUNT_ATTEMPTS):
        retry_after = await rate_limit_service.retry_after_seconds(account_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Konto vorübergehend gesperrt nach zu vielen Fehlversuchen. Bitte später erneut versuchen.",
            headers={"Retry-After": str(retry_after)},
        )

    result = await db.execute(
        select(User).where(User.email == form_data.username, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(form_data.password, user.hashed_password):
        await rate_limit_service.record_failure(ip_key)
        await rate_limit_service.record_failure(account_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-Mail oder Passwort falsch",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await rate_limit_service.reset(account_key)

    if user.totp_enabled:
        pending_token = create_2fa_pending_token({"sub": user.id, "tenant_id": user.tenant_id})
        return TokenResponse(requires_2fa=True, pending_token=pending_token)

    user.last_login = datetime.utcnow()
    await db.commit()
    return _issue_session(response, user)


@router.post("/2fa/verify", response_model=TokenResponse)
async def verify_2fa_login(
    request: Request,
    body: TwoFactorVerifyRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Second step of a 2FA-protected login: redeems the pending_token from
    /auth/token plus a live TOTP or backup code for a full session."""
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültiger oder abgelaufener Code")

    ip_key = f"ip:{rate_limit_service.client_ip(request)}"
    if await rate_limit_service.is_blocked(ip_key, rate_limit_service.MAX_IP_ATTEMPTS):
        retry_after = await rate_limit_service.retry_after_seconds(ip_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Zu viele Anmeldeversuche von dieser Adresse. Bitte später erneut versuchen.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        payload = jwt.decode(body.pending_token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        raise unauthorized
    if payload.get("scope") != "2fa_pending":
        raise unauthorized

    result = await db.execute(
        select(User).where(
            User.id == payload.get("sub"),
            User.tenant_id == payload.get("tenant_id"),
            User.is_active == True,
        )
    )
    user = result.scalar_one_or_none()
    if not user or not user.totp_enabled:
        raise unauthorized

    account_key = f"2fa:{user.id}"
    if await rate_limit_service.is_blocked(account_key, rate_limit_service.MAX_2FA_ATTEMPTS):
        retry_after = await rate_limit_service.retry_after_seconds(account_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Zu viele Fehlversuche. Bitte später erneut versuchen.",
            headers={"Retry-After": str(retry_after)},
        )

    from .profile import consume_totp_or_backup_code  # local import avoids a circular import at module load
    if not await consume_totp_or_backup_code(user, body.code, db):
        await rate_limit_service.record_failure(ip_key)
        await rate_limit_service.record_failure(account_key)
        raise unauthorized

    await rate_limit_service.reset(account_key)
    user.last_login = datetime.utcnow()
    await db.commit()
    return _issue_session(response, user)


def _issue_session(response: Response, user: User) -> TokenResponse:
    token = create_access_token({"sub": user.id, "tenant_id": user.tenant_id})

    # httpOnly session cookie for the web UI — never touched by JavaScript,
    # so a page can no longer be rendered client-side before the server has
    # verified authentication (see get_current_user_optional page guards).
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
    )

    return TokenResponse(
        access_token=token,
        tenant_id=user.tenant_id,
        user_id=user.id,
        role=user.role.value,
    )


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"status": "ok"}


# ─── Password reset ─────────────────────────────────────────────────

_RESET_TOKEN_PREFIX = "password_reset:"
_RESET_TOKEN_TTL = 3600  # 1 hour


def _get_valkey() -> aioredis.Redis:
    return aioredis.from_url(settings.valkey_url, decode_responses=True)


@router.post("/password-reset/request")
async def request_password_reset(
    request: Request,
    body: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Always returns the same generic response regardless of whether the
    email belongs to an account — a differing response would let an
    attacker enumerate registered email addresses.
    """
    generic_response = {
        "status": "ok",
        "message": "Falls ein Konto mit dieser E-Mail existiert, wurde eine E-Mail mit einem Link zum Zurücksetzen versendet.",
    }

    ip_key = f"ip:{rate_limit_service.client_ip(request)}"
    request_key = f"reset_request:{body.email.lower()}"
    if await rate_limit_service.is_blocked(ip_key, rate_limit_service.MAX_IP_ATTEMPTS) or \
       await rate_limit_service.is_blocked(request_key, 3):
        # Still return the generic response — a 429 here would itself leak
        # information (confirms the email triggered rate limiting).
        return generic_response
    await rate_limit_service.record_failure(request_key, window=_RESET_TOKEN_TTL)

    result = await db.execute(select(User).where(User.email == body.email, User.is_active == True))
    user = result.scalar_one_or_none()
    if user:
        token = secrets.token_urlsafe(32)
        valkey = _get_valkey()
        await valkey.setex(f"{_RESET_TOKEN_PREFIX}{token}", _RESET_TOKEN_TTL, user.id)
        reset_url = f"{settings.public_base_url}/login?reset_token={token}"
        send_password_reset_email(user.email, reset_url)
        logger.info("Password reset requested for user %s", user.id)

    return generic_response


@router.post("/password-reset/confirm")
async def confirm_password_reset(
    body: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db),
):
    if len(body.new_password) < 8:
        raise HTTPException(status_code=422, detail="Passwort muss mindestens 8 Zeichen lang sein.")

    valkey = _get_valkey()
    token_key = f"{_RESET_TOKEN_PREFIX}{body.token}"
    user_id = await valkey.get(token_key)
    if not user_id:
        raise HTTPException(status_code=400, detail="Link ist ungültig oder abgelaufen. Bitte erneut anfordern.")

    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="Link ist ungültig oder abgelaufen. Bitte erneut anfordern.")

    user.hashed_password = hash_password(body.new_password)
    await db.commit()
    await valkey.delete(token_key)  # single-use
    await rate_limit_service.reset(f"account:{user.email.lower()}")
    logger.info("Password reset completed for user %s", user.id)

    return {"status": "ok", "message": "Passwort wurde geändert. Sie können sich jetzt anmelden."}


@router.get("/me", response_model=CurrentUser)
async def get_me(current_user: CurrentUser = Depends(get_current_user)):
    return current_user
