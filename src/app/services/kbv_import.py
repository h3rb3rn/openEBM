"""
KBV EBM catalog PDF import: downloads the official quarterly PDF and
extracts GOP entries (code, description, points, exclusions, age
restrictions) heuristically.

This is a best-effort text/layout parser against an unstructured legal
document, not an official machine-readable feed from the KBV. It is
deliberately used in a preview-then-commit flow (see import_service.py /
admin API) rather than writing straight to Neo4j/ChromaDB, because a
silent mis-parse would be billing-relevant. Known limitations are
attached to each entry as `warnings` and surfaced in the admin UI so a
human reviews the numbers before they land in the validation graph.

Key structural insight used here: in the KBV PDF, GOP codes that start a
new entry are set in the regular (non-italic) font at the left content
margin, while GOP codes referenced inside "nicht neben ... berechnungs-
fähig" exclusion sentences are set in italics. That font distinction is
far more reliable than pattern-matching on line position alone, which
produces many false positives from codes referenced mid-paragraph.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO

import httpx
import pdfplumber

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_URL_SETTING_KEY = "ebm_catalog_source_url"
DEFAULT_SOURCE_URL = "https://www.kbv.de/documents/praxis/abrechnung/ebm/2026-3-ebm.pdf"

MAX_PDF_BYTES = 60 * 1024 * 1024  # 60 MB safety cap
LEFT_MARGIN_MIN, LEFT_MARGIN_MAX = 65, 90

_CODE_TOKEN_RE = re.compile(r"^\d{5}$")
_CODE_OR_RANGE_RE = re.compile(r"(\d{5})(?:\s+bis\s+(\d{5}))?")
_EURO_RE = re.compile(r"([\d.,]+)\s*€")
_POINTS_RE = re.compile(r"([\d.,]+)\s*Punkte")
_EXCLUSION_SENTENCE_RE = re.compile(
    r"Die Gebührenordnungsposition(?:en)? \d{5}[a-zA-Zäöü ,]*? ist "
    r"(?:am Behandlungstag |im Behandlungsfall |im Arztfall |im Arztgruppenfall )?"
    r"nicht neben (?:den |der )?Gebührenordnungsposition(?:en)?\s+(.+?)\s+berechnungsfähig\."
)
_AGE_MAX_RE = re.compile(r"bis (?:zum|zur) vollendeten (\d{1,3})\.\s*Lebensjahr")
_AGE_MIN_RE = re.compile(r"ab (?:(?:dem |Beginn des )vollendeten|vollendetem) (\d{1,3})\.\s*Lebensjahr")
_TIME_UNIT_RE = re.compile(r"je (?:vollendete|angefangene) (\d{1,3}) Minuten")


@dataclass
class ParsedCatalog:
    gops: list[dict] = field(default_factory=list)
    pages_processed: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def stats(self) -> dict:
        total = len(self.gops)
        missing_points = sum(1 for g in self.gops if not g["value_points"])
        with_exclusions = sum(1 for g in self.gops if g["exclusions"])
        with_age = sum(1 for g in self.gops if g["age_restriction_min"] or g["age_restriction_max"])
        needs_review = sum(1 for g in self.gops if g["warnings"])
        return {
            "total_gops": total,
            "missing_points_value": missing_points,
            "with_exclusions": with_exclusions,
            "with_age_restriction": with_age,
            "needs_review": needs_review,
            "pages_processed": self.pages_processed,
        }


async def fetch_kbv_pdf(url: str, timeout: float = 120.0) -> bytes:
    """Download the catalog PDF. Raises on non-2xx, non-PDF, or oversized responses."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "pdf" not in content_type.lower():
                raise ValueError(f"Erwartete ein PDF, erhielt Content-Type '{content_type}'")

            chunks = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > MAX_PDF_BYTES:
                    raise ValueError(f"PDF überschreitet die Größenbegrenzung ({MAX_PDF_BYTES // 1024 // 1024} MB)")
                chunks.append(chunk)
            return b"".join(chunks)


def _is_italic(fontname: str) -> bool:
    return "Italic" in fontname or "Oblique" in fontname


def _extract_lines(pdf: pdfplumber.PDF):
    """Yield (top, words[]) per visual line, word = {'text', 'fontname', 'x0'}. Skips header/footer bands."""
    for page in pdf.pages:
        words = page.extract_words(extra_attrs=["fontname"])
        words = [w for w in words if 30 < w["top"] < 755]
        lines: dict[int, list] = {}
        for w in words:
            lines.setdefault(round(w["top"]), []).append(w)
        for top in sorted(lines):
            yield sorted(lines[top], key=lambda w: w["x0"])


def _resolve_code_list(s: str) -> list[str]:
    codes = []
    for m in _CODE_OR_RANGE_RE.finditer(s):
        a, b = m.group(1), m.group(2)
        if b:
            lo, hi = int(a), int(b)
            if hi - lo <= 200:  # sanity cap against runaway mis-parsed ranges
                codes.extend(str(c).zfill(5) for c in range(lo, hi + 1))
        else:
            codes.append(a)
    return codes


def parse_kbv_pdf(pdf_bytes: bytes) -> ParsedCatalog:
    result = ParsedCatalog()

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        result.pages_processed = len(pdf.pages)
        all_lines = list(_extract_lines(pdf))

    header_indices = [
        i for i, words in enumerate(all_lines)
        if words
        and _CODE_TOKEN_RE.match(words[0]["text"])
        and not _is_italic(words[0]["fontname"])
        and LEFT_MARGIN_MIN <= words[0]["x0"] <= LEFT_MARGIN_MAX
    ]

    if not header_indices:
        result.warnings.append("Keine GOP-Einträge erkannt — Dokumentlayout weicht vom erwarteten Format ab.")
        return result

    seen_codes: set[str] = set()
    for i, start in enumerate(header_indices):
        end = header_indices[i + 1] if i + 1 < len(header_indices) else len(all_lines)
        block = all_lines[start:end]
        code = block[0][0]["text"]
        if code in seen_codes:
            continue  # keep first occurrence only
        seen_codes.add(code)

        plain_words, italic_words = [], []
        for words in block:
            for w in words:
                (italic_words if _is_italic(w["fontname"]) else plain_words).append(w["text"])
        plain_text = " ".join(plain_words)
        italic_text = " ".join(italic_words)

        header_words = [w["text"] for w in block[0][1:]]
        desc = re.sub(r"[\d.,]+\s*€.*$", "", " ".join(header_words)).strip()
        if not desc and len(block) > 1:
            desc = " ".join(w["text"] for w in block[1])[:200]

        euro_m = _EURO_RE.search(plain_text[:400])
        points_m = _POINTS_RE.search(plain_text[:500])
        points = float(points_m.group(1).replace(".", "").replace(",", ".")) if points_m else None

        exclusions: set[str] = set()
        section_level = False
        for exm in _EXCLUSION_SENTENCE_RE.finditer(italic_text):
            code_list_str = exm.group(1)
            if "Abschnitt" in code_list_str:
                section_level = True
                continue
            exclusions.update(_resolve_code_list(code_list_str))
        exclusions.discard(code)

        age_max_m = _AGE_MAX_RE.search(italic_text) or _AGE_MAX_RE.search(plain_text)
        age_min_m = _AGE_MIN_RE.search(italic_text) or _AGE_MIN_RE.search(plain_text)
        time_m = _TIME_UNIT_RE.search(plain_text)

        entry_warnings = []
        if points is None:
            entry_warnings.append("no_points_value")
        if section_level:
            entry_warnings.append("section_level_exclusion_unresolved")

        result.gops.append({
            "code": code,
            "description": desc[:250],
            "chapter": code[:2],
            "chapter_title": "",
            "value_points": points or 0.0,
            "value_euro": euro_m.group(1) if euro_m else None,
            "insurance_types": ["GKV"],
            "exclusions": sorted(exclusions),
            "age_restriction_min": int(age_min_m.group(1)) if age_min_m else None,
            "age_restriction_max": int(age_max_m.group(1)) if age_max_m else None,
            "gender_restriction": None,
            "time_value_minutes": int(time_m.group(1)) if time_m else 0,
            "valid_from": date.today().isoformat(),
            "valid_until": None,
            "warnings": entry_warnings,
        })

    logger.info(
        "KBV PDF parsed: %d pages, %d GOPs, %d flagged for review",
        result.pages_processed, len(result.gops), result.stats["needs_review"],
    )
    return result
