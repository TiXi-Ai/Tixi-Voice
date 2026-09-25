"""Pronunciation dictionary and custom replacement rules.

Two kinds of user data influence pronunciation:

* **Replacement rules** (``word -> replacement``) — used by the normalisation
  pipeline for things like ``AI`` -> ``هوش مصنوعی`` or ``Dr.`` -> ``دکتر``.
* **Pronunciation overrides** — an explicit diacritized form and/or IPA string
  for a word, used by the diacritizer and (when the engine supports it) as an
  espeak-ng IPA override.

Both are stored in SQLite by :class:`~tixi.storage.library_repository.DictionaryRepository`;
this module is the in-memory engine-side view, and ships with a small built-in
dictionary of Persian loanwords/abbreviations so the feature is useful before
anyone edits it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Sequence

# NOTE: defined locally on purpose — importing it from ``.persian`` would create an
# import cycle (``persian`` imports this module for its dictionary support).
ZWNJ = "\u200c"

if TYPE_CHECKING:  # pragma: no cover
    from ..text_normalization.persian import NormalizationReport

#: Built-in replacements that improve Persian TTS for very common strings.
BUILTIN_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Wi-Fi", "وای‌فای"),
    ("wifi", "وای‌فای"),
    ("WiFi", "وای‌فای"),
    ("AI", "هوش مصنوعی"),
    ("GPU", "جی‌پی‌یو"),
    ("CPU", "سی‌پی‌یو"),
    ("RAM", "رم"),
    ("USB", "یو‌اس‌بی"),
    ("PDF", "پی‌دی‌اف"),
    ("URL", "یو‌آر‌ال"),
    ("OK", "اوکی"),
    ("email", "ایمیل"),
    ("Email", "ایمیل"),
    ("E-mail", "ایمیل"),
    ("online", "آنلاین"),
    ("Online", "آنلاین"),
    ("offline", "آفلاین"),
    ("software", "نرم‌افزار"),
    ("hardware", "سخت‌افزار"),
    ("COVID-19", "کووید نوزده"),
    ("http://", "اچ‌تی‌تی‌پی دو نقطه اسلش اسلش "),
    ("https://", "اچ‌تی‌تی‌پی‌اس دو نقطه اسلش اسلش "),
    ("www.", "دبلیو دبلیو دبلیو نقطه "),
    (".com", " نقطه کام"),
    (".ir", " نقطه آی‌آر"),
    (".org", " نقطه ارگ"),
    (".net", " نقطه نت"),
    ("&", " و "),
    ("@", " ات "),
    ("#", " شماره "),
    ("+", " به‌علاوه "),
    ("=", " مساوی "),
    ("℃", " درجه سانتی‌گراد"),
    ("℉", " درجه فارنهایت"),
    ("×", " ضربدر "),
    ("÷", " تقسیم بر "),
    ("°", " درجه "),
    ("%", " درصد "),
    ("±", " مثبت منهای "),
    ("→", " به "),
    ("→", " به "),
    ("km/h", "کیلومتر بر ساعت"),
)

#: Words that are always written with an Ezafe (kasra) in formal Persian.
EZAFE_WORDS: frozenset[str] = frozenset()

#: Verb/noun endings that take a specific short vowel. Used by the heuristic
#: engine only; the neural engine learns these from data.
KASRA_ENDINGS = ("ه", "ی")
FATHA_PREFIXES = ("ا",)


@dataclass
class PronunciationRule:
    word: str
    replacement: str
    case_sensitive: bool = False
    priority: int = 0
    language: str = "fa"

    def pattern(self) -> re.Pattern[str]:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        # Word-boundary aware: Persian words must not match inside larger words.
        return re.compile(
            rf"(?<![\w\u0600-\u06FF]){re.escape(self.word)}(?![\w\u0600-\u06FF])", flags
        )


@dataclass
class PronunciationDictionary:
    """Ordered replacement rules applied before synthesis."""

    rules: list[PronunciationRule] = field(default_factory=list)
    use_builtin: bool = True
    overrides: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.use_builtin:
            for word, replacement in BUILTIN_REPLACEMENTS:
                self.add(word, replacement, priority=-10, language="*")

    # -- construction -------------------------------------------------------
    def add(
        self,
        word: str,
        replacement: str,
        *,
        case_sensitive: bool = False,
        priority: int = 0,
        language: str = "fa",
        replace_existing: bool = True,
    ) -> None:
        if not word:
            return
        if replace_existing:
            self.rules = [rule for rule in self.rules if rule.word != word]
        self.rules.append(
            PronunciationRule(word, replacement, case_sensitive, priority, language)
        )
        self._sort()

    def extend(self, entries: Iterable[tuple[str, str, bool]]) -> None:
        for word, replacement, case_sensitive in entries:
            # User entries win over the built-ins with the same key.
            self.add(word, replacement, case_sensitive=case_sensitive, priority=10)

    def set_overrides(self, overrides: dict[str, dict[str, str]]) -> None:
        self.overrides = dict(overrides)

    def remove(self, word: str) -> None:
        self.rules = [rule for rule in self.rules if rule.word != word]

    def clear(self, *, keep_builtin: bool = True) -> None:
        self.rules = []
        if keep_builtin:
            self.__post_init__()

    def _sort(self) -> None:
        self.rules.sort(key=lambda rule: (-rule.priority, -len(rule.word)))

    # -- helpers ------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.rules)

    def __bool__(self) -> bool:
        return bool(self.rules)

    def apply(
        self, text: str, *, report: "NormalizationReport | None" = None
    ) -> str:
        """Apply every rule once, longest/highest priority first."""
        if not text or not self.rules:
            return text
        hits: list[str] = []
        result = text
        for rule in self.rules:
            pattern = rule.pattern()
            result, count = pattern.subn(lambda _m, r=rule: r.replacement, result)
            if count:
                hits.append(f"{rule.word}→{rule.replacement}")
        if report is not None and hits:
            report.counts["dictionary_rules"] = len(hits)
            report.dictionary_hits.extend(hits[:20])
        return result

    def entries(self) -> list[dict[str, object]]:
        return [
            {
                "word": rule.word,
                "replacement": rule.replacement,
                "case_sensitive": rule.case_sensitive,
                "priority": rule.priority,
                "language": rule.language,
            }
            for rule in self.rules
        ]

    # -- pronunciation overrides -------------------------------------------
    def override_for(self, word: str) -> dict[str, str] | None:
        """Return ``{"ipa": ..., "diacritized": ...}`` for a word, if defined."""
        if not self.overrides:
            return None
        if word in self.overrides:
            return self.overrides[word]
        lowered = word.lower()
        for key, value in self.overrides.items():
            if key.lower() == lowered:
                return value
        return None

    def apply_overrides(self, text: str) -> str:
        """Replace words with their user-defined diacritized spelling."""
        if not text or not self.overrides:
            return text
        result = text
        for word, override in self.overrides.items():
            replacement = override.get("diacritized") or ""
            if not replacement:
                continue
            pattern = re.compile(rf"(?<![\w\u0600-\u06FF]){re.escape(word)}(?![\w\u0600-\u06FF])")
            result = pattern.sub(replacement, result)
        return result


def default_pronunciation_dictionary() -> PronunciationDictionary:
    return PronunciationDictionary()


def merge_entries(
    builtin: Sequence[tuple[str, str]], user: Sequence[tuple[str, str, bool]]
) -> list[tuple[str, str, bool]]:
    """Combine built-in and user rules, user rules winning."""
    merged: dict[str, tuple[str, str, bool]] = {}
    for word, replacement in builtin:
        merged[word] = (word, replacement, False)
    for word, replacement, case_sensitive in user:
        merged[word] = (word, replacement, case_sensitive)
    return list(merged.values())


def ipa_hint(word: str) -> str:
    """A minimal, *honest* IPA fallback used only to display hints in the UI.

    Real phonemisation is done by the espeak-ng backend inside the Piper engine
    pack (``piper.phonemize_espeak``); this helper never pretends to be exact.
    """
    table = {
        "ا": "æ", "آ": "ɒː", "ب": "b", "پ": "p", "ت": "t", "ث": "s", "ج": "dʒ",
        "چ": "tʃ", "ح": "h", "خ": "x", "د": "d", "ذ": "z", "ر": "ɾ", "ز": "z",
        "ژ": "ʒ", "س": "s", "ش": "ʃ", "ص": "s", "ض": "z", "ط": "t", "ظ": "z",
        "ع": "ʔ", "غ": "ɣ", "ف": "f", "ق": "ɢ", "ک": "k", "گ": "ɡ", "ل": "l",
        "م": "m", "ن": "n", "و": "v", "ه": "h", "ی": "j", ZWNJ: "",
    }
    return "".join(table.get(char, "") for char in word)
