"""
Neo4j-Loader für EBM-Katalog.

Graph-Schema:
  (:Chapter {id, title, valid_from, valid_until})
  (:GOP     {code, description, time_value_minutes, value_points, insurance_types, valid_from, valid_until})
  (:GOP)-[:BELONGS_TO]->(:Chapter)
  (:GOP)-[:EXCLUDES {reason}]->(:GOP)
  (:DemographicRestriction {min_age, max_age, gender})<-[:HAS_RESTRICTION]-(:GOP)

Temporal Versioning: valid_from / valid_until auf ALLEN Knoten.
"""
import logging
import os
from datetime import date

from neo4j import AsyncGraphDatabase

logger = logging.getLogger(__name__)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "changeme")


async def load_ebm_to_neo4j(gops: list[dict]) -> None:
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        async with driver.session() as session:
            await _create_constraints(session)
            await _load_chapters(session, gops)
            await _load_gops(session, gops)
            await _load_exclusions(session, gops)
            await _load_demographic_restrictions(session, gops)
    finally:
        await driver.close()


async def _create_constraints(session):
    await session.run("CREATE CONSTRAINT gop_code IF NOT EXISTS FOR (g:GOP) REQUIRE g.code IS UNIQUE")
    await session.run("CREATE CONSTRAINT chapter_id IF NOT EXISTS FOR (c:Chapter) REQUIRE c.id IS UNIQUE")
    await session.run(
        "CREATE INDEX gop_valid_from IF NOT EXISTS FOR (g:GOP) ON (g.valid_from)"
    )
    logger.info("Neo4j-Constraints und Indizes erstellt")


async def _load_chapters(session, gops: list[dict]) -> None:
    chapters = {}
    for g in gops:
        ch = g["chapter"]
        if ch not in chapters:
            chapters[ch] = g.get("chapter_title", f"Kapitel {ch}")

    result = await session.run(
        """
        UNWIND $chapters AS ch
        MERGE (c:Chapter {id: ch.id})
        SET c.title = ch.title,
            c.valid_from = date(ch.valid_from),
            c.valid_until = CASE WHEN ch.valid_until IS NOT NULL THEN date(ch.valid_until) ELSE NULL END
        RETURN count(c) AS count
        """,
        chapters=[
            {
                "id": k,
                "title": v,
                "valid_from": date.today().isoformat(),
                "valid_until": None,
            }
            for k, v in chapters.items()
        ],
    )
    record = await result.single()
    logger.info("Neo4j: %d Kapitel angelegt/aktualisiert", record["count"] if record else 0)


async def _load_gops(session, gops: list[dict]) -> None:
    total = 0
    batch_size = 100

    for i in range(0, len(gops), batch_size):
        batch = gops[i:i + batch_size]
        params = [
            {
                "code": g["code"],
                "description": g["description"],
                "chapter": g["chapter"],
                "time_value_minutes": g["time_value_minutes"],
                "value_points": g["value_points"],
                "insurance_types": g["insurance_types"],
                "valid_from": g["valid_from"],
                "valid_until": g.get("valid_until"),
            }
            for g in batch
        ]

        result = await session.run(
            """
            UNWIND $gops AS data
            MERGE (g:GOP {code: data.code})
            SET g.description      = data.description,
                g.time_value_minutes = data.time_value_minutes,
                g.value_points      = data.value_points,
                g.insurance_types   = data.insurance_types,
                g.valid_from        = date(data.valid_from),
                g.valid_until       = CASE WHEN data.valid_until IS NOT NULL
                                          THEN date(data.valid_until) ELSE NULL END
            WITH g, data
            MATCH (c:Chapter {id: data.chapter})
            MERGE (g)-[:BELONGS_TO]->(c)
            RETURN count(g) AS count
            """,
            gops=params,
        )
        record = await result.single()
        total += record["count"] if record else 0

    logger.info("Neo4j: %d GOPs geladen", total)


async def _load_exclusions(session, gops: list[dict]) -> None:
    exclusion_pairs = []
    for g in gops:
        for excl_code in g.get("exclusions", []):
            exclusion_pairs.append({"from": g["code"], "to": excl_code})

    if not exclusion_pairs:
        return

    await session.run(
        """
        UNWIND $pairs AS pair
        MATCH (a:GOP {code: pair.from})
        MATCH (b:GOP {code: pair.to})
        MERGE (a)-[:EXCLUDES]->(b)
        MERGE (b)-[:EXCLUDES]->(a)
        """,
        pairs=exclusion_pairs,
    )
    logger.info("Neo4j: %d Ausschlusspaare angelegt", len(exclusion_pairs))


async def _load_demographic_restrictions(session, gops: list[dict]) -> None:
    restricted = [
        g for g in gops
        if g.get("age_restriction_min") is not None
        or g.get("age_restriction_max") is not None
        or g.get("gender_restriction")
    ]
    if not restricted:
        return

    params = []
    for g in restricted:
        min_age = g.get("age_restriction_min")
        max_age = g.get("age_restriction_max")
        gender  = g.get("gender_restriction")
        # Neo4j MERGE kann keine null-Properties in den Key-Properties haben
        if min_age is None and max_age is None and not gender:
            continue
        params.append({
            "code":    g["code"],
            "min_age": int(min_age) if min_age is not None else -1,
            "max_age": int(max_age) if max_age is not None else 999,
            "gender":  gender or "ANY",
        })

    if not params:
        return

    await session.run(
        """
        UNWIND $gops AS data
        MATCH (g:GOP {code: data.code})
        MERGE (r:DemographicRestriction {
            gop_code:  data.code,
            min_age:   data.min_age,
            max_age:   data.max_age,
            gender:    data.gender
        })
        MERGE (g)-[:HAS_RESTRICTION]->(r)
        """,
        gops=params,
    )
    logger.info("Neo4j: %d demographische Restriktionen angelegt", len(restricted))
