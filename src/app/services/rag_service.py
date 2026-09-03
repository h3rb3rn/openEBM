"""
RAG service: ChromaDB-based semantic search for EBM GOP candidates.

Workflow:
  1. Split the medical report text into sentence-based chunks
  2. Vectorize chunks with the embedding model
  3. Query ChromaDB for semantically similar GOP descriptions
  4. Return top-K candidates as context for the LLM prompt
"""
import logging
import re
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from ..config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

COLLECTION_NAME = "ebm_gop_descriptions"
TOP_K = 15

_embedding_fn = None  # lazily initialized and cached


def _get_embedding_fn():
    global _embedding_fn
    if _embedding_fn is not None:
        return _embedding_fn
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        _embedding_fn = model.encode
        logger.info("RAG: SentenceTransformer initialized")
    except Exception as e:
        logger.warning("RAG: SentenceTransformer unavailable (%s) - keyword fallback", e)
        _embedding_fn = None
    return _embedding_fn


def _get_chroma_client() -> chromadb.HttpClient:
    return chromadb.HttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
        settings=ChromaSettings(
            chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
            chroma_client_auth_credentials=settings.chroma_token,
        ),
    )


def _chunk_text(text: str, max_chars: int = 400) -> list[str]:
    """Split text into sentence-based chunks of at most `max_chars`."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) > max_chars and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current += " " + sentence
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text[:max_chars]]


async def retrieve_gop_candidates(
    report_text: str,
    insurance_type: str,
    treatment_date: str,
    top_k: int = TOP_K,
) -> list[dict]:
    """
    Semantic search: returns GOP candidates with descriptions.

    Returns:
        List of {code, description, chapter, similarity, insurance_types}
    """
    client = _get_chroma_client()

    try:
        collection = client.get_collection(COLLECTION_NAME)
    except Exception:
        logger.warning("ChromaDB collection '%s' not found - returning no candidates", COLLECTION_NAME)
        return []

    chunks = _chunk_text(report_text)
    seen_codes: set[str] = set()
    candidates: list[dict] = []

    embed_fn = _get_embedding_fn()

    for chunk in chunks[:5]:  # cap at 5 chunks per report
        try:
            if embed_fn is not None:
                vec = embed_fn([chunk])[0].tolist()
                # chromadb client 0.6 has no $contains operator, so the
                # insurance-type filter is applied client-side below
                results = collection.query(
                    query_embeddings=[vec],
                    n_results=min(top_k, 10),
                    include=["metadatas", "distances", "documents"],
                )
            else:
                results = collection.query(
                    query_texts=[chunk],
                    n_results=min(top_k, 10),
                    include=["metadatas", "distances", "documents"],
                )
        except Exception as e:
            logger.error("ChromaDB query failed: %s", e)
            continue

        for i, meta in enumerate(results.get("metadatas", [[]])[0]):
            code = meta.get("code", "")
            if insurance_type and insurance_type not in meta.get("insurance_types", ""):
                continue
            if code and code not in seen_codes:
                seen_codes.add(code)
                distance = results.get("distances", [[]])[0][i] if results.get("distances") else 1.0
                candidates.append({
                    "code": code,
                    "description": results.get("documents", [[]])[0][i] if results.get("documents") else meta.get("description", ""),
                    "chapter": meta.get("chapter", ""),
                    "chapter_title": meta.get("chapter_title", ""),
                    "similarity": round(1 - distance, 4),
                    "insurance_types": meta.get("insurance_types", "").split(","),
                    "time_value_minutes": meta.get("time_value_minutes", 0),
                    "value_points": meta.get("value_points", 0),
                })

    # Highest similarity first
    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    return candidates[:top_k]


def build_rag_context(candidates: list[dict]) -> str:
    """Format GOP candidates as readable context for the (German) LLM prompt."""
    if not candidates:
        return "Keine GOP-Kandidaten gefunden."
    lines = []
    for c in candidates:
        lines.append(
            f"GOP {c['code']} (Kapitel {c['chapter']}): {c['description'][:200]}"
            f" [Zeitwert: {c['time_value_minutes']} Min., Kassenarten: {', '.join(c['insurance_types'])}]"
        )
    return "\n".join(lines)
