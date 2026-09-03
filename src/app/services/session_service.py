"""
Session service: manages instant/transient sessions in Valkey.

GDPR compliance: instant-session data is NEVER written to PostgreSQL.
The TTL is strictly enforced; no persistent traces remain.
"""
import json
import logging
import uuid
from datetime import datetime

import redis.asyncio as aioredis

from ..config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

SESSION_PREFIX = "instant_session:"
SESSION_TTL = settings.session_ttl_seconds


def _get_valkey() -> aioredis.Redis:
    return aioredis.from_url(settings.valkey_url, decode_responses=True)


async def create_instant_session(
    tenant_id: str,
    report_text: str | None = None,
    patient_data: dict | None = None,
) -> str:
    """Create a short-lived instant session in Valkey. Returns the session UUID."""
    session_id = str(uuid.uuid4())
    key = f"{SESSION_PREFIX}{tenant_id}:{session_id}"
    payload = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "created_at": datetime.utcnow().isoformat(),
        "report_text": report_text,
        "patient_data": patient_data or {},
        "analysis_result": None,
    }
    valkey = _get_valkey()
    await valkey.setex(key, SESSION_TTL, json.dumps(payload, default=str))
    logger.info("Instant session created: %s (TTL: %ss)", session_id, SESSION_TTL)
    return session_id


async def get_instant_session(tenant_id: str, session_id: str) -> dict | None:
    key = f"{SESSION_PREFIX}{tenant_id}:{session_id}"
    valkey = _get_valkey()
    raw = await valkey.get(key)
    if raw:
        return json.loads(raw)
    return None


async def update_instant_session(
    tenant_id: str,
    session_id: str,
    analysis_result: dict,
) -> bool:
    """Store the analysis result in the instant session (no PostgreSQL write)."""
    key = f"{SESSION_PREFIX}{tenant_id}:{session_id}"
    valkey = _get_valkey()
    raw = await valkey.get(key)
    if not raw:
        return False
    session = json.loads(raw)
    session["analysis_result"] = analysis_result
    # Reset the TTL to keep an actively used session alive
    await valkey.setex(key, SESSION_TTL, json.dumps(session, default=str))
    return True


async def delete_instant_session(tenant_id: str, session_id: str) -> bool:
    key = f"{SESSION_PREFIX}{tenant_id}:{session_id}"
    valkey = _get_valkey()
    deleted = await valkey.delete(key)
    return bool(deleted)


async def get_session_ttl(tenant_id: str, session_id: str) -> int:
    """Return the remaining TTL in seconds."""
    key = f"{SESSION_PREFIX}{tenant_id}:{session_id}"
    valkey = _get_valkey()
    return await valkey.ttl(key)
