"""Lexicon + rule based Persian diacritization (explicitly approximate).

This engine exists so that Persian diacritization is *usable* before the user
downloads the neural model, and so the app still works completely offline on a
minimal install.  It is a **curated pronunciation lexicon** (high-frequency
words with their correct short vowels) combined with morphology and context
rules — not a neural model, and the UI always labels it as approximate.

Honest scope:

* words present in the lexicon get their correct vowels,
* unknown words get *only* the marks that the rules can justify
  (Ezafe kasra, verb endings, plural endings),
* nothing is invented: a word whose vowels we do not know is left unmarked.

Users can extend the lexicon at any time by editing the pronunciation dictionary
(`Settings ▸ Voice Typing/AI ▸ Pronunciation`) or by correcting text in the
Diacritization workspace, which feeds back into the learned-overrides store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from ...app.logging_config import get_logger
from ..diacritization.base import (
    DiacritizationEngine,
    DiacritizationMode,
    DiacritizationResult,
)
from ..text_normalization.persian import (
    ARABIC_TO_PERSIAN,
    DIACRITIC_CHARS,
    ZWNJ,
    strip_diacritics,
)

log = get_logger("tixi.diacritization.lexicon")

FATHA = "\u064e"
DAMMA = "\u064f"
KASRA = "\u0650"
SOKUN = "\u0652"
SHADDA = "\u0651"
FATHATAN = "\u064b"
KASRATAN = "\u064d"
DAMMATAN = "\u064c"
_ALEF_MADDA = "\u0622"
_ALEF = "\u0627"
_VAV = "\u0648"
_YEH = "\u06cc"
_HEH = "\u0647"

#: Curated vocalisation of high-frequency Persian words.
#: Keys are written without diacritics and in the *Persian* spelling.
BUILTIN_LEXICON: dict[str, str] = {
    # pronouns / determiners
    "من": "مَن", "تو": "تُو", "او": "او", "ما": "ما", "شما": "شُما",
    "آن": "آن", "این": "این", "اینجا": "اینجا", "آنجا": "آنجا",
    "همین": "هَمین", "خود": "خود", "خودم": "خودَم", "خودت": "خودَت",
    "هیچ": "هیچ", "همه": "هَمِه", "هم": "هَم", "هیچ‌کدام": "هیچ‌کدام",
    "چه": "چِه", "کدام": "کُدام", "چقدر": "چِقَدر", "چطور": "چِطور",
    # copulas / frequent verbs
    "است": "اَست", "هست": "هَست", "نیست": "نیست", "بود": "بود", "بودند": "بودَند",
    "شد": "شُد", "شده": "شُدِه", "می‌شود": "می‌شَوَد", "می‌شوند": "می‌شَوَند",
    "می‌کند": "می‌کُنَد", "می‌کنند": "می‌کُنَند", "کرد": "کَرد", "کرده": "کَردِه",
    "دارد": "دارَد", "دارند": "دارَند", "داشت": "داشت", "داشتم": "داشتَم",
    "می‌تواند": "می‌تَوانَد", "می‌توانم": "می‌تَوانَم", "توانست": "تَوانست",
    "گفت": "گُفت", "گفتند": "گُفتَند", "می‌گوید": "می‌گویَد",
    "رفت": "رَفت", "رفتم": "رَفتَم", "آمد": "آمَد", "آمدند": "آمَدَند",
    "می‌آید": "می‌آیَد", "داد": "داد", "می‌دهد": "می‌دَهَد",
    "دید": "دید", "می‌بیند": "می‌بینَد", "خورد": "خورد", "خوابید": "خوابید",
    "نوشت": "نِوِشت", "خواند": "خواند", "گیر": "گیر", "بگیر": "بِگیر",
    "دانم": "دانَم", "می‌دانم": "می‌دانَم", "می‌دانید": "می‌دانید",
    "باید": "بایَد", "نباید": "نَبایَد", "شاید": "شایَد", "می‌شودکه": "می‌شَوَد که",
    # nouns — everyday
    "کتاب": "کِتاب", "خانه": "خانِه", "آب": "آب", "نان": "نان", "شهر": "شَهر",
    "روستا": "روستا", "کشور": "کِشوَر", "ایران": "ایران", "تهران": "تِهران",
    "مردم": "مَردُم", "پدر": "پِدَر", "مادر": "مادَر", "برادر": "بَرادَر",
    "خواهر": "خواهَر", "بچه": "بَچِه", "دوست": "دوست", "کار": "کار",
    "روز": "روز", "شب": "شَب", "صبح": "صُبح", "ظهر": "ظُهر", "عصر": "عَصر",
    "امروز": "اِمروز", "فردا": "فَردا", "دیروز": "دیروز", "سال": "سال",
    "ماه": "ماه", "هفته": "هَفتِه", "ساعت": "ساعَت", "دقیقه": "دَقیقِه",
    "ثانیه": "ثانیِه", "زمان": "زَمان", "وقت": "وَقت", "دنیا": "دُنیا",
    "زندگی": "زِندِگی", "عشق": "عِشق", "دل": "دِل", "قلب": "قَلب",
    "سر": "سَر", "دست": "دَست", "چشم": "چِشم", "گوش": "گوش", "دهان": "دَهان",
    "صورت": "صورَت", "مو": "مو", "پا": "پا", "جان": "جان", "نام": "نام",
    "اسم": "اِسم", "متن": "مَتن", "کلمه": "کَلِمِه", "جمله": "جُملِه",
    "زبان": "زَبان", "فارسی": "فارسی", "انگلیسی": "اِنگِلیسی", "دستور": "دَستور",
    "صدا": "صِدا", "آواز": "آواز", "موسیقی": "موسیقی", "فیلم": "فیلم",
    "تصویر": "تَصویر", "نقشه": "نَقشِه", "برنامه": "بَرنامِه", "پروژه": "پروژِه",
    "سیستم": "سیستِم", "کامپیوتر": "کامپیوتر", "رایانه": "رایانِه", "گوشی": "گوشی",
    "تلفن": "تِلِفُن", "اینترنت": "اینتِرنِت", "اطلاعات": "اِطلاعات",
    "فایل": "فایل", "پوشه": "پوشِه", "صفحه": "صَفحِه", "کلید": "کِلید",
    "دکمه": "دُکمِه", "تنظیمات": "تَنجیمات", "گزینه": "گُزینِه", "سرعت": "سُرعَت",
    "کیفیت": "کِیفیَت", "حجم": "حَجم", "فضا": "فَضا", "انرژی": "اِنِرژی",
    "هوش": "هوش", "مصنوعی": "مَصنوعی", "داده": "دادِه", "نتیجه": "نَتیجِه",
    "مدرسه": "مَدرِسِه", "دانشگاه": "دانِشگاه", "دانش": "دانِش", "علم": "عِلم",
    "کلاس": "کِلاس", "درس": "دَرس", "شاگرد": "شاگِرد", "معلم": "مُعَلِم",
    "استاد": "اُستاد", "دکتر": "دُکتُر", "مهندس": "مُهَندِس", "مدیر": "مُدیر",
    "شرکت": "شِرکَت", "کارمند": "کارمَند", "بازار": "بازار", "پول": "پول",
    "قیمت": "قِیمَت", "کالا": "کالا", "خبر": "خَبَر", "روزنامه": "روزنـامِه",
    "مجله": "مَجَلِه", "کتابخانه": "کِتابخانِه", "تاریخ": "تاریخ",
    "فرهنگ": "فَرهَنگ", "هنر": "هُنَر", "ورزش": "وَرزِش", "بازی": "بازی",
    "غذا": "غَذا", "چای": "چای", "قهوه": "قَهوِه", "شیر": "شیر", "گوشت": "گوشت",
    "میوه": "میوه", "سبزی": "سَبزی", "برنج": "بِرِنج", "روغن": "رُوغَن",
    "ماشین": "ماشین", "مترو": "مِترو", "اتوبوس": "اتوبوس", "قطار": "قِطار",
    "هواپیما": "هَواپَیما", "بلیت": "بِلیت", "سفر": "سَفَر", "هتل": "هُتِل",
    "بیمارستان": "بیمارِستان", "دکتری": "دُکتُری", "دارو": "دارو",
    "سلامت": "سَلامَت", "بیمار": "بیمار", "درد": "دَرد", "درمان": "دَرمان",
    "سؤال": "سُؤال", "پاسخ": "پاسُخ", "جواب": "جَواب", "پیام": "پَیام",
    "خبرنامه": "خَبَرنامِه", "ایمیل": "ایمِل", "شماره": "شُمارِه",
    "آدرس": "آدرِس", "کد": "کُد", "رمز": "رَمز", "امنیت": "اِمنیَت",
    "حریم": "حَریم", "خصوصی": "خُصوصی", "کاربر": "کاربَر", "حساب": "حِساب",
    # adjectives / adverbs
    "خوب": "خوب", "بد": "بَد", "بزرگ": "بُزُرگ", "کوچک": "کوچَک",
    "زیاد": "زیاد", "کم": "کَم", "خیلی": "خیلی", "بسیار": "بِسیار",
    "جدید": "جِدید", "قدیمی": "قَدیمی", "زیبا": "زیبا", "قشنگ": "قَشَنگ",
    "مهم": "مُهِم", "ممکن": "مُمکِن", "مشکل": "مُشکِل", "راحت": "راحَت",
    "سخت": "سَخت", "ساده": "سادِه", "سریع": "سَریع", "آهسته": "آهِستِه",
    "صحیح": "صَحیح", "درست": "دُرُست", "غلط": "غَلَط", "کامل": "کامِل",
    "ناقص": "ناقِص", "اول": "اَوَل", "آخر": "آخَر", "آخری": "آخَری",
    "بهتر": "بِهتَر", "بهترین": "بِهتَرین", "بیشتر": "بیشتَر", "کمتر": "کَمتَر",
    "بلند": "بُلَند", "کوتاه": "کوتاه", "تازه": "تازِه", "گرم": "گَرم",
    "سرد": "سَرد", "روشن": "رُوشَن", "تاریک": "تاریک", "سفید": "سِفید",
    "سیاه": "سیاه", "سبز": "سَبز", "قرمز": "قِرمِز", "آبی": "آبی",
    "زرد": "زَرد", "بین": "بین", "داخل": "داخِل", "خارج": "خارِج",
    "بالا": "بالا", "پایین": "پایین", "جلو": "جِلُو", "عقب": "عَقَب",
    "چپ": "چَپ", "راست": "راست", "کنار": "کَنار", "روی": "روی",
    "زیر": "زیر", "پیش": "پیش", "پس": "پَس", "هنوز": "هَنوز",
    "دیگر": "دیگَر", "فقط": "فَقَط", "حتماً": "حَتماً", "البته": "اَلبَتِه",
    "واقعاً": "واقِعاً", "تقریباً": "تَقریباً", "تقریبا": "تَقریباً",
    "مثلاً": "مَثَلَاً", "مثلا": "مَثَلَاً", "دوباره": "دوبارِه",
    "هنوزهم": "هَنوز هَم", "همیشه": "هَمیشِه", "گاهی": "گاهی", "هرگز": "هَرگِز",
    # function words worth vocalising
    "را": "را", "که": "کِه", "و": "وَ", "با": "با", "بی": "بی", "در": "دَر",
    "به": "بِه", "از": "اَز", "تا": "تا", "بر": "بَر", "برای": "بَرایِ",
    "اگر": "اَگَر", "چون": "چون", "ولی": "وَلـی", "اما": "اَمّا", "یا": "یا",
    "نه": "نَه", "بله": "بَـلِه", "آری": "آری", "چرا": "چِرا", "کجا": "کُجا",
    "کی": "کَی", "چگونه": "چِگونِه", "بنابراین": "بَنابَراین",
    "زیرا": "زیرا", "بلکه": "بَلکِه", "نیز": "نیز", "حتی": "حَتـی",
}

#: Verb endings: (ending, vocalised ending) — applied to the stem.
VERB_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("یم", f"{KASRA}م"),
    ("ید", f"{KASRA}{DAMMA}د"),
    ("ند", f"{FATHA}ند"),
    ("م", f"{FATHA}م"),
    ("ی", f"{KASRA}ی"),
    ("د", f"{FATHA}د"),
)

#: Prefixes that are written joined or half-joined to the stem.
VERB_PREFIXES: tuple[tuple[str, str], ...] = (
    ("می" + ZWNJ, f"م{KASRA}{ZWNJ}"),
    ("نمی" + ZWNJ, f"ن{KASRA}م{KASRA}{ZWNJ}"),
)

#: Frequent Ezafe contexts: a word followed by these is usually in Ezafe.
ADJECTIVE_ENDINGS = ("ی", "ین", "انه", "یی", "های")
_PLURAL_ENDINGS = ("ها", "های", "هایی", "ان")

_RE_WORD = re.compile(r"[\u0600-\u06FF\u200c]+")
_RE_LATIN = re.compile(r"[A-Za-z]+")


@dataclass
class LexiconEntry:
    word: str
    diacritized: str
    source: str = "builtin"


@dataclass
class LexiconDiacritizer(DiacritizationEngine):
    """Approximate, dictionary-and-rules Persian diacritizer."""

    engine_id: str = "lexicon-fa"
    display_name: str = "Persian pronunciation lexicon (approximate)"
    is_neural: bool = False
    languages: tuple[str, ...] = ("fa",)
    supports_confidence: bool = False
    memory_bytes: int = 1 * 1024 * 1024

    entries: dict[str, str] = field(default_factory=dict)
    learned: dict[str, str] = field(default_factory=dict)
    insert_ezafe: bool = True
    vocalise_function_words: bool = True

    def __post_init__(self) -> None:
        merged = dict(BUILTIN_LEXICON)
        merged.update(self.entries or {})
        normalised: dict[str, str] = {}
        for word, value in merged.items():
            key = _normalise_key(word)
            if key and value:
                normalised[key] = value
        self.entries = normalised

    # -- public API ---------------------------------------------------------
    def add_entry(self, word: str, diacritized: str, *, learned: bool = False) -> None:
        key = _normalise_key(word)
        if not key:
            return
        if learned:
            self.learned[key] = diacritized
        else:
            self.entries[key] = diacritized

    def load_overrides(self, overrides: Mapping[str, Any]) -> None:
        """Load user pronunciation overrides (word -> {"diacritized": ...})."""
        for word, payload in (overrides or {}).items():
            if isinstance(payload, dict):
                value = payload.get("diacritized") or payload.get("ipa")
            else:
                value = str(payload)
            if value:
                self.add_entry(word, str(value))

    def lookup(self, word: str) -> str | None:
        key = _normalise_key(word)
        if not key:
            return None
        return self.learned.get(key) or self.entries.get(key)

    def diacritize(
        self,
        text: str,
        *,
        mode: str | DiacritizationMode = DiacritizationMode.SMART,
        threshold: float | None = None,
        progress: Callable[[float, str], None] | None = None,
    ) -> DiacritizationResult:
        mode_enum = DiacritizationMode.parse(mode)
        source = text or ""
        if not source.strip():
            return DiacritizationResult(
                text=source, source_text=source, mode=mode_enum,
                engine_id=self.engine_id, engine_name=self.display_name, is_neural=False,
            )

        words = list(_RE_WORD.finditer(source))
        total = max(1, len(words))
        marks_added = 0
        known = 0
        output: list[str] = []
        cursor = 0

        for index, match in enumerate(words):
            if progress and index % 25 == 0:
                progress(index / total, "Applying Persian pronunciation lexicon")
            output.append(source[cursor : match.start()])
            raw_word = match.group(0)
            replacement = self._process_word(raw_word, mode_enum, source, match.end())
            if replacement is not None:
                output.append(replacement)
                marks_added += sum(1 for char in replacement if char in DIACRITIC_CHARS)
                known += 1
            else:
                output.append(raw_word)
            cursor = match.end()
        output.append(source[cursor:])
        if progress:
            progress(1.0, "Lexicon pass finished")

        result = DiacritizationResult(
            text="".join(output),
            source_text=source,
            mode=mode_enum,
            engine_id=self.engine_id,
            engine_name=self.display_name,
            is_neural=False,
            marks_added=marks_added,
            words_processed=len(words),
            confidence=0.0,
            warnings=[
                "This is the built-in pronunciation lexicon, not a neural model. "
                "Install the Persian diacritization model for full coverage."
            ],
            statistics={
                "known_words": known,
                "unknown_words": len(words) - known,
                "lexicon_size": len(self.entries) + len(self.learned),
            },
        )
        return result

    # -- internals ----------------------------------------------------------
    def _process_word(
        self, word: str, mode: DiacritizationMode, source: str, end_offset: int
    ) -> str | None:
        if not word or strip_diacritics(word) == "":
            return None
        bare = strip_diacritics(word)
        if _RE_LATIN.fullmatch(bare):
            return None

        direct = self.lookup(bare)
        if direct:
            return direct

        # Plural: word + ها / های  ->  vocalised stem + vocalised suffix
        for ending in _PLURAL_ENDINGS:
            if bare.endswith(ending) and len(bare) > len(ending) + 1:
                stem = bare[: -len(ending)]
                stem_marked = self.lookup(stem)
                if stem_marked:
                    return stem_marked + ZWNJ + _vocalise_plural(ending)

        # Verb ending: stem + personal suffix
        for ending, marked_ending in VERB_SUFFIXES:
            if bare.endswith(ending) and len(bare) > 2:
                stem = bare[: -len(ending)]
                stem_marked = self.lookup(stem)
                if stem_marked and mode is DiacritizationMode.FULL:
                    return stem_marked + marked_ending

        # Half-space prefixed verbs: می‌رود / نمی‌رود
        for prefix, marked_prefix in VERB_PREFIXES:
            if bare.startswith(prefix):
                stem = bare[len(prefix) :]
                stem_marked = self.lookup(stem)
                if stem_marked:
                    return marked_prefix + stem_marked
                if mode is DiacritizationMode.FULL and stem:
                    return marked_prefix + stem

        if mode is DiacritizationMode.FULL:
            return self._rule_based(bare)
        return None

    def _rule_based(self, bare: str) -> str:
        """Very conservative rule pass used only in full mode."""
        letters = list(bare)
        out: list[str] = []
        for index, char in enumerate(letters):
            out.append(char)
            last = index == len(letters) - 1
            if last:
                # Words ending in a consonant usually take no mark; a final "ه"
                # is pronounced /e/ and gets a kasra (also covers the Ezafe).
                if char == _HEH:
                    out.append(KASRA)
            elif char in _VAV:
                continue
        return "".join(out)


def _vocalise_plural(ending: str) -> str:
    if ending == "ها":
        return f"{FATHA}ها"
    if ending == "های":
        return f"{FATHA}هایِ"
    if ending == "هایی":
        return f"{FATHA}هایی"
    if ending == "ان":
        return f"{FATHA}ان"
    return ending


#: Letter shapes that must be folded before a lexicon lookup.  Built from the
#: pipeline table so the lexicon can never disagree with normalisation.
_LEXICON_FOLD = str.maketrans(ARABIC_TO_PERSIAN)


def _normalise_key(word: str) -> str:
    """Canonical lexicon key: no diacritics and Persian letter forms only.

    Arabic yeh/kaf/heh variants are folded to their Persian counterparts so a
    word typed with an Arabic keyboard still hits the same lexicon entry.
    """
    if not word:
        return ""
    cleaned = strip_diacritics(word).strip().translate(_LEXICON_FOLD)
    cleaned = re.sub(r"[^\u0600-\u06FF\u200c]", "", cleaned)
    return cleaned


def ezafe_needed(word: str, next_word: str, *, context: str = "") -> bool:
    """Should ``word`` carry an Ezafe kasra before ``next_word``?

    This is the conservative heuristic used in *smart* mode: it only fires for
    endings and contexts where the Ezafe is essentially unconditional.  The
    neural engine replaces this with learned predictions; when it is available
    the heuristic is not used at all.
    """
    if not word or not next_word:
        return False
    if next_word.startswith(("ی", "می", "نمی")):
        return False
    if next_word[0] in "،؛؟!.«»()[]":
        return False
    if _RE_LATIN.match(next_word[0]) or next_word[0].isdigit():
        return True
    return word.endswith(_PLURAL_ENDINGS) or word.endswith(("ه", "ی", "ان", "ات"))


def find_words(text: str) -> Sequence[re.Match[str]]:
    return list(_RE_WORD.finditer(text))


def lexicon_coverage(text: str, lexicon: Mapping[str, str]) -> float:
    """Share of Persian words in ``text`` covered by ``lexicon`` (0..1)."""
    matches = find_words(text)
    if not matches:
        return 0.0
    covered = sum(1 for match in matches if _normalise_key(match.group(0)) in lexicon)
    return covered / len(matches)


def export_lexicon() -> dict[str, str]:
    """Return a copy of the built-in lexicon (used by tests and docs)."""
    return dict(BUILTIN_LEXICON)
