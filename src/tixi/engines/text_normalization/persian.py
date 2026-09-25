"""Persian (and general) text normalisation.

This is the layer that turns *raw* text typed by a user into text a
text-to-speech model can pronounce, and it is also reused by the search /
history features when they need comparable strings.

Everything here is deterministic and rule-based on purpose: linguistic
normalisation (character folding, spacing, punctuation, numerals) is *not*
diacritization.  Neural/lexical vowel restoration lives in
:mod:`tixi.engines.diacritization`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Sequence

from .numbers import (
    NumberRules,
    convert_digits,
    is_persian_text,
    to_ascii_digits,
    verbalise_text,
)

if TYPE_CHECKING:  # pragma: no cover - avoids a circular import at runtime
    from .pronunciation import PronunciationDictionary

# ---------------------------------------------------------------------------
# Character tables
# ---------------------------------------------------------------------------
#: Zero-width non-joiner — the Persian "half space".  Must survive normalisation.
ZWNJ = "\u200c"
ZWJ = "\u200d"
ZERO_WIDTH_SPACE = "\u200b"
NO_BREAK_SPACE = "\u00a0"
ARABIC_TATWEEL = "\u0640"
BOM = "\ufeff"

#: Arabic → Persian letter folding (applied when ``fold_arabic`` is enabled).
ARABIC_TO_PERSIAN = {
    "\u064a": "\u06cc",  # ARABIC YEH        -> FARSI YEH
    "\u0649": "\u06cc",  # ALEF MAKSURA      -> FARSI YEH
    "\u0643": "\u06a9",  # ARABIC KAF        -> KEHEH
    "\u0629": "\u0647",  # TEH MARBUTA       -> HEH
    "\u06c0": "\u0647",  # HEH WITH YEH ABOVE-> HEH
    "\u06c2": "\u0647",  # HEH GOAL + hamza  -> HEH
    "\u06d2": "\u06cc",  # YEH BARREE        -> FARSI YEH
    "\u06d3": "\u06cc",  # YEH BARREE+hamza  -> FARSI YEH
    "\u06c1": "\u0647",  # HEH GOAL          -> HEH
    "\u0624": "\u0648",  # WAW WITH HAMZA    -> WAW (configurable)
    "\u0626": "\u06cc",  # YEH WITH HAMZA    -> FARSI YEH
}

#: Combining marks (harakat) — stripped by ``strip_diacritics``.
DIACRITIC_CHARS = "\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0653\u0654\u0655\u0670\u0656\u0657\u0658\u06df\u06e0\u06e1\u06e2"

#: Persian letters that must never carry a "tatweel" or a stray hamza.
_PERSIAN_LETTERS = re.compile(r"[\u0621-\u064a\u066e-\u06d3\u06fa-\u06fc]")

_PERSIAN_DIGITS = set("۰۱۲۳۴۵۶۷۸۹")
_ARABIC_DIGITS = set("٠١٢٣٤٥٦٧٨٩")

#: English punctuation → Persian equivalent when the surrounding text is Persian.
PUNCTUATION_MAP_FA = {
    "?": "؟",
    ";": "؛",
    ",": "،",
}

_PERSIAN_PUNCT = "،؛؟«»٫…"
_LATIN_PUNCT = ",;?"

# Common Persian words that are accidentally written joined to the next word.
COMMON_ZWNJ_WORDS = {
    "می": ("می", True),      # می رود -> می‌رود
    "نمی": ("نمی", True),
    "ها": ("‌ها", False),    # کتاب ها -> کتاب‌ها
    "هایی": ("‌هایی", False),
    "های": ("‌های", False),
    "تر": ("‌تر", False),
    "ترین": ("‌ترین", False),
}

_RE_MULTISPACE = re.compile(r"[ \t\u00a0\u2000-\u200a]{2,}")
_RE_SPACE_BEFORE_PUNCT = re.compile(r"\s+([،؛؟!.٫:»\]\})])")
_RE_SPACE_AFTER_OPEN = re.compile(r"([«\[\({])\s+")
_RE_SPACE_AROUND_ZWNJ = re.compile(rf"\s*{ZWNJ}\s*")
_RE_MULTI_PUNCT = re.compile(r"([،؛.!؟])\1+")
_RE_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z0-9'’\-_.]*")
_RE_PARAGRAPH = re.compile(r"\n\s*\n+")
_RE_MI_VERB = re.compile(
    r"(?<![\u0600-\u06FF])"
    r"(می|نمی)\s+(?=[\u0600-\u06FF]{2,})"
)
_RE_PLURAL_HA = re.compile(r"([\u0600-\u06FF]{3,})\s+ها(?![ا-ی])")
_RE_COMPARATIVE = re.compile(r"([\u0600-\u06FF]{3,})\s+تر(?![ا-ی])")

#: Words after which an Ezafe kasra is (almost) certain in written Persian.
#: Used by the diacritizer's smart mode; kept here so both stages agree.
EZAFE_TRIGGERS = frozenset(
    {
        "کتاب",
        "خانه",
        "مدرسه",
        "دانشگاه",
        "شهر",
        "کشور",
        "روز",
        "شب",
        "صبح",
        "ظهر",
        "عصر",
        "سال",
        "ماه",
        "هفته",
        "ساعت",
        "دقیقه",
        "دقیقه‌ی",
        "مدیر",
        "معلم",
        "استاد",
        "دکتر",
        "مهندس",
        "رئیس",
        "برنامه",
        "پروژه",
        "سیستم",
        "نرم‌افزار",
        "سخت‌افزار",
        "زبان",
        "فرهنگ",
        "تاریخ",
        "داستان",
        "فیلم",
        "موسیقی",
        "آهنگ",
        "تصویر",
        "رنگ",
        "نوع",
        "شکل",
        "اندازه",
        "مقدار",
        "تعداد",
        "کیفیت",
        "سرعت",
    }
)


@dataclass
class NormalizationOptions:
    """Every switch the normaliser understands (mirrors the Settings UI)."""

    fold_arabic_characters: bool = True
    normalise_zwnj: bool = True
    fix_spacing: bool = True
    persian_punctuation: bool = True
    convert_digits_to_persian: bool = True
    strip_existing_diacritics: bool = False
    verbalise_numbers: bool = True
    number_rules: NumberRules = field(default_factory=NumberRules)
    expand_abbreviations: bool = True
    apply_dictionary: bool = True
    collapse_whitespace: bool = True
    insert_zwnj_for_mi: bool = True
    insert_zwnj_for_plural: bool = False
    remove_tatweel: bool = True
    remove_control_characters: bool = True


@dataclass
class NormalizationReport:
    """Human-readable summary of what the pipeline changed."""

    original: str = ""
    normalised: str = ""
    changes: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    dictionary_hits: list[str] = field(default_factory=list)
    was_persian: bool = False

    @property
    def changed_count(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> str:
        if not self.counts:
            return "No normalisation changes were needed."
        parts = [f"{value}× {key.replace('_', ' ')}" for key, value in self.counts.items() if value]
        return ", ".join(parts) if parts else "No normalisation changes were needed."


class PersianTextNormalizer:
    """Configurable, deterministic Persian text normaliser."""

    def __init__(
        self,
        options: NormalizationOptions | None = None,
        dictionary: PronunciationDictionary | None = None,
    ) -> None:
        self.options = options or NormalizationOptions()
        if dictionary is None:
            from .pronunciation import PronunciationDictionary as _Dictionary

            dictionary = _Dictionary()
        self.dictionary = dictionary

    # -- public API ---------------------------------------------------------
    def normalise(self, text: str, *, report: bool = False) -> str | NormalizationReport:
        result = self.normalise_with_report(text)
        return result if report else result.normalised

    def normalise_with_report(self, text: str) -> NormalizationReport:
        options = self.options
        report = NormalizationReport(original=text or "")
        if not text:
            report.normalised = ""
            return report

        persian = is_persian_text(text)
        report.was_persian = persian
        working = unicodedata.normalize("NFC", text)

        if options.remove_control_characters:
            working = self._strip_control_characters(working, report)
        working = working.replace(BOM, "").replace(NO_BREAK_SPACE, " ")

        if options.remove_tatweel:
            if ARABIC_TATWEEL in working:
                working = working.replace(ARABIC_TATWEEL, "")
                report.counts["tatweel_removed"] = 1

        if options.fold_arabic_characters and persian:
            working = self._fold_arabic(working, report)

        if options.strip_existing_diacritics:
            stripped = _strip_diacritics(working)
            if stripped != working:
                report.counts["diacritics_stripped"] = 1
            working = stripped

        if options.normalise_zwnj:
            working = self._normalise_zwnj(working, report)

        if options.insert_zwnj_for_mi and persian:
            working = self._insert_mi_zwnj(working, report)
        if options.insert_zwnj_for_plural and persian:
            working = self._insert_plural_zwnj(working, report)

        if options.fix_spacing:
            working = self._fix_spacing(working, report)

        if options.persian_punctuation and persian:
            working = self._persian_punctuation(working, report)

        if options.apply_dictionary and len(self.dictionary):
            working = self.dictionary.apply(working, report=report)

        if options.expand_abbreviations and options.number_rules.expand_abbreviations:
            working = self._expand_known_abbreviations(working, persian, report)

        if options.verbalise_numbers:
            rules = options.number_rules
            if persian:
                rules = NumberRules(**{**rules.__dict__, "language": "fa"})
            before = working
            working = verbalise_text(working, rules)
            if working != before:
                report.counts["numbers_verbalised"] = 1

        if options.convert_digits_to_persian and persian and options.verbalise_numbers is False:
            working = convert_digits(working, "fa")

        if options.collapse_whitespace:
            working = self._collapse_whitespace(working)

        report.normalised = working.strip()
        return report

    # -- stages -------------------------------------------------------------
    def _strip_control_characters(self, text: str, report: NormalizationReport) -> str:
        cleaned = "".join(
            char
            for char in text
            if char in "\n\r\t" or unicodedata.category(char) not in {"Cc", "Cf"}
        )
        # ZWNJ/ZWJ are Cf but must survive.
        cleaned = cleaned.replace(ZWJ, "")
        if cleaned != text:
            report.counts["control_characters_removed"] = 1
        return cleaned

    def _fold_arabic(self, text: str, report: NormalizationReport) -> str:
        folded = []
        changed = 0
        for char in text:
            replacement = ARABIC_TO_PERSIAN.get(char)
            if replacement and replacement != char:
                folded.append(replacement)
                changed += 1
            else:
                folded.append(char)
        if changed:
            report.counts["arabic_letters_folded"] = changed
            report.changes.append("Arabic ي/ك/ة folded to Persian ی/ک/ه")
        return "".join(folded)

    def _normalise_zwnj(self, text: str, report: NormalizationReport) -> str:
        before = text
        text = text.replace(ZERO_WIDTH_SPACE, ZWNJ)
        text = _RE_SPACE_AROUND_ZWNJ.sub(ZWNJ, text)
        text = text.replace(ZWNJ + ZWNJ, ZWNJ)
        # A ZWNJ between two non-letters or at a word boundary is meaningless.
        text = re.sub(rf"(?<![\u0600-\u06FF]){ZWNJ}", "", text)
        text = re.sub(rf"{ZWNJ}(?![\u0600-\u06FF])", "", text)
        if text != before:
            report.counts["zwnj_normalised"] = 1
        return text

    def _insert_mi_zwnj(self, text: str, report: NormalizationReport) -> str:
        before = text
        text = _RE_MI_VERB.sub(lambda m: f"{m.group(1)}{ZWNJ}", text)
        if text != before:
            report.counts["mi_zwnj_inserted"] = 1
        return text

    def _insert_plural_zwnj(self, text: str, report: NormalizationReport) -> str:
        before = text
        text = _RE_PLURAL_HA.sub(lambda m: f"{m.group(1)}{ZWNJ}ها", text)
        text = _RE_COMPARATIVE.sub(lambda m: f"{m.group(1)}{ZWNJ}تر", text)
        if text != before:
            report.counts["plural_zwnj_inserted"] = 1
        return text

    def _fix_spacing(self, text: str, report: NormalizationReport) -> str:
        before = text
        text = _RE_SPACE_BEFORE_PUNCT.sub(r"\1", text)
        text = _RE_SPACE_AFTER_OPEN.sub(r"\1", text)
        text = _RE_MULTISPACE.sub(" ", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        if text != before:
            report.counts["spacing_fixed"] = 1
        return text

    def _persian_punctuation(self, text: str, report: NormalizationReport) -> str:
        out: list[str] = []
        changed = 0
        for index, char in enumerate(text):
            if char in PUNCTUATION_MAP_FA:
                # Only convert when the neighbouring context is Persian script.
                window = text[max(0, index - 12) : index] + text[index + 1 : index + 13]
                if is_persian_text(window) or not window.strip():
                    out.append(PUNCTUATION_MAP_FA[char])
                    changed += 1
                    continue
            out.append(char)
        text = "".join(out)
        text = _RE_MULTI_PUNCT.sub(r"\1", text)
        if changed:
            report.counts["punctuation_converted"] = changed
        return text

    def _expand_known_abbreviations(
        self, text: str, persian: bool, report: NormalizationReport
    ) -> str:
        from .numbers import ABBREVIATIONS_FA

        changed = 0
        if persian:
            for abbreviation, expansion in ABBREVIATIONS_FA.items():
                if abbreviation == expansion or len(abbreviation) > 4:
                    continue
                pattern = re.compile(
                    rf"(?<![\w\u0600-\u06FF]){re.escape(abbreviation)}(?![\w\u0600-\u06FF])"
                )
                new_text, count = pattern.subn(expansion, text)
                if count:
                    changed += count
                    text = new_text
        if changed:
            report.counts["abbreviations_expanded"] = changed
        return text

    def _collapse_whitespace(self, text: str) -> str:
        paragraphs = [part for part in _RE_PARAGRAPH.split(text)]
        cleaned = []
        for paragraph in paragraphs:
            lines = [line.strip() for line in paragraph.splitlines()]
            kept = [line for line in lines if line]
            cleaned.append("\n".join(kept))
        return "\n\n".join(part for part in cleaned if part)


# ---------------------------------------------------------------------------
# Module-level helpers reused across the app
# ---------------------------------------------------------------------------
def _strip_diacritics(text: str) -> str:
    return "".join(char for char in text if char not in DIACRITIC_CHARS)


def strip_diacritics(text: str) -> str:
    """Public helper: remove all Persian/Arabic short-vowel marks."""
    return _strip_diacritics(text)


def has_diacritics(text: str) -> bool:
    return any(char in DIACRITIC_CHARS for char in text)


def fold_arabic_characters(text: str) -> str:
    return "".join(ARABIC_TO_PERSIAN.get(char, char) for char in text)


def normalise_zwnj(text: str) -> str:
    return _RE_SPACE_AROUND_ZWNJ.sub(ZWNJ, text.replace(ZERO_WIDTH_SPACE, ZWNJ))


def collapse_whitespace(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def persian_only_letters(text: str) -> str:
    """Remove everything that is neither a Persian letter nor a space."""
    return "".join(
        char
        for char in text
        if _PERSIAN_LETTERS.match(char) or char == " " or char == ZWNJ
    )


def sort_dictionary_entries(entries: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Longest-first ordering so multi-word rules win over single words."""
    return sorted(entries, key=lambda item: (-len(item[0]), item[0]))


def strip_rtl_markers(text: str) -> str:
    return text.replace("\u200e", "").replace("\u200f", "").replace("\u061c", "")


def wrap_rtl_safe(text: str) -> str:
    """Wrap mixed content so Qt renders the base direction correctly.

    The Unicode isolate characters keep Persian sentences right-to-left while
    embedded Latin words (brand names, code, URLs) stay left-to-right.
    """
    return f"\u2067{text}\u2069"


def join_sentences(sentences: Sequence[str], separator: str = " ") -> str:
    return separator.join(part.strip() for part in sentences if part.strip())


def text_statistics(text: str) -> dict[str, int]:
    """Counts shown in the editors' status bars."""
    ascii_text = to_ascii_digits(text)
    words = [chunk for chunk in ascii_text.split() if chunk]
    persian_words = [
        word
        for word in words
        if any("\u0600" <= char <= "\u06ff" for char in word)
    ]
    return {
        "characters": len(text),
        "characters_no_spaces": len(re.sub(r"\s", "", text)),
        "words": len(words),
        "sentences": len([part for part in re.split(r"[.!?؟।\n]+", text) if part.strip()]),
        "paragraphs": len([part for part in re.split(r"\n\s*\n", text) if part.strip()]),
        "persian_words": len(persian_words),
        "digits": len(re.findall(r"[0-9۰-۹٠-٩]", text)),
    }
