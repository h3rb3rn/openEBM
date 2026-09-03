"""Neo4j client for the MCP server — async query helpers for GOP validation."""
import logging
from contextlib import asynccontextmanager
from datetime import date

from neo4j import AsyncGraphDatabase, AsyncDriver

from .config import get_mcp_settings

logger = logging.getLogger(__name__)
settings = get_mcp_settings()

_driver: AsyncDriver | None = None


async def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_pool_size=20,
        )
    return _driver


async def close_driver():
    global _driver
    if _driver:
        await _driver.close()
        _driver = None


async def get_exclusions_for_codes(
    gop_codes: list[str], treatment_date: date
) -> dict[str, list[str]]:
    """Return mutual exclusion lists per GOP code, respecting valid_from / valid_until versioning."""
    driver = await get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            UNWIND $codes AS code
            MATCH (g:GOP {code: code})
            WHERE g.valid_from <= date($treatment_date)
              AND (g.valid_until IS NULL OR g.valid_until >= date($treatment_date))
            OPTIONAL MATCH (g)-[:EXCLUDES]->(excl:GOP)
            WHERE excl.valid_from <= date($treatment_date)
              AND (excl.valid_until IS NULL OR excl.valid_until >= date($treatment_date))
            RETURN g.code AS gop_code, collect(excl.code) AS excluded_codes
            """,
            codes=gop_codes,
            treatment_date=treatment_date.isoformat(),
        )
        records = await result.data()

    exclusion_map: dict[str, list[str]] = {}
    for record in records:
        exclusion_map[record["gop_code"]] = record["excluded_codes"]
    return exclusion_map


async def get_time_values_for_codes(
    gop_codes: list[str], treatment_date: date
) -> dict[str, int]:
    """Return time value (minutes) per GOP code for time-profile plausibility checks (§ 46 BMV-Ä)."""
    driver = await get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            UNWIND $codes AS code
            MATCH (g:GOP {code: code})
            WHERE g.valid_from <= date($treatment_date)
              AND (g.valid_until IS NULL OR g.valid_until >= date($treatment_date))
            RETURN g.code AS gop_code, coalesce(g.time_value_minutes, 0) AS time_minutes
            """,
            codes=gop_codes,
            treatment_date=treatment_date.isoformat(),
        )
        records = await result.data()

    return {r["gop_code"]: r["time_minutes"] for r in records}


async def get_demographic_restrictions(
    gop_codes: list[str], treatment_date: date
) -> dict[str, dict]:
    """Return demographic restrictions (age, gender) per GOP code."""
    driver = await get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            UNWIND $codes AS code
            MATCH (g:GOP {code: code})
            WHERE g.valid_from <= date($treatment_date)
              AND (g.valid_until IS NULL OR g.valid_until >= date($treatment_date))
            OPTIONAL MATCH (g)-[:HAS_RESTRICTION]->(r:DemographicRestriction)
            RETURN g.code AS gop_code,
                   r.min_age AS min_age,
                   r.max_age AS max_age,
                   r.gender   AS gender,
                   g.insurance_types AS insurance_types
            """,
            codes=gop_codes,
            treatment_date=treatment_date.isoformat(),
        )
        records = await result.data()

    restrictions: dict[str, dict] = {}
    for r in records:
        restrictions[r["gop_code"]] = {
            "min_age": r["min_age"],
            "max_age": r["max_age"],
            "gender": r["gender"],
            "insurance_types": r["insurance_types"] or [],
        }
    return restrictions
