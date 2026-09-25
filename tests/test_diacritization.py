"""Diacritization: lexicon behaviour and the safety net around marks."""

from __future__ import annotations

from tixi.engines.diacritization.lexicon import LexiconDiacritizer, ezafe_needed
from tixi.engines.diacritization.service import DiacritizationService, coverage_report


def test_lexicon_diacritizes_a_known_word() -> None:
    diacritizer = LexiconDiacritizer()
    result = diacritizer.diacritize("کتاب من روی میز است")
    assert result.engine_id == "lexicon-fa"
    assert result.text != result.source_text
    assert result.marks_added > 0
    assert "کِتاب" in result.text



def test_lexicon_reports_itself_as_approximate() -> None:
    diacritizer = LexiconDiacritizer()
    assert diacritizer.is_neural is False
    assert "approximate" in diacritizer.display_name.lower()
    assert diacritizer.describe()["neural"] is False
    assert diacritizer.diacritize("کتاب").warnings  # the UI must be told it is approximate


def test_lexicon_folds_arabic_letter_forms() -> None:
    diacritizer = LexiconDiacritizer()
    assert diacritizer.lookup("\u0643\u062a\u0627\u0628") == diacritizer.lookup("کتاب")


def test_lexicon_never_drops_letters() -> None:
    diacritizer = LexiconDiacritizer()
    source = "کتاب من روی میز است"
    result = diacritizer.diacritize(source)
    stripped = "".join(char for char in result.text if char not in "\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652")
    assert stripped == source


def test_ezafe_detection() -> None:
    assert isinstance(ezafe_needed("کتاب", "من"), bool)
    assert isinstance(ezafe_needed("کتاب", "من", context="کتاب من"), bool)
    assert ezafe_needed("کتاب", "من") is False  # never guessed for a plain noun pair


def test_service_prefers_the_lexicon_when_no_model_is_installed() -> None:
    service = DiacritizationService()
    assert service.neural is None
    assert service.describe()["active"] == "lexicon-fa"


def test_coverage_report_counts_words() -> None:
    report = coverage_report("سلام دنیا", {})
    assert report["words"] >= 2
    assert 0.0 <= float(report.get("coverage", 0.0)) <= 1.0
