"""Number, date, time, currency and unit verbalisation for Persian and English.

Spoken numbers are one of the biggest sources of unnatural TTS output: a Piper
voice handed ``۱۴۰۲/۰۵/۱۲`` will read slashes and digits instead of
"دوازده مرداد هزار و چهارصد و دو".  This module converts numeric expressions
into words *in the target language* while leaving everything else untouched, so
it can be used as one stage of the :mod:`~tixi.engines.text_normalization`
pipeline.

The Persian rules follow everyday Iranian broadcast/newsroom conventions:

* ``۱۲۳۴``      -> ``هزار و دویست و سی و چهار``
* ``۳/۱۴``      -> ``سه ممیز چهارده``
* ``۲۵٪``       -> ``بیست و پنج درصد``
* ``۱۴:۳۰``     -> ``ساعت چهارده و سی دقیقه``
* ``۱۴۰۲/۰۵/۱۲``-> ``دوازده مرداد هزار و چهارصد و دو``
* ``۵ تا ۱۰``   -> ``پنج تا ده``
* ``۰۹۱۲۳۴۵۶۷۸۹``-> ``صفر نه یک دو سه چهار پنج شش هفت هشت نه`` (digit by digit)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Digit handling
# ---------------------------------------------------------------------------
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_ASCII = "0123456789"

_DIGIT_TRANSLATION = str.maketrans(
    {**{ord(p): a for p, a in zip(PERSIAN_DIGITS, _ASCII)},
     **{ord(a): a for a in ARABIC_DIGITS},
     ord("٫"): ".",
     ord("،"): ","}  # Arabic decimal separator, Arabic comma used as thousands sep
)

#: Persian decimal separator characters that may appear inside numbers.
DECIMAL_SEPARATORS = ".٫,،"
_THOUSANDS_SEPARATORS = ",،٬ ٬"

ONES_FA = (
    "صفر",
    "یک",
    "دو",
    "سه",
    "چهار",
    "پنج",
    "شش",
    "هفت",
    "هشت",
    "نه",
    "ده",
    "یازده",
    "دوازده",
    "سیزده",
    "چهارده",
    "پانزده",
    "شانزده",
    "هفده",
    "هجده",
    "نوزده",
)
TENS_FA = ("", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود")
HUNDREDS_FA = (
    "",
    "صد",
    "دویست",
    "سیصد",
    "چهارصد",
    "پانصد",
    "ششصد",
    "هفتصد",
    "هشتصد",
    "نهصد",
)
SCALES_FA = ("", "هزار", "میلیون", "میلیارد", "تریلیون", "کوادریلیون", "کوینتیلیون")
ORDINAL_FA = {
    1: "یکم",
    2: "دوم",
    3: "سوم",
    4: "چهارم",
    5: "پنجم",
    6: "ششم",
    7: "هفتم",
    8: "هشتم",
    9: "نهم",
    10: "دهم",
    11: "یازدهم",
    12: "دوازدهم",
    13: "سیزدهم",
    14: "چهاردهم",
    15: "پانزدهم",
    16: "شانزدهم",
    17: "هفدهم",
    18: "هجدهم",
    19: "نوزدهم",
    20: "بیستم",
    30: "سی‌ام",
    40: "چهلم",
    50: "پنجاهم",
    60: "شصتم",
    70: "هفتادم",
    80: "هشتادم",
    90: "نودم",
    100: "صدم",
    1000: "هزارم",
}
MONTHS_FA = (
    "",
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)
WEEKDAYS_FA = (
    "دوشنبه",
    "سه‌شنبه",
    "چهارشنبه",
    "پنج‌شنبه",
    "جمعه",
    "شنبه",
    "یک‌شنبه",
)

# English words (used for mixed text and English voices).
ONES_EN = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
)
TENS_EN = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
SCALES_EN = ("", "thousand", "million", "billion", "trillion", "quadrillion")
ORDINALS_EN = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
    7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth", 11: "eleventh",
    12: "twelfth", 13: "thirteenth", 20: "twentieth", 30: "thirtieth", 100: "hundredth",
}

#: unit symbol -> (Persian singular, Persian plural/plain, English)
UNITS: dict[str, tuple[str, str, str]] = {
    "km": ("کیلومتر", "کیلومتر", "kilometres"),
    "m": ("متر", "متر", "metres"),
    "cm": ("سانتی‌متر", "سانتی‌متر", "centimetres"),
    "mm": ("میلی‌متر", "میلی‌متر", "millimetres"),
    "kg": ("کیلوگرم", "کیلوگرم", "kilograms"),
    "g": ("گرم", "گرم", "grams"),
    "mg": ("میلی‌گرم", "میلی‌گرم", "milligrams"),
    "l": ("لیتر", "لیتر", "litres"),
    "ml": ("میلی‌لیتر", "میلی‌لیتر", "millilitres"),
    "gb": ("گیگابایت", "گیگابایت", "gigabytes"),
    "mb": ("مگابایت", "مگابایت", "megabytes"),
    "kb": ("کیلوبایت", "کیلوبایت", "kilobytes"),
    "tb": ("ترابایت", "ترابایت", "terabytes"),
    "hz": ("هرتز", "هرتز", "hertz"),
    "khz": ("کیلوهرتز", "کیلوهرتز", "kilohertz"),
    "ghz": ("گیگاهرتز", "گیگاهرتز", "gigahertz"),
    "w": ("وات", "وات", "watts"),
    "kw": ("کیلووات", "کیلووات", "kilowatts"),
    "v": ("ولت", "ولت", "volts"),
    "a": ("آمپر", "آمپر", "amps"),
    "px": ("پیکسل", "پیکسل", "pixels"),
    "dpi": ("دی‌پی‌آی", "دی‌پی‌آی", "D P I"),
    "°c": ("درجه سانتی‌گراد", "درجه سانتی‌گراد", "degrees Celsius"),
    "°f": ("درجه فارنهایت", "درجه فارنهایت", "degrees Fahrenheit"),
    "°": ("درجه", "درجه", "degrees"),
    "s": ("ثانیه", "ثانیه", "seconds"),
    "min": ("دقیقه", "دقیقه", "minutes"),
    "h": ("ساعت", "ساعت", "hours"),
}

CURRENCIES: dict[str, tuple[str, str, str]] = {
    "تومان": ("تومان", "تومان", "toman"),
    "ریال": ("ریال", "ریال", "rial"),
    "$": ("دلار", "دلار", "dollars"),
    "دلار": ("دلار", "دلار", "dollars"),
    "€": ("یورو", "یورو", "euros"),
    "یورو": ("یورو", "یورو", "euros"),
    "£": ("پوند", "پوند", "pounds"),
    "پوند": ("پوند", "پوند", "pounds"),
    "﷼": ("ریال", "ریال", "rial"),
}

# Abbreviations that should be expanded before anything else.
ABBREVIATIONS_FA: dict[str, str] = {
    "م.": "میلادی",
    "ق.": "قمری",
    "ش.": "شمسی",
    "دکتر": "دکتر",
    "پ.ن": "پی‌نوشت",
    "وغ": "و غیره",
    "وغ.": "و غیره",
    "مث": "مثلاً",
    "مث.": "مثلاً",
    "ص": "صفحه",
    "ج": "جلد",
    "ت": "تاریخ",
    "خ": "خیابان",
    "ک": "کوچه",
    "ش": "شماره",
    "شماره": "شماره",
    "وب": "وب",
    "اینترنت": "اینترنت",
    "آی‌تی": "آی‌تی",
    "کد": "کد",
    "تلفن": "تلفن",
    "ساعت": "ساعت",
    "ه.ش": "هجری شمسی",
    "ه.ق": "هجری قمری",
    "ق.م": "قبل از میلاد",
    "م": "میلادی",
    "ه": "هجری",
    "%": "درصد",
}

ABBREVIATIONS_EN: dict[str, str] = {
    "e.g.": "for example",
    "i.e.": "that is",
    "etc.": "et cetera",
    "vs.": "versus",
    "no.": "number",
    "fig.": "figure",
    "min.": "minutes",
    "max.": "maximum",
    "approx.": "approximately",
}

_DIGIT_WORDS_FA = tuple(ONES_FA[:10])
_DIGIT_WORDS_EN = tuple(ONES_EN[:10])

# Regular expressions -------------------------------------------------------
_NUMBER = r"\d+(?:[.,٫]\d+)?"
_RE_NUMBER = re.compile(_NUMBER)
_RE_THOUSANDS = re.compile(r"^\d{1,3}(?:[,٬]\d{3})+$")
_RE_DATE = re.compile(r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b")
_RE_DATE_DMY = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b")
_RE_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?\b")
_RE_PERCENT_FA = re.compile(r"([\d٠-٩۰-۹]+(?:[.,٫][\d٠-٩۰-۹]+)?)\s*[٪%]")
_RE_RANGE = re.compile(r"\b(\d+(?:[.,٫]\d+)?)\s*[-–—]\s*(\d+(?:[.,٫]\d+)?)\b")
_RE_ORDINAL_FA = re.compile(r"\b(\d+)\s*(?:م|ام|اُم)\b")
_RE_ORDINAL_EN = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)
_RE_CURRENCY_PREFIX = re.compile(r"([$€£])\s*(?=\d)")
_RE_CURRENCY_SUFFIX = re.compile(
    r"(\d+(?:[.,٫]\d+)?)\s*(تومان|ریال|دلار|یورو|پوند|افغانی|درهم)\b"
)
_RE_UNIT = re.compile(
    r"(\d+(?:[.,٫]\d+)?)\s*(°C|°F|km|cm|mm|kg|mg|ml|gb|mb|kb|tb|khz|ghz|hz|kw|km/h|m/s|"
    r"کیلومتر|کیلوگرم|سانتی‌متر|میلی‌متر|متر|گرم|لیتر|درصد|ساعت|دقیقه|ثانیه)\b",
    re.IGNORECASE,
)
_RE_PHONE = re.compile(r"(?<![\d.])(\+?\d[\d\s\-()]{7,}\d)(?![\d.])")
#: "ساعت" (or the English word once/at) directly in front of a spoken time.
_RE_TIME_DUPLICATE = re.compile(r"\b(ساعت|at)\s+(ساعت\b|once\b)", re.IGNORECASE)
_RE_FRACTION_FA = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")


@dataclass(frozen=True)
class NumberRules:
    """Knobs for :func:`verbalise_text`."""

    language: str = "fa"
    spell_out_threshold: int = 13          # numbers below this are always spelled
    read_phone_digits: bool = True         # 8+ digit runs read one digit at a time
    expand_dates: bool = True
    expand_times: bool = True
    expand_currency: bool = True
    expand_units: bool = True
    expand_percent: bool = True
    expand_ordinals: bool = True
    expand_abbreviations: bool = True
    keep_year_digits: bool = False         # False -> years are spoken


# ---------------------------------------------------------------------------
# Persian verbalisation
# ---------------------------------------------------------------------------
def number_to_words_fa(value: int | str, *, year: bool = False) -> str:
    """Convert an integer (or digit string) to Persian words."""
    if isinstance(value, str):
        digits = to_ascii_digits(value).replace(",", "").replace("٬", "")
        if not digits.isdigit():
            raise ValueError(f"not an integer: {value!r}")
        number = int(digits)
    else:
        number = int(value)
    if number == 0:
        return ONES_FA[0]
    sign = "منفی " if number < 0 else ""
    number = abs(number)
    if year and 1300 <= number <= 1500:
        # Persian years are spoken as "هزار و چهارصد و دو" (never "یک هزار").
        number_text = _three_digit_group_fa(number % 1000)
        year_text = f"هزار{' و ' + number_text if number_text else ''}"
        return sign + year_text
    return sign + _integer_to_words_fa(number)


def _integer_to_words_fa(number: int) -> str:
    if number <= 19:
        return ONES_FA[number]
    if number < 100:
        tens, rest = divmod(number, 10)
        return TENS_FA[tens] + (f" و {ONES_FA[rest]}" if rest else "")
    if number < 1000:
        hundreds, rest = divmod(number, 100)
        head = HUNDREDS_FA[hundreds]
        return head + (f" و {_integer_to_words_fa(rest)}" if rest else "")

    groups: list[tuple[int, int]] = []
    index = 0
    remaining = number
    while remaining > 0 and index < len(SCALES_FA):
        remaining, chunk = divmod(remaining, 1000)
        groups.append((chunk, index))
        index += 1
    parts: list[str] = []
    for chunk, scale in reversed(groups):
        if chunk == 0:
            continue
        scale_word = SCALES_FA[scale] if scale < len(SCALES_FA) else ""
        if scale == 1 and chunk == 1:
            chunk_text = ""  # "هزار" not "یک هزار"
        else:
            chunk_text = _integer_to_words_fa(chunk)
        if scale_word and chunk_text:
            parts.append(f"{chunk_text} {scale_word}")
        elif scale_word:
            parts.append(scale_word)
        else:
            parts.append(chunk_text)
    return " و ".join(parts)


def _three_digit_group_fa(number: int) -> str:
    return _integer_to_words_fa(number) if number else ""


def decimal_to_words_fa(text: str) -> str:
    """``"3.14"`` -> ``"سه ممیز چهارده"``."""
    normalised = to_ascii_digits(text).replace("٫", ".").replace("،", ".")
    if "." not in normalised:
        return number_to_words_fa(normalised)
    whole, _, fraction = normalised.partition(".")
    whole_text = number_to_words_fa(whole or "0")
    fraction = fraction.rstrip("0") or "0"
    fraction_text = number_to_words_fa(fraction) if len(fraction) < 4 else _digits_fa(fraction)
    return f"{whole_text} ممیز {fraction_text}"


def year_to_words_fa(year: int) -> str:
    return number_to_words_fa(year, year=True)


def ordinal_fa(number: int) -> str:
    if number in ORDINAL_FA:
        return ORDINAL_FA[number]
    if number < 100:
        tens = (number // 10) * 10
        rest = number % 10
        if tens in ORDINAL_FA and rest:
            return f"{TENS_FA[tens]} و {ORDINAL_FA[rest]}"
        if tens in ORDINAL_FA:
            return ORDINAL_FA[tens]
    # General rule: number words + "اُم"
    return f"{_integer_to_words_fa(number)}‌اُم".replace(" ", " و ") if number < 1000 else (
        _integer_to_words_fa(number) + "اُم"
    )


def _digits_fa(digit_string: str) -> str:
    return " ".join(_DIGIT_WORDS_FA[int(ch)] for ch in digit_string if ch.isdigit())


def time_to_words_fa(hour: int, minute: int, second: int | None = None) -> str:
    """``14:30`` -> ``ساعت چهارده و سی دقیقه``."""
    parts = [f"ساعت {number_to_words_fa(hour)}"]
    if minute:
        minute_text = number_to_words_fa(minute)
        parts.append(f"{minute_text} دقیقه")
    elif second is not None and second:
        parts.append("و صفر دقیقه")
    if second:
        parts.append(f"{number_to_words_fa(second)} ثانیه")
    return " و ".join(parts)


def date_to_words_fa(year: int, month: int, day: int, *, julian: bool = True) -> str:
    """``1402/05/12`` -> ``دوازده مرداد هزار و چهارصد و دو``."""
    month_name = MONTHS_FA[month] if 1 <= month <= 12 else number_to_words_fa(month)
    day_text = ordinal_fa(day) if day <= 31 else number_to_words_fa(day)
    if not julian:
        day_text = ordinal_fa(day) if day in ORDINAL_FA else number_to_words_fa(day)
    return f"{day_text} {month_name} {year_to_words_fa(year)}"


# ---------------------------------------------------------------------------
# English verbalisation
# ---------------------------------------------------------------------------
def number_to_words_en(value: int | str) -> str:
    if isinstance(value, str):
        digits = to_ascii_digits(value).replace(",", "")
        if not digits.lstrip("-").isdigit():
            raise ValueError(f"not an integer: {value!r}")
        number = int(digits)
    else:
        number = int(value)
    if number == 0:
        return "zero"
    if number < 0:
        return "minus " + number_to_words_en(-number)
    words: list[str] = []
    scale_index = 0
    remaining = number
    while remaining > 0:
        remaining, chunk = divmod(remaining, 1000)
        if chunk:
            chunk_words = _three_digit_en(chunk)
            scale = SCALES_EN[scale_index] if scale_index < len(SCALES_EN) else ""
            words.insert(0, f"{chunk_words} {scale}".strip())
        scale_index += 1
    return " ".join(words)


def _three_digit_en(number: int) -> str:
    hundreds, rest = divmod(number, 100)
    parts: list[str] = []
    if hundreds:
        parts.append(f"{ONES_EN[hundreds]} hundred")
    if rest:
        if rest < 20:
            parts.append(ONES_EN[rest])
        else:
            tens, unit = divmod(rest, 10)
            parts.append(TENS_EN[tens] + (f"-{ONES_EN[unit]}" if unit else ""))
    return " ".join(parts)


def decimal_to_words_en(text: str) -> str:
    normalised = to_ascii_digits(text).replace(",", "")
    if "." not in normalised:
        return number_to_words_en(normalised)
    whole, _, fraction = normalised.partition(".")
    return f"{number_to_words_en(whole or '0')} point {' '.join(ONES_EN[int(ch)] for ch in fraction)}"


def ordinal_en(number: int) -> str:
    if number in ORDINALS_EN:
        return ORDINALS_EN[number]
    if 20 < number < 100:
        tens, unit = divmod(number, 10)
        if unit == 0:
            return ORDINALS_EN.get(tens * 10, number_to_words_en(number) + "th")
        return f"{TENS_EN[tens]}-{ORDINALS_EN.get(unit, ONES_EN[unit] + 'th')}"
    return number_to_words_en(number) + "th"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def to_ascii_digits(text: str) -> str:
    """Convert Persian/Arabic-Indic digits to ASCII without touching letters."""
    return text.translate(_DIGIT_TRANSLATION)


def convert_digits(text: str, target: str = "fa") -> str:
    """Replace ASCII digits with Persian (``fa``) or ASCII (``en``) digits."""
    if target == "fa":
        return "".join(PERSIAN_DIGITS[int(ch)] if ch.isdigit() else ch for ch in text)
    return to_ascii_digits(text)


def is_persian_text(text: str) -> bool:
    """Heuristic: does ``text`` contain a meaningful amount of Persian script?"""
    script = 0
    latin = 0
    for char in text:
        if _is_persian_letter(char):
            script += 1
        elif char.isascii() and char.isalpha():
            latin += 1
    if script == 0:
        return False
    return script >= max(1, latin * 0.4)


def _is_persian_letter(char: str) -> bool:
    code = ord(char)
    return (
        0x0600 <= code <= 0x06FF
        or 0x0750 <= code <= 0x077F
        or 0xFB50 <= code <= 0xFDFF
        or 0xFE70 <= code <= 0xFEFF
    )


def verbalise_text(text: str, rules: NumberRules | None = None) -> str:
    """Rewrite numeric expressions in ``text`` as words.

    Mixed Persian/English input is handled: each numeric expression is spoken in
    the language of the surrounding text.
    """
    rules = rules or NumberRules()
    if not text:
        return ""
    working = to_ascii_digits(text)
    persian = rules.language == "fa" or is_persian_text(text)

    if rules.expand_abbreviations:
        working = _expand_abbreviations(working, persian)

    # Order matters: dates/times first so their digits are not re-processed.
    if rules.expand_dates:
        working = _RE_DATE.sub(lambda m: _fa_date(m) if persian else _en_date(m), working)
        working = _RE_DATE_DMY.sub(lambda m: _fa_date_dmy(m) if persian else _en_date_dmy(m), working)
    if rules.expand_times:
        working = _RE_TIME.sub(lambda m: _fa_time(m) if persian else _en_time(m), working)
        # "ساعت ۱۰:۳۰" must not become "ساعت ساعت ده و سی دقیقه".
        working = _RE_TIME_DUPLICATE.sub(r"\1", working)
    if rules.expand_percent:
        working = _RE_PERCENT_FA.sub(
            lambda m: (
                f"{decimal_to_words_fa(m.group(1))} درصد"
                if persian
                else f"{decimal_to_words_en(m.group(1))} percent"
            ),
            working,
        )
    if rules.expand_currency:
        working = _RE_CURRENCY_PREFIX.sub(
            lambda m: _currency_word(m.group(1), persian) + " ", working
        )
        working = _RE_CURRENCY_SUFFIX.sub(
            lambda m: f"{_spoken_number(m.group(1), persian)} {_currency_word(m.group(2), persian)}",
            working,
        )
    if rules.expand_units:
        working = _RE_UNIT.sub(lambda m: _unit_replacement(m, persian), working)
    if rules.expand_ordinals:
        working = _RE_ORDINAL_FA.sub(
            lambda m: ordinal_fa(int(m.group(1))) if persian else ordinal_en(int(m.group(1))),
            working,
        )
        working = _RE_ORDINAL_EN.sub(lambda m: ordinal_en(int(m.group(1))), working)

    if rules.read_phone_digits:
        working = _RE_PHONE.sub(lambda m: _phone_words(m.group(1), persian), working)

    working = _RE_RANGE.sub(lambda m: _range_replacement(m, persian), working)

    def _generic(match: re.Match[str]) -> str:
        raw = match.group(0)
        if _looks_like_year(raw, working, match.start()):
            value = int(to_ascii_digits(raw))
            return year_to_words_fa(value) if persian else number_to_words_en(value)
        if len(raw) > 15:
            return raw  # something like an ID hash: leave it alone
        try:
            return _spoken_number(raw, persian)
        except ValueError:
            return raw

    working = _RE_NUMBER.sub(_generic, working)
    return working


def _spoken_number(raw: str, persian: bool) -> str:
    cleaned = raw.replace("٬", "").replace(",", "")
    has_decimal = "." in cleaned
    if has_decimal:
        return decimal_to_words_fa(cleaned) if persian else decimal_to_words_en(cleaned)
    value = int(cleaned)
    return number_to_words_fa(value) if persian else number_to_words_en(value)


def _expand_abbreviations(text: str, persian: bool) -> str:
    table = ABBREVIATIONS_FA if persian else ABBREVIATIONS_EN
    for abbreviation, expansion in table.items():
        if abbreviation == expansion:
            continue
        pattern = re.compile(rf"(?<![\w\u0600-\u06FF]){re.escape(abbreviation)}(?![\w\u0600-\u06FF])")
        text = pattern.sub(expansion, text)
    return text


def _fa_date(match: re.Match[str]) -> str:
    year, month, day = (int(part) for part in match.groups())
    if month > 12 or day > 31:
        return match.group(0)
    if 1200 <= year <= 1600:  # Jalali/Shamsi
        return date_to_words_fa(year, month, day)
    # Gregorian: keep a spoken but unambiguous form.
    return f"{number_to_words_fa(day)} ماه {number_to_words_fa(month)} سال {number_to_words_fa(year)}"


def _fa_date_dmy(match: re.Match[str]) -> str:
    day, month, year = (int(part) for part in match.groups())
    if month > 12 or day > 31:
        return match.group(0)
    return f"{ordinal_fa(day)} {MONTHS_FA[month]} {number_to_words_fa(year)}"


def _en_date(match: re.Match[str]) -> str:
    year, month, day = (int(part) for part in match.groups())
    return f"{MONTHS_EN.get(month, month)} {ordinal_en(day)}, {number_to_words_en(year)}"


def _en_date_dmy(match: re.Match[str]) -> str:
    day, month, year = (int(part) for part in match.groups())
    return f"{ordinal_en(day)} of {MONTHS_EN.get(month, month)} {number_to_words_en(year)}"


MONTHS_EN = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}


def _fa_time(match: re.Match[str]) -> str:
    hour, minute = int(match.group(1)), int(match.group(2))
    second = int(match.group(3)) if match.group(3) else None
    return time_to_words_fa(hour, minute, second)


def _en_time(match: re.Match[str]) -> str:
    hour, minute = int(match.group(1)), int(match.group(2))
    suffix = "AM" if hour < 12 else "PM"
    display = hour % 12 or 12
    if minute == 0:
        return f"{number_to_words_en(display)} {suffix}"
    return f"{number_to_words_en(display)} {number_to_words_en(minute)} {suffix}"


def _currency_word(symbol: str, persian: bool) -> str:
    entry = CURRENCIES.get(symbol)
    if not entry:
        return symbol
    return entry[0] if persian else entry[2]


def _unit_replacement(match: re.Match[str], persian: bool) -> str:
    value, unit = match.group(1), match.group(2)
    key = unit.lower()
    if key in ("درصد", "%"):
        word = "درصد"
        return f"{_spoken_number(value, persian)} {word}" if persian else f"{_spoken_number(value, False)} percent"
    entry = UNITS.get(key)
    if entry:
        return f"{_spoken_number(value, persian)} {entry[0] if persian else entry[2]}"
    return f"{_spoken_number(value, persian)} {unit}"


def _phone_words(raw: str, persian: bool) -> str:
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) < 8:
        return raw
    reading = _digits_fa(digits) if persian else " ".join(ONES_EN[int(ch)] for ch in digits)
    if raw.strip().startswith("+"):
        prefix = "به علاوه" if persian else "plus"
        return f"{prefix} {reading}"
    return reading


def _range_replacement(match: re.Match[str], persian: bool) -> str:
    left, right = match.group(1), match.group(2)
    joiner = "تا" if persian else "to"
    return f"{_spoken_number(left, persian)} {joiner} {_spoken_number(right, persian)}"


def _looks_like_year(raw: str, context: str, start: int) -> bool:
    text = to_ascii_digits(raw)
    if len(text) != 4 or not text.isdigit():
        return False
    value = int(text)
    if not (1300 <= value <= 2100):
        return False
    prefix = context[max(0, start - 14):start]
    return bool(re.search(r"(سال|year|در سال|سنه)\s*$", prefix, re.IGNORECASE)) or 1300 <= value <= 1500


def normalise_unicode(text: str) -> str:
    """NFKC-ish normalisation that keeps Persian combining marks intact."""
    return unicodedata.normalize("NFC", text)
