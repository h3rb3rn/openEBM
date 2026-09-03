"""
EBM import service: runs the ingestion pipeline from inside the app container.

Two catalog sources feed the same Neo4j/ChromaDB loader:
  1. A local JSON file (EBM_CATALOG_PATH) — the original air-gapped path.
  2. The KBV's quarterly PDF, downloaded and parsed on demand
     (see kbv_import.py) via a two-step preview → commit flow, since the
     PDF parser is a heuristic best-effort extraction and a silent
     mis-parse would be billing-relevant. Nothing from the PDF path
     reaches Neo4j/ChromaDB until an admin explicitly confirms the
     preview.

Import progress and pending (not-yet-committed) KBV previews are stored
in Valkey rather than in-process memory: the app runs with multiple
uvicorn worker processes (see app.Dockerfile, --workers 2), each with
its own separate memory space, so an in-memory dict would make status
polling randomly show stale state depending on which worker happened to
handle a given request — a real, observed bug during development,
especially noticeable given the KBV PDF fetch+parse takes several
minutes.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models.base import SystemSetting
from . import kbv_import

logger = logging.getLogger(__name__)
settings = get_settings()

CATALOG_PATH = os.environ.get("EBM_CATALOG_PATH", "/data/ebm_catalog.json")

_STATUS_KEY = "ebm_import:status"
_PENDING_KEY_PREFIX = "ebm_import:pending:"
_PENDING_TTL = 3600  # 1h — a stale, un-reviewed preview should not linger indefinitely

_DEFAULT_STATUS: dict[str, Any] = {
    "state":        "idle",    # idle | running | fetching | preview_ready | committing | done | error
    "started_at":   None,
    "finished_at":  None,
    "gop_count":    0,
    "message":      "",
    "preview":      None,      # set when state == "preview_ready": {run_id, stats, sample, source_url}
}


def _get_valkey() -> aioredis.Redis:
    return aioredis.from_url(settings.valkey_url, decode_responses=True)


async def _get_status() -> dict:
    valkey = _get_valkey()
    raw = await valkey.get(_STATUS_KEY)
    return json.loads(raw) if raw else dict(_DEFAULT_STATUS)


async def _set_status(**fields) -> None:
    current = await _get_status()
    current.update(fields)
    valkey = _get_valkey()
    await valkey.set(_STATUS_KEY, json.dumps(current, default=str))


async def get_import_status() -> dict:
    return await _get_status()


def get_catalog_info() -> dict | None:
    """Read metadata about the catalog file, or None if it does not exist."""
    p = Path(CATALOG_PATH)
    if not p.exists():
        return None
    try:
        stat = p.stat()
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        gops = data if isinstance(data, list) else data.get("gops", [])
        return {
            "path":         CATALOG_PATH,
            "file_size_mb": round(stat.st_size / 1024 / 1024, 2),
            "modified_at":  datetime.fromtimestamp(stat.st_mtime).isoformat()[:16],
            "gop_count":    len(gops),
            "source":       data.get("source", "") if isinstance(data, dict) else "",
            "valid_from":   data.get("valid_from", "") if isinstance(data, dict) else "",
        }
    except Exception as e:
        logger.error("Failed to read catalog info: %s", e)
        return None


async def get_db_stats() -> dict:
    """Read GOP counts from ChromaDB and Neo4j."""
    stats: dict[str, Any] = {"chroma_gop_count": 0, "neo4j_gop_count": 0, "neo4j_chapter_count": 0}

    # ChromaDB
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        from ..config import get_settings
        s = get_settings()
        client = chromadb.HttpClient(
            host=s.chroma_host,
            port=s.chroma_port,
            settings=ChromaSettings(
                chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
                chroma_client_auth_credentials=s.chroma_token,
            ),
        )
        col = client.get_collection("ebm_gop_descriptions")
        stats["chroma_gop_count"] = col.count()
    except Exception as e:
        logger.warning("ChromaDB stats failed: %s", e)

    # Neo4j
    try:
        from neo4j import AsyncGraphDatabase
        from ..config import get_settings
        s = get_settings()
        driver = AsyncGraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))
        async with driver.session() as session:
            r = await session.run("MATCH (g:GOP) RETURN count(g) AS n")
            rec = await r.single()
            stats["neo4j_gop_count"] = rec["n"] if rec else 0
            r2 = await session.run("MATCH (c:Chapter) RETURN count(c) AS n")
            rec2 = await r2.single()
            stats["neo4j_chapter_count"] = rec2["n"] if rec2 else 0
        await driver.close()
    except Exception as e:
        logger.warning("Neo4j stats failed: %s", e)

    return stats


async def _load_gops_into_databases(gops: list[dict]) -> None:
    """Shared Neo4j + ChromaDB writer used by both the local-file and the KBV-PDF import paths."""
    await _set_status(message=f"{len(gops)} GOPs validiert. Schreibe in Neo4j…")
    logger.info("Import: loading %d GOPs", len(gops))

    s = get_settings()
    from neo4j import AsyncGraphDatabase
    driver = AsyncGraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))
    try:
        async with driver.session() as session:
            await _neo4j_constraints(session)
            await _neo4j_chapters(session, gops)
            await _neo4j_gops(session, gops)
            await _neo4j_exclusions(session, gops)
    finally:
        await driver.close()
    await _set_status(message="Neo4j fertig. Schreibe in ChromaDB…")
    logger.info("Import: Neo4j done")

    await _chroma_load(gops, s)
    await _set_status(message="ChromaDB fertig.")
    logger.info("Import: ChromaDB done")


async def _run_ingestion(catalog_path: str) -> None:
    await _set_status(state="running", started_at=datetime.now().isoformat()[:16], message="Lade Katalog…", preview=None)
    t0 = time.time()

    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_gops = data if isinstance(data, list) else data.get("gops", [])
        gops = [_validate_gop(g) for g in raw_gops]
        gops = [g for g in gops if g]

        await _load_gops_into_databases(gops)

        elapsed = round(time.time() - t0)
        await _set_status(
            state="done",
            finished_at=datetime.now().isoformat()[:16],
            gop_count=len(gops),
            message=f"Import erfolgreich: {len(gops)} GOPs in {elapsed}s eingelesen.",
        )
    except Exception as e:
        logger.error("Import failed: %s", e, exc_info=True)
        await _set_status(
            state="error",
            finished_at=datetime.now().isoformat()[:16],
            message=f"Fehler: {e}",
        )


async def start_import_background(catalog_path: str | None = None) -> bool:
    """Start the local-file import as an asyncio task. Returns False if one is already running."""
    status = await _get_status()
    if status["state"] in ("running", "fetching", "committing"):
        return False
    path = catalog_path or CATALOG_PATH
    asyncio.create_task(_run_ingestion(path))
    return True


# ─── KBV PDF import (preview → commit) ─────────────────────────────────────────

DEFAULT_KBV_SOURCE_URL = kbv_import.DEFAULT_SOURCE_URL
_SOURCE_URL_KEY = kbv_import.DEFAULT_SOURCE_URL_SETTING_KEY


async def get_kbv_source_url(db: AsyncSession) -> str:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == _SOURCE_URL_KEY))
    setting = result.scalar_one_or_none()
    if setting and setting.value.get("url"):
        return setting.value["url"]
    return DEFAULT_KBV_SOURCE_URL


async def set_kbv_source_url(db: AsyncSession, url: str, user_id: str) -> None:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == _SOURCE_URL_KEY))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = {"url": url}
        setting.updated_by_id = user_id
    else:
        db.add(SystemSetting(key=_SOURCE_URL_KEY, value={"url": url}, updated_by_id=user_id))
    await db.commit()


async def _store_pending_kbv_import(run_id: str, gops: list[dict]) -> None:
    valkey = _get_valkey()
    await valkey.setex(f"{_PENDING_KEY_PREFIX}{run_id}", _PENDING_TTL, json.dumps(gops))


async def _get_pending_kbv_import(run_id: str) -> list[dict] | None:
    valkey = _get_valkey()
    raw = await valkey.get(f"{_PENDING_KEY_PREFIX}{run_id}")
    return json.loads(raw) if raw else None


async def _delete_pending_kbv_import(run_id: str) -> None:
    valkey = _get_valkey()
    await valkey.delete(f"{_PENDING_KEY_PREFIX}{run_id}")


async def _run_kbv_fetch(url: str) -> None:
    """Download + parse the KBV PDF. Stores the result as a pending preview — does NOT write to Neo4j/ChromaDB."""
    await _set_status(state="fetching", started_at=datetime.now().isoformat()[:16], message="Lade PDF von KBV…", preview=None)

    try:
        pdf_bytes = await kbv_import.fetch_kbv_pdf(url)
        await _set_status(message=f"PDF geladen ({len(pdf_bytes) // 1024} KB). Analysiere Inhalt…")

        parsed = await asyncio.get_running_loop().run_in_executor(None, kbv_import.parse_kbv_pdf, pdf_bytes)

        if not parsed.gops:
            await _set_status(
                state="error",
                finished_at=datetime.now().isoformat()[:16],
                message="Keine GOP-Einträge im PDF erkannt. " + " ".join(parsed.warnings),
            )
            return

        run_id = str(uuid.uuid4())
        await _store_pending_kbv_import(run_id, parsed.gops)

        stats = parsed.stats
        await _set_status(
            state="preview_ready",
            finished_at=datetime.now().isoformat()[:16],
            message=f"Vorschau bereit: {stats['total_gops']} GOPs erkannt. Bitte prüfen und übernehmen.",
            preview={
                "run_id": run_id,
                "source_url": url,
                "stats": stats,
                "sample": parsed.gops[:8],
                "document_warnings": parsed.warnings,
            },
        )
    except Exception as e:
        logger.error("KBV fetch/parse failed: %s", e, exc_info=True)
        await _set_status(
            state="error",
            finished_at=datetime.now().isoformat()[:16],
            message=f"Fehler beim Laden/Parsen: {e}",
        )


async def start_kbv_fetch_background(url: str) -> bool:
    status = await _get_status()
    if status["state"] in ("running", "fetching", "committing"):
        return False
    asyncio.create_task(_run_kbv_fetch(url))
    return True


async def _run_kbv_commit(run_id: str) -> None:
    gops = await _get_pending_kbv_import(run_id)
    if gops is None:
        await _set_status(state="error", message="Vorschau abgelaufen oder nicht gefunden. Bitte erneut laden.")
        return

    await _set_status(state="committing", started_at=datetime.now().isoformat()[:16], message="Übernehme geprüfte Daten…", preview=None)
    t0 = time.time()
    try:
        await _load_gops_into_databases(gops)
        await _delete_pending_kbv_import(run_id)
        elapsed = round(time.time() - t0)
        await _set_status(
            state="done",
            finished_at=datetime.now().isoformat()[:16],
            gop_count=len(gops),
            message=f"Import erfolgreich: {len(gops)} GOPs in {elapsed}s übernommen.",
        )
    except Exception as e:
        logger.error("KBV commit failed: %s", e, exc_info=True)
        await _set_status(
            state="error",
            finished_at=datetime.now().isoformat()[:16],
            message=f"Fehler beim Übernehmen: {e}",
        )


async def start_kbv_commit_background(run_id: str) -> bool:
    if await _get_pending_kbv_import(run_id) is None:
        return False
    status = await _get_status()
    if status["state"] in ("running", "fetching", "committing"):
        return False
    asyncio.create_task(_run_kbv_commit(run_id))
    return True


# ─── Ingestion helpers ────────────────────────────────────────────────────────

def _validate_gop(gop: dict) -> dict | None:
    """Normalize a raw catalog entry; returns None for entries without a code."""
    code = str(gop.get("code", "")).strip()
    if not code:
        return None
    today = date.today().isoformat()
    return {
        "code":               code,
        "description":        str(gop.get("description", "")).strip(),
        "chapter":            str(gop.get("chapter", "0")),
        "chapter_title":      str(gop.get("chapter_title", "")),
        "time_value_minutes": int(gop.get("time_value_minutes") or 0),
        "value_points":       float(gop.get("value_points") or 0.0),
        "insurance_types":    gop.get("insurance_types") or ["GKV"],
        "exclusions":         gop.get("exclusions") or [],
        "age_restriction_min": gop.get("age_restriction_min"),
        "age_restriction_max": gop.get("age_restriction_max"),
        "gender_restriction": gop.get("gender_restriction"),
        "valid_from":         gop.get("valid_from", today),
        "valid_until":        gop.get("valid_until"),
    }


async def _neo4j_constraints(session) -> None:
    await session.run("CREATE CONSTRAINT gop_code IF NOT EXISTS FOR (g:GOP) REQUIRE g.code IS UNIQUE")
    await session.run("CREATE CONSTRAINT chapter_id IF NOT EXISTS FOR (c:Chapter) REQUIRE c.id IS UNIQUE")


async def _neo4j_chapters(session, gops: list[dict]) -> None:
    chapters = {}
    for g in gops:
        ch = g["chapter"]
        if ch not in chapters:
            chapters[ch] = g.get("chapter_title", f"Kapitel {ch}")
    await session.run(
        """
        UNWIND $chapters AS ch
        MERGE (c:Chapter {id: ch.id})
        SET c.title = ch.title, c.valid_from = date(ch.vf)
        """,
        chapters=[{"id": k, "title": v, "vf": date.today().isoformat()} for k, v in chapters.items()],
    )


async def _neo4j_gops(session, gops: list[dict]) -> None:
    BATCH = 200
    for i in range(0, len(gops), BATCH):
        batch = gops[i:i+BATCH]
        await session.run(
            """
            UNWIND $gops AS g
            MERGE (n:GOP {code: g.code})
            SET n.description        = g.description,
                n.chapter            = g.chapter,
                n.chapter_title      = g.chapter_title,
                n.time_value_minutes = g.time_value_minutes,
                n.value_points       = g.value_points,
                n.insurance_types    = g.insurance_types,
                n.valid_from         = date(g.valid_from)
            WITH n, g
            MATCH (c:Chapter {id: g.chapter})
            MERGE (n)-[:BELONGS_TO]->(c)
            """,
            gops=[{
                "code": g["code"], "description": g["description"],
                "chapter": g["chapter"], "chapter_title": g["chapter_title"],
                "time_value_minutes": g["time_value_minutes"],
                "value_points": g["value_points"],
                "insurance_types": g["insurance_types"],
                "valid_from": g["valid_from"] or date.today().isoformat(),
            } for g in batch],
        )


async def _neo4j_exclusions(session, gops: list[dict]) -> None:
    pairs = [
        {"from_code": g["code"], "to_code": exc}
        for g in gops for exc in (g.get("exclusions") or [])
        if exc != g["code"]
    ]
    if not pairs:
        return
    BATCH = 500
    for i in range(0, len(pairs), BATCH):
        await session.run(
            """
            UNWIND $pairs AS p
            MATCH (a:GOP {code: p.from_code}), (b:GOP {code: p.to_code})
            MERGE (a)-[:EXCLUDES]->(b)
            """,
            pairs=pairs[i:i+BATCH],
        )


async def _chroma_load(gops: list[dict], settings) -> None:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from sentence_transformers import SentenceTransformer

    client = chromadb.HttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
        settings=ChromaSettings(
            chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
            chroma_client_auth_credentials=settings.chroma_token,
        ),
    )

    # Rebuild the collection from scratch on every import
    try:
        client.delete_collection("ebm_gop_descriptions")
    except Exception:
        pass
    col = client.get_or_create_collection("ebm_gop_descriptions", metadata={"hnsw:space": "cosine"})

    model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")

    def _doc(g):
        parts = [f"GOP {g['code']}: {g['description']}"]
        if g.get("chapter_title"):
            parts.append(f"Kapitel {g['chapter']}: {g['chapter_title']}")
        return ". ".join(parts)

    def _meta(g):
        return {
            "code":               str(g["code"]),
            "chapter":            str(g.get("chapter", "")),
            "chapter_title":      str(g.get("chapter_title") or ""),
            "insurance_types":    ",".join(g.get("insurance_types") or ["GKV"]),
            "time_value_minutes": int(g.get("time_value_minutes") or 0),
            "value_points":       float(g.get("value_points") or 0.0),
            "valid_from":         str(g.get("valid_from") or "2024-01-01"),
            "description":        str(g.get("description") or "")[:500],
        }

    BATCH = 50
    for i in range(0, len(gops), BATCH):
        batch = gops[i:i+BATCH]
        docs  = [_doc(g) for g in batch]
        metas = [_meta(g) for g in batch]
        ids   = [f"gop_{g['code']}" for g in batch]
        vecs  = model.encode(docs).tolist()
        col.add(documents=docs, embeddings=vecs, metadatas=metas, ids=ids)
        logger.info("ChromaDB import: %d/%d GOPs", i + len(batch), len(gops))
