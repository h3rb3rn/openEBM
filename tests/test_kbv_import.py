"""
Unit tests for the KBV PDF parsing heuristics — the pure-function pieces
that don't need a real PDF or network access. The full parse_kbv_pdf()
pipeline was validated by hand against the actual current KBV catalog
(2225 pages, 3173 GOPs extracted) during development; these tests guard
the specific regex/classification rules so a future refactor doesn't
silently regress them.
"""
from src.app.services.kbv_import import (
    _AGE_MAX_RE,
    _AGE_MIN_RE,
    _EURO_RE,
    _EXCLUSION_SENTENCE_RE,
    _POINTS_RE,
    _TIME_UNIT_RE,
    _is_italic,
    _resolve_code_list,
)


class TestIsItalic:
    def test_italic_fontname(self):
        assert _is_italic("Arial-ItalicMT")

    def test_oblique_fontname(self):
        assert _is_italic("Helvetica-Oblique")

    def test_regular_fontname_not_italic(self):
        assert not _is_italic("ArialMT")

    def test_bold_but_not_italic(self):
        assert not _is_italic("Arial-BoldMT")


class TestResolveCodeList:
    def test_single_code(self):
        assert _resolve_code_list("01100") == ["01100"]

    def test_multiple_codes(self):
        codes = _resolve_code_list("01100, 01101 und 01102")
        assert codes == ["01100", "01101", "01102"]

    def test_code_range_expands(self):
        codes = _resolve_code_list("01100 bis 01103")
        assert codes == ["01100", "01101", "01102", "01103"]

    def test_runaway_range_capped(self):
        """A mis-parsed range spanning thousands of codes must not silently
        expand into a huge, meaningless exclusion list."""
        codes = _resolve_code_list("01100 bis 09999")
        assert codes == []

    def test_no_codes_returns_empty(self):
        assert _resolve_code_list("keine Ziffern hier") == []


class TestExclusionSentenceRegex:
    def test_matches_standard_exclusion_sentence(self):
        text = (
            "Die Gebührenordnungsposition 01100 ist nicht neben der "
            "Gebührenordnungsposition 01101 berechnungsfähig."
        )
        m = _EXCLUSION_SENTENCE_RE.search(text)
        assert m is not None
        assert "01101" in m.group(1)

    def test_matches_plural_variant_with_multiple_codes(self):
        text = (
            "Die Gebührenordnungspositionen 01100 ist nicht neben den "
            "Gebührenordnungspositionen 01101, 01102 berechnungsfähig."
        )
        m = _EXCLUSION_SENTENCE_RE.search(text)
        assert m is not None
        assert "01101" in m.group(1) and "01102" in m.group(1)

    def test_no_match_on_unrelated_sentence(self):
        text = "Diese Leistung wird einmal im Behandlungsfall berechnet."
        assert _EXCLUSION_SENTENCE_RE.search(text) is None


class TestAgeRestrictionRegex:
    def test_age_max(self):
        m = _AGE_MAX_RE.search("Leistung bis zum vollendeten 18. Lebensjahr")
        assert m is not None
        assert m.group(1) == "18"

    def test_age_min(self):
        m = _AGE_MIN_RE.search("Leistung ab vollendetem 70. Lebensjahr")
        assert m is not None
        assert m.group(1) == "70"

    def test_no_age_restriction(self):
        assert _AGE_MAX_RE.search("keine Altersangabe") is None
        assert _AGE_MIN_RE.search("keine Altersangabe") is None


class TestValueRegexes:
    def test_euro_value(self):
        m = _EURO_RE.search("Bewertung: 24,97 € je Fall")
        assert m is not None
        assert m.group(1) == "24,97"

    def test_points_value(self):
        m = _POINTS_RE.search("196 Punkte")
        assert m is not None
        assert m.group(1) == "196"

    def test_time_unit(self):
        m = _TIME_UNIT_RE.search("je vollendete 10 Minuten")
        assert m is not None
        assert m.group(1) == "10"
