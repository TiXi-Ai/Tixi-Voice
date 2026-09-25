"""Number, date and time verbalisation (Persian and English)."""

from __future__ import annotations

from tixi.engines.text_normalization.numbers import (
    NumberRules,
    number_to_words_fa,
    to_ascii_digits,
    verbalise_text,
)


def test_digits_are_converted_to_ascii() -> None:
    assert to_ascii_digits("۱۲۳۴") == "1234"


def test_basic_cardinals() -> None:
    assert "سه" in number_to_words_fa(3)
    assert "صد" in number_to_words_fa(100)


def test_time_is_spoken_once() -> None:
    """Regression: 'ساعت ۱۰:۳۰' used to become 'ساعت ساعت …'."""
    result = verbalise_text("ساعت ۱۰:۳۰", NumberRules(language="fa"))
    assert result.count("ساعت") == 1
    assert "دقیقه" in result


def test_percent_and_units() -> None:
    result = verbalise_text("۳۵ درصد", NumberRules(language="fa"))
    assert "درصد" in result and "۳۵" not in result


def test_date_is_verbalised() -> None:
    result = verbalise_text("۱۴۰۳/۰۵/۱۲", NumberRules(language="fa"))
    assert any(month in result for month in ("مرداد", "مردادماه"))


def test_english_numbers() -> None:
    result = verbalise_text("42 apples and 10:30", NumberRules(language="en"))
    assert "forty" in result.lower() or "four" in result.lower()


def test_empty_text_is_safe() -> None:
    assert verbalise_text("") == ""
