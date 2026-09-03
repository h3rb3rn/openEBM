"""
LLM service: the probabilistic text-understanding layer (split-brain architecture).

Supported backends:
  - Ollama (native /api/generate format, air-gapped default)
  - OpenAI-compatible endpoints (/v1/chat/completions)

The LLM proposes GOP codes with character offsets - deterministic validation
happens afterwards in the MCPClient, never here.

Note: prompts and user-facing error details are intentionally German because
the application analyzes German clinical reports for German medical billing.
"""
import json
import logging
import re
from datetime import date

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

ANALYSIS_PROMPT_DE = """\
Analysiere den folgenden Arztbrief auf abrechenbare EBM-GOP (GKV Deutschland).
Versicherung: {insurance_type} | Datum: {treatment_date}

BEKANNTE GOP-KANDIDATEN:
{rag_context}

BERICHT:
{report_text}

Identifiziere alle abrechenbaren Leistungen. Gib exakte Zeichenpositionen (start_char, end_char) des jeweiligen Textstücks aus dem Bericht an.
Antworte NUR mit JSON:
{{"suggestions":[{{"gop_code":"XXXXX","confidence":0.92,"reasoning":"Begründung","source_text":"Zitat","start_char":0,"end_char":10}}]}}
"""

SYSTEM_PROMPT_DE = (
    "Du bist ein präziser medizinischer EBM-Kodierungsexperte. "
    "Antworte ausschließlich mit validem JSON."
)

# Local Ollama fallback if the primary URL is unreachable
_OLLAMA_FALLBACK_URL = "http://host.docker.internal:11434"

# Cached protocol detection result:
# True = primary URL speaks Ollama /api/generate, False = OpenAI /v1/chat only
_primary_speaks_ollama: bool | None = None


def _ollama_url() -> str:
    # localhost inside a container points at the container itself; rewrite to
    # the docker host gateway so a host-side Ollama remains reachable
    return settings.ollama_base_url.replace("localhost", "host.docker.internal").replace(
        "127.0.0.1", "host.docker.internal"
    )


async def _primary_url_speaks_ollama() -> bool:
    """One-time (cached) check whether the primary URL speaks Ollama format."""
    global _primary_speaks_ollama
    if _primary_speaks_ollama is not None:
        return _primary_speaks_ollama
    models = await _ollama_list_models(_ollama_url())
    _primary_speaks_ollama = len(models) > 0
    logger.info(
        "LLM protocol detection: %s -> %s",
        _ollama_url(),
        "Ollama /api/generate" if _primary_speaks_ollama else "OpenAI /v1/chat/completions",
    )
    return _primary_speaks_ollama


async def _ollama_list_models(base_url: str) -> list[str]:
    """List models via Ollama format (/api/tags); empty list on any failure."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{base_url}/api/tags")
            if r.status_code == 200:
                return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return []


async def _openai_list_models(base_url: str) -> list[str]:
    """List models via OpenAI format (/v1/models); empty list on any failure."""
    headers = {}
    if settings.openai_api_key:
        headers["Authorization"] = f"Bearer {settings.openai_api_key}"
    # base_url may or may not already include /v1
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{url}/models", headers=headers)
            if r.status_code == 200:
                data = r.json()
                raw = data.get("data") or data.get("models") or []
                return [m.get("id", m) if isinstance(m, dict) else str(m) for m in raw]
    except Exception:
        pass
    return []


async def list_available_models() -> list[dict]:
    """
    Return all models available through the configured LLM API.

    For the "ollama" provider this also tries the OpenAI-compatible /v1/models
    on the same base URL, because some endpoints (e.g. moe-sovereign.org) only
    speak that format. Result shape: [{"id": "<name>", "provider": "ollama"|"openai"}]
    """
    if settings.llm_provider == "ollama":
        primary = _ollama_url()

        # 1. Native Ollama format (/api/tags)
        models = await _ollama_list_models(primary)
        if models:
            return [{"id": m, "provider": "ollama"} for m in models]

        # 2. OpenAI-compatible format on the same URL (/v1/models)
        models = await _openai_list_models(primary)
        if models:
            logger.info("Loaded %d models from %s via OpenAI format", len(models), primary)
            return [{"id": m, "provider": "openai"} for m in models]

        # 3. Local Ollama fallback (port 11434)
        models = await _ollama_list_models(_OLLAMA_FALLBACK_URL)
        if models:
            logger.info("Loaded models from local fallback Ollama (%s)", _OLLAMA_FALLBACK_URL)
            return [{"id": m, "provider": "ollama"} for m in models]

        return []

    # openai provider: query /models directly
    headers = {"Content-Type": "application/json"}
    if settings.openai_api_key:
        headers["Authorization"] = f"Bearer {settings.openai_api_key}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{settings.openai_base_url}/models", headers=headers)
            if r.status_code == 200:
                data = r.json()
                raw = data.get("data") or data.get("models") or []
                return [{"id": m.get("id", m) if isinstance(m, dict) else m, "provider": "openai"} for m in raw]
    except Exception as e:
        logger.warning("Could not fetch model list: %s", e)
    return [{"id": settings.openai_model, "provider": "openai"}]


def _pick_fallback_model(configured_model: str, fallback_models: list[str]) -> str:
    """Choose the closest match for the configured model from the fallback list."""
    prefix = configured_model.split(":")[0].split("@")[0]
    preferred = [m for m in fallback_models if prefix[:5] in m]
    return preferred[0] if preferred else fallback_models[0]


async def _call_ollama(prompt: str, model_override: str | None = None, reasoning: bool = False) -> str:
    primary_url = _ollama_url()
    headers: dict = {}
    if settings.openai_api_key:
        headers["Authorization"] = f"Bearer {settings.openai_api_key}"

    base = primary_url
    model = model_override or settings.ollama_model

    if not model_override:
        # Automatic fallback only applies when no explicit model was requested
        primary_models = await _ollama_list_models(primary_url)
        needs_fallback = (primary_models and model not in primary_models) or not primary_models
        if needs_fallback:
            fallback_models = await _ollama_list_models(_OLLAMA_FALLBACK_URL)
            if fallback_models:
                base = _OLLAMA_FALLBACK_URL
                headers = {}
                model = _pick_fallback_model(model, fallback_models)
                logger.warning(
                    "Model '%s' unavailable on primary Ollama URL - falling back to %s with '%s'",
                    settings.ollama_model, base, model,
                )

    logger.info("Ollama call: %s - model: %s - reasoning: %s", base, model, reasoning)
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            f"{base}/api/generate",
            headers=headers,
            json={
                "model": model,
                "system": SYSTEM_PROMPT_DE,
                "prompt": prompt,
                "stream": False,
                "think": reasoning,
                "format": "json",
                "options": {"temperature": 0.1, "top_p": 0.9, "num_predict": 2048},
            },
        )
        response.raise_for_status()
        data = response.json()
        raw = data.get("response") or data.get("message", {}).get("content", "")
        if not reasoning:
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        return raw


async def _call_openai_compatible(prompt: str, model_override: str | None = None, reasoning: bool = False) -> str:
    headers = {"Content-Type": "application/json"}
    if settings.openai_api_key:
        headers["Authorization"] = f"Bearer {settings.openai_api_key}"

    model = model_override or settings.openai_model
    # "/nothink" suppresses chain-of-thought on Qwen3 models; omit when reasoning is on
    prefix = "" if reasoning else "/nothink\n\n"
    system_content = f"{prefix}{SYSTEM_PROMPT_DE}"

    # Streaming is mandatory: some OpenAI-compatible APIs (e.g. moe-sovereign.org)
    # never respond to non-streaming chat completions
    chunks: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=300, write=10, pool=10)) as client:
        async with client.stream(
            "POST",
            f"{settings.openai_base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "stream": True,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)
                    token = delta["choices"][0]["delta"].get("content") or ""
                    chunks.append(token)
                except Exception:
                    pass

    return "".join(chunks)


def _parse_llm_response(raw: str) -> list[dict]:
    """Parse the LLM answer and extract GOP suggestions robustly."""
    try:
        # Extract the JSON block in case the model wrapped it in prose
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = json.loads(raw)
        return data.get("suggestions", [])
    except json.JSONDecodeError as e:
        logger.error("LLM JSON parsing failed: %s\nRaw: %.500s", e, raw)
        return []


def _validate_char_offsets(suggestion: dict, report_text: str) -> dict:
    """Repair or invalidate broken character offsets returned by the LLM."""
    start = suggestion.get("start_char", 0)
    end = suggestion.get("end_char", 0)
    source = suggestion.get("source_text", "")
    n = len(report_text)

    if start < 0 or end < 0 or start >= n or end > n or start >= end:
        # Fallback: locate the quoted source text inside the report
        if source:
            pos = report_text.lower().find(source.lower())
            if pos >= 0:
                suggestion["start_char"] = pos
                suggestion["end_char"] = pos + len(source)
            else:
                suggestion["start_char"] = None
                suggestion["end_char"] = None
    return suggestion


async def analyze_report(
    report_text: str,
    insurance_type: str,
    treatment_date: str,
    rag_context: str,
    model: str | None = None,
    reasoning: bool = False,
) -> list[dict]:
    """
    Main entry point: the LLM analyzes the report and returns GOP suggestions.
    No MCP validation happens here - that is the orchestrator's job.
    """
    prompt = ANALYSIS_PROMPT_DE.format(
        insurance_type=insurance_type,
        treatment_date=treatment_date,
        rag_context=rag_context,
        report_text=report_text[:3000],
    )

    try:
        if settings.llm_provider == "ollama" and await _primary_url_speaks_ollama():
            raw = await _call_ollama(prompt, model_override=model, reasoning=reasoning)
        else:
            # OpenAI-compatible endpoint (or ollama provider whose URL only speaks OpenAI)
            raw = await _call_openai_compatible(prompt, model_override=model, reasoning=reasoning)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as exc:
        provider = settings.llm_provider
        url = _ollama_url() if provider == "ollama" else settings.openai_base_url
        logger.error("LLM unreachable (%s @ %s): %s", provider, url, exc)
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail=(
                f"LLM-Service nicht erreichbar ({provider} @ {url}). "
                f"Bitte Verbindung und Konfiguration prüfen."
            ),
        )
    except httpx.HTTPStatusError as exc:
        logger.error("LLM HTTP error %s: %s", exc.response.status_code, exc.response.text[:200])
        from fastapi import HTTPException
        raise HTTPException(
            status_code=502,
            detail=f"LLM antwortete mit Fehler {exc.response.status_code}: {exc.response.text[:200]}",
        )

    suggestions = _parse_llm_response(raw)

    # Validate and repair character offsets
    return [_validate_char_offsets(s, report_text) for s in suggestions]
