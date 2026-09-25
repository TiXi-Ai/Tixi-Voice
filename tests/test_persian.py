"""Persian normalisation: folding, ZWNJ, spacing, statistics."""

from __future__ import annotations

from tixi.engines.text_normalization.persian import (
    ZWNJ,
    NormalizationOptions,
    PersianTextNormalizer,
    fold_arabic_characters,
    normalise_zwnj,
    text_statistics,
)


def test_arabic_letters_are_folded() -> None:
    assert fold_arabic_characters("كتاب ي") == "کتاب ی"
    assert fold_arabic_characters("علي") == "علی"


def test_persian_text_is_left_alone() -> None:
    text = "سلام دنیا"
    assert fold_arabic_characters(text) == text


def test_zwnj_repair_does_not_invent_spaces() -> None:
    # a space hugging a real ZWNJ collapses onto it, and ZWSP is promoted to ZWNJ
    assert normalise_zwnj("می‌  روم") == "می‌روم"
    assert normalise_zwnj("می​روم") == "می‌روم"
    repaired = PersianTextNormalizer().normalise("می  روم")
    assert repaired == "می‌روم"  # the pipeline repairs the gap
    assert "  " not in repaired


def test_statistics_counts_persian_words_and_marks() -> None:
    stats = text_statistics("سلام دنیا\n۲ کلمه")
    assert stats["words"] >= 3
    assert stats["digits"] >= 1
    assert stats["characters"] == len("سلام دنیا\n۲ کلمه")


def test_zwnj_constant_is_the_real_character() -> None:
    assert ZWNJ == "\u200c"


def test_normalise_keeps_latin_separate() -> None:
    normalizer = PersianTextNormalizer()
    text = normalizer.normalise("Hello دنیا 123")
    assert isinstance(text, str)
    assert "Hello" in text and "دنیا" in text  # Latin runs are never touched
    assert "صد و بیست و سه" in text  # digits are verbalised for speech
    report = normalizer.normalise("Hello دنیا 123", report=True)
    assert report.normalised == text
    assert report.was_persian is True  # the line contains Persian, so it is treated as such
    assert report.counts.get("numbers_verbalised") == 1
    quiet = PersianTextNormalizer(options=NormalizationOptions(verbalise_numbers=False))
    assert quiet.normalise("Hello world 123") == "Hello world 123"  # opt-out leaves digits alone
