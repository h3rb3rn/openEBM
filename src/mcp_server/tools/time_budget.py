"""
MCP Tool: ebm_validator_time_budget

Time-profile plausibility check per § 46 BMV-Ä / KBV Plausibilitätsrichtlinie.
Verifies that the total time values of proposed GOPs for a single day do not
exceed the physiologically possible maximum.

Daily physician work limit:   780 minutes (13 h) — KBV threshold.
Quarterly physician work limit: 52,000 minutes (~867 h / quarter).
"""
import json
import logging
from datetime import date

import redis.asyncio as aioredis

from ..neo4j_client import get_time_values_for_codes
from ..config import get_mcp_settings

logger = logging.getLogger(__name__)
settings = get_mcp_settings()

# KBV plausibility thresholds
MAX_MINUTES_PER_DAY = 780
MAX_MINUTES_PER_QUARTAL = 52_000

_valkey: aioredis.Redis | None = None


def _get_valkey() -> aioredis.Redis:
    global _valkey
    if _valkey is None:
        _valkey = aioredis.from_url(settings.valkey_url, decode_responses=True)
    return _valkey


async def check_time_budget(
    gop_codes: list[str],
    treatment_date_str: str,
    patient_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    """
    Validate time-profile plausibility for the proposed GOPs.

    Args:
        gop_codes:          List of GOP codes to check.
        treatment_date_str: Treatment date ISO-8601.
        patient_id:         Persistent mode — loads today's accumulator from Valkey.
        session_id:         Instant mode — transient accumulator key.

    Returns:
        {
          "total_minutes": 45,
          "daily_budget_minutes": 780,
          "budget_exceeded": false,
          "flagged_codes": [],
          "per_code": {"01435": 5, "01600": 10},
          "accumulated_today": 30,
          "remaining_minutes": 735
        }
    """
    try:
        treatment_date = date.fromisoformat(treatment_date_str)
    except ValueError:
        return {"error": f"Ungültiges Datum: {treatment_date_str}"}

    valkey = _get_valkey()

    time_values = await get_time_values_for_codes(gop_codes, treatment_date)

    # Load today's accumulated minutes from Valkey — key: tb:{context_id}:{date}
    context_key = patient_id or session_id
    accumulated_today = 0
    if context_key:
        acc_key = f"tb:{context_key}:{treatment_date_str}"
        acc_raw = await valkey.get(acc_key)
        if acc_raw:
            accumulated_today = int(acc_raw)

    total_proposed_minutes = sum(time_values.get(c, 0) for c in gop_codes)
    grand_total = accumulated_today + total_proposed_minutes

    budget_exceeded = grand_total > MAX_MINUTES_PER_DAY
    flagged_codes: list[dict] = []

    if budget_exceeded:
        # Identify the specific GOPs that push over the limit
        running = accumulated_today
        for code in gop_codes:
            t = time_values.get(code, 0)
            if running + t > MAX_MINUTES_PER_DAY:
                flagged_codes.append({
                    "code": code,
                    "minutes": t,
                    "reason": (
                        f"Daily budget exceeded: "
                        f"{running + t} min > {MAX_MINUTES_PER_DAY} min"
                    ),
                })
            running += t

    return {
        "total_minutes_proposed": total_proposed_minutes,
        "accumulated_today_minutes": accumulated_today,
        "grand_total_minutes": grand_total,
        "daily_budget_minutes": MAX_MINUTES_PER_DAY,
        "budget_exceeded": budget_exceeded,
        "flagged_codes": flagged_codes,
        "per_code_minutes": {c: time_values.get(c, 0) for c in gop_codes},
        "remaining_minutes": max(0, MAX_MINUTES_PER_DAY - grand_total),
        "treatment_date": treatment_date_str,
    }
