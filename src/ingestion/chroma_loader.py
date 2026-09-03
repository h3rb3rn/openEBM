"""
ChromaDB-Loader für EBM-GOP-Beschreibungen.

Embedding-Strategie:
  - Standard: sentence-transformers (lokal, air-gapped)
  - Alternativ: Ollama-Embeddings via API
  - Jede GOP wird als Dokument gespeichert:
      Text: "GOP {code}: {description} (Kapitel {chapter}: {chapter_title})"
      Metadaten: code, chapter, insurance_types, time_value_minutes, valid_from

Quartalsversionierung: Dokumente werden mit valid_from als Metadatum gespeichert.
Beim Retrieval kann nach treatment_date gefiltert werden.
"""
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

CHROMA_HOST  = os.environ.get("CHROMA_HOST", "chromadb")
CHROMA_PORT  = int(os.environ.get("CHROMA_PORT", "8000"))
CHROMA_TOKEN = os.environ.get("CHROMA_TOKEN", "changeme")
COLLECTION_NAME = "ebm_gop_descriptions"
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
BATCH_SIZE = 50


def _build_document_text(gop: dict) -> str:
    parts = [f"GOP {gop['code']}: {gop['description']}"]
    if gop.get("chapter_title"):
        parts.append(f"Kapitel {gop['chapter']}: {gop['chapter_title']}")
    if gop.get("insurance_types"):
        parts.append(f"Kassenarten: {', '.join(gop['insurance_types'])}")
    if gop.get("time_value_minutes"):
        parts.append(f"Zeitwert: {gop['time_value_minutes']} Minuten")
    return ". ".join(parts)


def _build_metadata(gop: dict) -> dict:
    # ChromaDB erlaubt nur str, int, float, bool — None ist verboten
    return {
        "code":                str(gop["code"]),
        "chapter":             str(gop.get("chapter", "")),
        "chapter_title":       str(gop.get("chapter_title") or ""),
        "insurance_types":     ",".join(gop.get("insurance_types") or ["GKV"]),
        "time_value_minutes":  int(gop.get("time_value_minutes") or 0),
        "value_points":        float(gop.get("value_points") or 0.0),
        "valid_from":          str(gop.get("valid_from") or "2024-01-01"),
        "valid_until":         str(gop.get("valid_until") or ""),
        "description":         str(gop.get("description") or "")[:500],
    }


def _get_embedding_function():
    """Gibt die optimale Embedding-Funktion zurück (lokal → Ollama → SentenceTransformers)."""
    try:
        # Versuch: Ollama-Embeddings (falls Ollama läuft)
        import httpx
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        if resp.status_code == 200:
            logger.info("Verwende Ollama-Embeddings: %s", EMBEDDING_MODEL)
            from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
            return OllamaEmbeddingFunction(
                url=f"{OLLAMA_BASE_URL}/api/embeddings",
                model_name=EMBEDDING_MODEL,
            )
    except Exception:
        pass

    # Fallback: SentenceTransformers (immer lokal verfügbar)
    logger.info("Verwende SentenceTransformers: paraphrase-multilingual-MiniLM-L12-v2")
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    return SentenceTransformerEmbeddingFunction(
        model_name="paraphrase-multilingual-MiniLM-L12-v2"
    )


async def load_ebm_to_chroma(gops: list[dict]) -> None:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    client = chromadb.HttpClient(
        host=CHROMA_HOST,
        port=CHROMA_PORT,
        settings=ChromaSettings(
            chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
            chroma_client_auth_credentials=CHROMA_TOKEN,
        ),
    )

    ef = _get_embedding_function()

    # Collection anlegen oder wiederverwenden
    try:
        collection = client.get_collection(COLLECTION_NAME, embedding_function=ef)
        logger.info("Bestehende Collection '%s' gefunden", COLLECTION_NAME)
    except Exception:
        collection = client.create_collection(
            COLLECTION_NAME,
            embedding_function=ef,
            metadata={"description": "EBM GOP Beschreibungen", "hnsw:space": "cosine"},
        )
        logger.info("Neue Collection '%s' erstellt", COLLECTION_NAME)

    # Bestehende IDs laden (für Upsert-Logik)
    try:
        existing = set(collection.get(include=[])["ids"])
    except Exception:
        existing = set()

    total = 0
    for i in range(0, len(gops), BATCH_SIZE):
        batch = gops[i:i + BATCH_SIZE]

        ids = [f"gop_{g['code']}_{g.get('valid_from', 'current')}" for g in batch]
        documents = [_build_document_text(g) for g in batch]
        metadatas = [_build_metadata(g) for g in batch]

        # Neue GOPs hinzufügen, bestehende aktualisieren
        new_ids = [id_ for id_ in ids if id_ not in existing]
        new_docs = [d for id_, d in zip(ids, documents) if id_ not in existing]
        new_meta = [m for id_, m in zip(ids, metadatas) if id_ not in existing]

        if new_ids:
            collection.add(ids=new_ids, documents=new_docs, metadatas=new_meta)
            total += len(new_ids)
            logger.info("ChromaDB: %d GOPs eingebettet (Batch %d)", total, i // BATCH_SIZE + 1)

        # Kurze Pause um Überlastung zu vermeiden
        time.sleep(0.1)

    logger.info("ChromaDB: Insgesamt %d neue GOPs eingebettet (Collection: %s)", total, COLLECTION_NAME)
