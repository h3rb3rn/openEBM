"""
MCP Tool: ebm_validator_exclusions

Deterministic mutual-exclusion check — no LLM involved.

Rules (§ 1 Abs. 3 EBM):
  - Mutually exclusive GOPs cannot be billed together in the same treatment quarter.
  - Results are cached in Valkey (TTL 24 h) for fast repeated queries.
"""
import json
import logging
from datetime import date, datetime

import redis.asyncio as aioredis

from ..neo4j_client import get_exclusions_for_codes
from ..config import get_mcp_settings

logger = logging.getLogger(__name__)
settings = get_mcp_settings()

_valkey: aioredis.Redis | None = None


def _get_valkey() -> aioredis.Redis:
    global _valkey
    if _valkey is None:
        _valkey = aioredis.from_url(settings.valkey_url, decode_responses=True)
    return _valkey


def _quartal_from_date(d: date) -> str:
    q = (d.month - 1) // 3 + 1
    return f"{d.year}Q{q}"


async def check_exclusions(
    gop_codes: list[str],
    treatment_date_str: str,
    patient_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    """
    Check mutual exclusions between the proposed GOPs.

    Args:
        gop_codes:          List of GOP codes to validate.
        treatment_date_str: Treatment date ISO-8601 (YYYY-MM-DD).
        patient_id:         Optional — used for quarter-context lookup in cache.
        session_id:         Optional — transient session ID (instant mode).

    Returns:
        {
          "allowed": ["01435", "01600"],
          "banned": [{"code": "01100", "reason": "Mutual exclusion with 01101", "excluded_by": "01101"}],
          "matrix": {"01435": [], "01100": ["01101"]},
          "quartal": "2024Q1"
        }
    """
    try:
        treatment_date = date.fromisoformat(treatment_date_str)
    except ValueError:
        return {"error": f"Ungültiges Datum: {treatment_date_str}"}

    quartal = _quartal_from_date(treatment_date)
    cache_key = f"excl:{quartal}:{':'.join(sorted(gop_codes))}"
    valkey = _get_valkey()

    cached = await valkey.get(cache_key)
    if cached:
        logger.debug("Exclusions cache hit: %s", cache_key)
        data = json.loads(cached)
        data["cache_hit"] = True
        return data

    exclusion_map = await get_exclusions_for_codes(gop_codes, treatment_date)

    allowed: list[str] = []
    banned: list[dict] = []
    seen_banned: set[str] = set()

    # Greedy pass: keep as many GOPs as possible.
    # On pairwise conflict the later code (second in input order) is banned.
    for code in gop_codes:
        if code in seen_banned:
            continue
        conflicts = exclusion_map.get(code, [])
        conflicting_allowed = [c for c in conflicts if c in allowed]
        if conflicting_allowed:
            banned.append({
                "code": code,
                "reason": "Mutual exclusion with already-allowed GOP",
                "excluded_by": conflicting_allowed[0],
            })
            seen_banned.add(code)
        else:
            allowed.append(code)
            for excl in exclusion_map.get(code, []):
                if excl in gop_codes and excl not in allowed:
                    seen_banned.add(excl)

    # Ensure every seen_banned entry appears in the banned list
    for code in gop_codes:
        if code in seen_banned and code not in [b["code"] for b in banned]:
            exc_by = next(
                (a for a in allowed if code in exclusion_map.get(a, [])),
                "unknown"
            )
            banned.append({
                "code": code,
                "reason": "Mutual exclusion",
                "excluded_by": exc_by,
            })

    result = {
        "allowed": allowed,
        "banned": banned,
        "matrix": exclusion_map,
        "quartal": quartal,
        "treatment_date": treatment_date_str,
        "cache_hit": False,
    }

    await valkey.setex(cache_key, 86400, json.dumps(result))
    return result
