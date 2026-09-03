"""
EBM-Katalog Ingestion Pipeline

Ablauf:
  1. EBM-Katalog laden (lokal oder von KBV-URL)
  2. GOPs parsen und normalisieren
  3. Neo4j: Graph-Struktur (Kapitel, GOPs, Ausschlüsse, Restriktionen)
  4. ChromaDB: Semantische Embeddings der GOP-Beschreibungen

Air-Gap-Modus: Lokale JSON-Datei aus /data/ebm_catalog.json
Online-Modus: Abruf von der offiziellen KBV-Quelle (Fallback)

Starten:
  docker compose --profile ingestion run ingestion
  ODER: python -m src.ingestion.fetch_ebm
"""
import asyncio
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

from .neo4j_loader import load_ebm_to_neo4j
from .chroma_loader import load_ebm_to_chroma
from .seed_data import SEED_GOPS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

CATALOG_PATH = os.environ.get("EBM_CATALOG_PATH", "/data/ebm_catalog.json")


def load_catalog() -> list[dict]:
    """
    Lädt den EBM-Katalog.
    Priorität: Lokale Datei → Seed-Daten (für Demo/Testing).
    """
    catalog_file = Path(CATALOG_PATH)

    if catalog_file.exists():
        logger.info("Lade EBM-Katalog aus: %s", CATALOG_PATH)
        with open(catalog_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        gops = data if isinstance(data, list) else data.get("gops", [])
        logger.info("Katalog geladen: %d GOPs", len(gops))
        return gops

    logger.warning(
        "Keine Katalogdatei gefunden (%s). Verwende Seed-Daten für Demo.", CATALOG_PATH
    )
    return SEED_GOPS


def validate_gop(gop: dict) -> dict | None:
    """Validiert und normalisiert einen GOP-Eintrag."""
    code = str(gop.get("code", "")).strip()
    if not code:
        return None

    today = date.today().isoformat()
    return {
        "code": code,
        "description": str(gop.get("description", "")).strip(),
        "chapter": str(gop.get("chapter", "0")),
        "chapter_title": str(gop.get("chapter_title", "")),
        "time_value_minutes": int(gop.get("time_value_minutes", 0)),
        "value_points": float(gop.get("value_points", 0.0)),
        "insurance_types": gop.get("insurance_types", ["GKV"]),
        "exclusions": gop.get("exclusions", []),
        "age_restriction_min": gop.get("age_restriction_min"),
        "age_restriction_max": gop.get("age_restriction_max"),
        "gender_restriction": gop.get("gender_restriction"),
        "valid_from": gop.get("valid_from", today),
        "valid_until": gop.get("valid_until"),
    }


async def run_ingestion():
    logger.info("=" * 60)
    logger.info("EBM Ingestion Pipeline gestartet")
    logger.info("=" * 60)

    raw_gops = load_catalog()
    gops = [g for g in (validate_gop(r) for r in raw_gops) if g is not None]
    logger.info("Validierte GOPs: %d", len(gops))

    # Neo4j
    logger.info("Lade in Neo4j...")
    try:
        await load_ebm_to_neo4j(gops)
        logger.info("Neo4j: Fertig")
    except Exception as e:
        logger.error("Neo4j-Fehler: %s", e)

    # ChromaDB
    logger.info("Lade in ChromaDB...")
    try:
        await load_ebm_to_chroma(gops)
        logger.info("ChromaDB: Fertig")
    except Exception as e:
        logger.error("ChromaDB-Fehler: %s", e)

    logger.info("Ingestion abgeschlossen.")


if __name__ == "__main__":
    asyncio.run(run_ingestion())
