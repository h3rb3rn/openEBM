"""
Brute-force protection for login and 2FA, backed by Valkey (same pattern
as session_service.py / import_service.py).

Two independent counters guard each attempt:
  - per-account: stops credential stuffing against one specific user
  - per-IP:      stops one source hammering many different accounts

A fixed-window counter (INCR + EXPIRE on first increment) is used rather
than a true sliding window — simpler, and "at most max_attempts per
window, then a fixed cooldown" is precise enough for this purpose.
"""
import redis.asyncio as aioredis

from ..config import get_settings

settings = get_settings()

WINDOW_SECONDS = 900  # 15 minutes
MAX_ACCOUNT_ATTEMPTS = 5
MAX_IP_ATTEMPTS = 20
MAX_2FA_ATTEMPTS = 8

_PREFIX = "rate_limit:"


def _get_valkey() -> aioredis.Redis:
    return aioredis.from_url(settings.valkey_url, decode_responses=True)


async def is_blocked(key: str, max_attempts: int) -> bool:
    valkey = _get_valkey()
    count = await valkey.get(f"{_PREFIX}{key}")
    return count is not None and int(count) >= max_attempts


async def record_failure(key: str, window: int = WINDOW_SECONDS) -> int:
    valkey = _get_valkey()
    full_key = f"{_PREFIX}{key}"
    count = await valkey.incr(full_key)
    if count == 1:
        await valkey.expire(full_key, window)
    return count


async def reset(key: str) -> None:
    valkey = _get_valkey()
    await valkey.delete(f"{_PREFIX}{key}")


async def retry_after_seconds(key: str) -> int:
    valkey = _get_valkey()
    ttl = await valkey.ttl(f"{_PREFIX}{key}")
    return max(ttl, 0)


def client_ip(request) -> str:
    return request.client.host if request.client else "unknown"
