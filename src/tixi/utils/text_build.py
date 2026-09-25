"""Text post-processing for dictated speech.

Applied to recognised text before it is inserted, in this order:

1. whitespace and Persian half-space hygiene,
2. optional punctuation reconstruction (Persian question/exclamation marks,
   sentence-final stop for long utterances),
3. optional capitalisation (only for Latin-script sentences — Persian has no
   letter case, so nothing is invented there),
4. user post-processing rules (regular-expression free, literal replacements),
5. optional dictation commands ("new line", "period", "comma", …) in both
   English and Persian.

Nothing here changes meaning: it fixes spacing and adds punctuation only where
the model did not, which is what "automatic punctuation" honestly means for an
offline Whisper model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..engines.text_normalization.persian import ZWNJ, is_persian_text

_RE_MULTISPACE = re.compile(r"[ \t\u00a0]{2,}")
_RE_SPACE_BEFORE_PUNCT = re.compile(r"\s+([،؛؟!.٫:,;?!])")
_RE_SPACE_AFTER_OPEN = re.compile(r"([«(\[{])\s+")
_RE_MULTI_PUNCT = re.compile(r"([.!؟?])\1+")
_RE_SENTENCE_START = re.compile(r"(^|[.!؟?]\s+)([a-z])")
_RE_TRAILING_PARTIAL = re.compile(r"[,;،؛:]\s*$")

#: Spoken commands mapped to their written equivalent (English + Persian).
DICTATION_COMMANDS: dict[str, str] = {
    "new line": "\n",
    "newline": "\n",
    "new paragraph": "\n\n",
    "period": ".",
    "full stop": ".",
    "comma": "،",
    "question mark": "؟",
    "exclamation mark": "!",
    "exclamation point": "!",
    "colon": ":",
    "semicolon": "؛",
    "line break": "\n",
    "open parenthesis": "(",
    "close parenthesis": ")",
    "quote": "«",
    "end quote": "»",
    "نقطه": ".",
    "ویرگول": "،",
    "علامت سؤال": "؟",
    "علامت سوال": "؟",
    "علامت تعجب": "!",
    "خط جدید": "\n",
    "پاراگراف جدید": "\n\n",
    "دونقطه": ":",
    "نقطه ویرگول": "؛",
    "پرانتز باز": "(",
    "پرانتز بسته": ")",
}


@dataclass
class PostProcessOptions:
    """Switches mirroring Settings ▸ Voice Typing."""

    fix_spacing: bool = True
    auto_punctuation: bool = True
    auto_capitalization: bool = True
    commands: bool = True
    append_space: bool = True
    trim: bool = True
    max_length: int = 0                 # 0 = unlimited
    language: str = "fa"
    persian_punctuation: bool = True
    strip_filler_words: bool = False
    rules: tuple[str, ...] = ()         # literal "find => replace" rules
    keep_trailing_newline: bool = False


def clean_whitespace(text: str) -> str:
    text = (text or "").replace("\u00a0", " ")
    text = _RE_MULTISPACE.sub(" ", text)
    text = _RE_SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _RE_SPACE_AFTER_OPEN.sub(r"\1", text)
    text = re.sub(rf"\s*{ZWNJ}\s*", ZWNJ, text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def apply_commands(text: str, language: str = "fa") -> str:
    """Replace spoken dictation commands with their written form."""
    if not text:
        return text
    result = text
    lowered = result.lower()
    for phrase, replacement in sorted(DICTATION_COMMANDS.items(), key=lambda item: -len(item[0])):
        pattern = re.compile(rf"(?:^|\s){re.escape(phrase)}(?=\s|$)", re.IGNORECASE)
        if pattern.search(lowered if phrase.isascii() else result):
            # Command replacement: keep one separating space before "." style marks.
            result = pattern.sub(_replacement_for(replacement), result)
    return clean_whitespace(result) if "\n" in result else result


def _replacement_for(replacement: str) -> str:
    if replacement.startswith("\n"):
        return replacement
    return "\\g<0>" if False else f" {replacement}"


def auto_punctuate(text: str, *, language: str = "fa", persian_punctuation: bool = True) -> str:
    """Add the punctuation an offline model often omits."""
    if not text:
        return text
    result = clean_whitespace(text)
    if not result:
        return result
    if persian_punctuation and (language == "fa" or is_persian_text(result)):
        result = result.replace("?", "؟")
        result = re.sub(r"(?<=[\u0600-\u06FF]),(?=\s|$)", "،", result)
    result = _RE_TRAILING_PARTIAL.sub("", result)
    if not result.endswith((".", "!", "?", "؟", "…", ":", "؛")):
        result += "؟" if _looks_like_question(result) and result.endswith(("چی", "چرا", "کجا", "کی", "چطور")) else "."
    result = _RE_MULTI_PUNCT.sub(r"\1", result)
    return result


def _looks_like_question(text: str) -> bool:
    lowered = text.lower().strip()
    return lowered.startswith(
        ("what", "why", "how", "when", "where", "who", "is ", "are ", "do ", "does ", "can ")
    )


def auto_capitalize(text: str) -> str:
    """Capitalise Latin sentences; Persian text is returned unchanged."""
    if not text:
        return text
    if is_persian_text(text) and not re.search(r"[A-Za-z]{3,}", text):
        return text
    result = text
    if result and result[0].islower():
        result = result[0].upper() + result[1:]
    result = _RE_SENTENCE_START.sub(lambda match: match.group(1) + match.group(2).upper(), result)
    return result


def apply_rules(text: str, rules: tuple[str, ...] | list[str]) -> str:
    """Apply literal ``find => replace`` rules from the settings."""
    result = text
    for rule in rules or ():
        if "=>" not in rule:
            continue
        find, _, replace = rule.partition("=>")
        find = find.strip()
        if not find:
            continue
        result = result.replace(find, replace.strip())
    return result


def strip_fillers(text: str) -> str:
    """Remove common hesitation sounds (opt-in only)."""
    patterns = (
        r"(?<!\w)(?:اِ|اِه|ااا|امم|اوم)(?!\w)",
        r"(?<!\w)(?:uh|um|erm|hmm)(?!\w)",
    )
    result = text
    for pattern in patterns:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    return clean_whitespace(result)


def post_process(text: str, options: PostProcessOptions | None = None) -> str:
    """Run the full dictation post-processing chain."""
    options = options or PostProcessOptions()
    result = text or ""
    if not result.strip():
        return ""
    if options.commands:
        result = apply_commands(result, options.language)
    if options.strip_filler_words:
        result = strip_fillers(result)
    if options.auto_punctuation:
        result = auto_punctuate(
            result, language=options.language, persian_punctuation=options.persian_punctuation
        )
    else:
        result = clean_whitespace(result)
    if options.auto_capitalization:
        result = auto_capitalize(result)
    if options.rules:
        result = apply_rules(result, options.rules)
    if options.fix_spacing:
        result = clean_whitespace(result)
    if options.max_length and len(result) > options.max_length:
        result = result[: options.max_length].rstrip()
    if options.trim:
        result = result.strip()
    if options.append_space and result and not result.endswith((" ", "\n")):
        result += " "
    if options.keep_trailing_newline and not result.endswith("\n"):
        result += "\n"
    return result


def looks_like_speech(text: str, *, min_characters: int = 1) -> bool:
    """Reject hallucinated silence markers such as ``[BLANK_AUDIO]`` or ``♪``."""
    cleaned = (text or "").strip()
    if len(cleaned) < min_characters:
        return False
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return False
    if all(char in "♪♫.،،؟!…-_… " for char in cleaned):
        return False
    lowered = cleaned.lower()
    for marker in ("[blank_audio]", "[silence]", "(silence)", "[music]", "(music)", "شکر"):
        if lowered == marker:
            return False
    return True


def split_into_sentences(text: str) -> list[str]:
    from ..engines.text_normalization.sentences import segment_sentences

    return [sentence.text for sentence in segment_sentences(text or "")]


def insert_text_at_cursor_safe(text: str) -> str:
    """Normalise text for clipboard/Unicode insertion (no control characters)."""
    cleaned = "".join(
        char for char in (text or "") if char in "\n\t" or ord(char) >= 0x20
    )
    return cleaned.replace("\r\n", "\n").replace("\r", "\n")
