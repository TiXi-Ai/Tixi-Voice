"""Sentence segmentation and synthesis chunking.

Long documents must be synthesised in chunks that respect linguistic
boundaries: a TTS model given 40 000 characters at once will either fail or
produce garbled prosody.  This module splits text into sentences, then packs
sentences into chunks of at most *N* characters while keeping:

* sentence-final punctuation with its sentence,
* paragraph boundaries (which become longer pauses),
* decimals, abbreviations, URLs and version numbers un-split,
* Persian half-spaces (ZWNJ) glued to their word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, Literal

from .persian import ZWNJ, strip_rtl_markers

#: Sentence-final characters in Persian and English.
SENTENCE_ENDINGS = ".!?؟…۔"
#: Characters that end a *clause* (used when a sentence is too long).
CLAUSE_ENDINGS = "،,;؛:—–"

#: Abbreviations whose trailing dot is not a sentence boundary.
_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "jr.", "sr.", "vs.", "etc.",
    "e.g.", "i.e.", "fig.", "no.", "approx.", "min.", "max.", "inc.", "ltd.",
    "co.", "dept.", "univ.", "est.", "ر.ک.", "دکتر", "مهندس", "پروفسور",
}

_RE_URL = re.compile(r"https?://\S+|www\.\S+")
_RE_VERSION = re.compile(r"\bv?\d+\.\d+(?:\.\d+)*\b")
_RE_DECIMAL = re.compile(r"\d+[.,٫]\d+")
_RE_INITIALS = re.compile(r"\b[A-Z]\.(?=\s*[A-Z])")
_RE_LIST_MARKER = re.compile(r"^\s*(?:[-*•‣▪◦]|\d+[.)]|[۰-۹]+[.)]|\(\d+\))\s*")
_RE_MULTI_NEWLINE = re.compile(r"\n{2,}")
_RE_SENTENCE_SPLIT = re.compile(rf"([{re.escape(SENTENCE_ENDINGS)}]+[\"'»”’)\]]*)")
_RE_CLAUSE_SPLIT = re.compile(rf"([{re.escape(CLAUSE_ENDINGS)}])")


@dataclass
class Sentence:
    """A single sentence with its position in the source document."""

    text: str
    start: int
    end: int
    paragraph_index: int = 0
    index: int = 0
    is_heading: bool = False

    @property
    def length(self) -> int:
        return len(self.text)

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return self.text


@dataclass
class TextChunk:
    """A synthesis unit handed to a TTS engine."""

    text: str
    pause_ms: int = 0
    kind: Literal["sentence", "clause", "paragraph", "heading"] = "sentence"
    sentence_indexes: list[int] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.text)


@dataclass
class SegmentationOptions:
    """Tunables for :func:`segment_sentences` and :func:`build_chunks`."""

    max_chunk_chars: int = 900
    min_chunk_chars: int = 40
    sentence_pause_ms: int = 180
    paragraph_pause_ms: int = 400
    heading_pause_ms: int = 500
    keep_line_breaks: bool = True
    split_on_clauses: bool = True


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------
def segment_sentences(text: str, *, paragraphs: bool = True) -> list[Sentence]:
    """Split ``text`` into sentences, preserving offsets and paragraphs."""
    if not text or not text.strip():
        return []

    sentences: list[Sentence] = []
    paragraph_index = 0
    cursor = 0
    blocks = _RE_MULTI_NEWLINE.split(text) if paragraphs else [text]
    running_offset = 0

    for block_raw in blocks:
        block = block_raw
        block_offset = text.find(block, running_offset)
        if block_offset < 0:  # pragma: no cover - defensive
            block_offset = running_offset
        running_offset = block_offset + len(block) + 2
        paragraph_index += 1

        lines = block.splitlines()
        line_offset = block_offset
        for line in lines:
            stripped = line.strip()
            if not stripped:
                line_offset += len(line) + 1
                paragraph_index += 1
                continue
            indent = line.index(stripped) if stripped in line else 0
            position = line_offset + indent
            is_heading = _looks_like_heading(stripped)
            body = _RE_LIST_MARKER.sub("", stripped)
            if body != stripped:
                position += len(stripped) - len(body)
            for piece in _split_line(body):
                cleaned = piece.strip()
                if not cleaned:
                    continue
                offset_in_body = body.find(cleaned)
                sentences.append(
                    Sentence(
                        text=cleaned,
                        start=position + max(0, offset_in_body),
                        end=position + max(0, offset_in_body) + len(cleaned),
                        paragraph_index=paragraph_index,
                        index=len(sentences),
                        is_heading=is_heading,
                    )
                )
                position += offset_in_body + len(cleaned)
            line_offset += len(line) + 1
        cursor = block_offset  # noqa: F841 - kept for readability/debugging

    return sentences


def _split_line(line: str) -> list[str]:
    """Split one line into sentence-sized pieces."""
    if not line:
        return []
    protected, restore = _protect_spans(line)
    pieces: list[str] = []
    buffer = ""
    index = 0
    while index < len(protected):
        char = protected[index]
        buffer += char
        if char in SENTENCE_ENDINGS:
            # Consume trailing punctuation/quotes/brackets.
            while index + 1 < len(protected) and protected[index + 1] in '"\'»”’)]}؟!.…':
                index += 1
                buffer += protected[index]
            # A sentence must be followed by whitespace or end-of-line.
            if index + 1 >= len(protected) or protected[index + 1].isspace():
                if not _is_abbreviation(buffer):
                    stripped = buffer.strip()
                    if stripped:
                        pieces.append(stripped)
                    buffer = ""
        index += 1
    tail = buffer.strip()
    if tail:
        pieces.append(tail)
    return [restore(piece) for piece in pieces] if pieces else [restore(line)]


def _protect_spans(line: str) -> tuple[str, callable]:  # type: ignore[valid-type]
    """Replace dots inside URLs/decimals/versions/initials with a sentinel."""
    sentinel = "\u0001"
    placeholders: list[str] = []
    result = line

    def _mask(pattern: re.Pattern[str], text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            value = match.group(0)
            placeholders.append(value)
            return f"{sentinel}{len(placeholders) - 1}{sentinel}"

        return pattern.sub(repl, text)

    for pattern in (_RE_URL, _RE_VERSION, _RE_DECIMAL, _RE_INITIALS):
        result = _mask(pattern, result)

    def restore(text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            return placeholders[int(match.group(1))]

        return re.sub(rf"{sentinel}(\d+){sentinel}", repl, text)

    return result, restore


def _is_abbreviation(piece: str) -> bool:
    """True when the *tail* of ``piece`` is a known abbreviation, not an end.

    Only the last one or two tokens are inspected, so "نمونه Dr." is recognised
    as an abbreviation while "او دکتر شد." still ends a sentence.
    """
    stripped = piece.strip()
    if not stripped or not stripped.endswith("."):
        return False
    tokens = stripped.split()
    tail = tokens[-1].lower()
    if tail in _ABBREVIATIONS or tail.rstrip(".") in _ABBREVIATIONS:
        return True
    if len(tokens) >= 2:
        joined = " ".join(token.lower() for token in tokens[-2:])
        if joined in _ABBREVIATIONS:
            return True
    return False


def _looks_like_heading(line: str) -> bool:
    if len(line) > 80 or line.endswith(tuple(SENTENCE_ENDINGS)):
        return False
    if line.startswith("#"):
        return True
    letters = [char for char in line if char.isalpha()]
    if not letters:
        return False
    uppercase = sum(1 for char in letters if char.isupper())
    return uppercase / max(1, len(letters)) > 0.75


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def build_chunks(
    text: str, options: SegmentationOptions | None = None
) -> list[TextChunk]:
    """Pack sentences into engine-sized chunks with pause hints."""
    options = options or SegmentationOptions()
    sentences = segment_sentences(text)
    if not sentences:
        stripped = (text or "").strip()
        return [TextChunk(stripped)] if stripped else []

    chunks: list[TextChunk] = []
    buffer: list[Sentence] = []
    buffer_len = 0
    previous_paragraph = sentences[0].paragraph_index

    def flush(kind: str = "sentence", pause_ms: int | None = None) -> None:
        nonlocal buffer, buffer_len
        if not buffer:
            return
        text_value = " ".join(sentence.text for sentence in buffer)
        delay = (
            pause_ms
            if pause_ms is not None
            else (options.heading_pause_ms if buffer[-1].is_heading else options.sentence_pause_ms)
        )
        chunks.append(
            TextChunk(
                text=text_value,
                pause_ms=delay,
                kind=kind,  # type: ignore[arg-type]
                sentence_indexes=[sentence.index for sentence in buffer],
            )
        )
        buffer = []
        buffer_len = 0

    for sentence in sentences:
        if sentence.paragraph_index != previous_paragraph:
            flush("paragraph", options.paragraph_pause_ms)
            previous_paragraph = sentence.paragraph_index

        if sentence.length > options.max_chunk_chars and options.split_on_clauses:
            flush()
            for piece in _split_long_sentence(sentence, options.max_chunk_chars):
                chunks.append(
                    TextChunk(
                        text=piece,
                        pause_ms=options.sentence_pause_ms,
                        kind="clause",
                        sentence_indexes=[sentence.index],
                    )
                )
            continue

        projected = buffer_len + sentence.length + (1 if buffer else 0)
        if buffer and projected > options.max_chunk_chars:
            flush()
        buffer.append(sentence)
        buffer_len = projected

    flush()
    return [chunk for chunk in chunks if chunk.text.strip()]


def _split_long_sentence(sentence: Sentence, limit: int) -> list[str]:
    """Split an over-long sentence at clause boundaries, then at whitespace."""
    protected, restore = _protect_spans(sentence.text)
    parts = [part for part in _RE_CLAUSE_SPLIT.split(protected) if part.strip()]
    pieces: list[str] = []
    buffer = ""
    for part in parts:
        if len(buffer) + len(part) + 1 > limit and buffer:
            pieces.append(restore(buffer.strip()))
            buffer = ""
        if len(part) > limit:
            words = part.split()
            line = ""
            for word in words:
                if len(line) + len(word) + 1 > limit and line:
                    pieces.append(restore(line.strip()))
                    line = ""
                line += word + " "
            buffer = line.strip()
            continue
        buffer += part + (" " if not part.endswith(" ") else "")
    if buffer.strip():
        pieces.append(restore(buffer.strip()))
    return [piece for piece in pieces if piece]


def chunks_from_segments(
    segments: list[dict], options: SegmentationOptions | None = None
) -> list[TextChunk]:
    """Chunk a *transcript* (speaker turns / subtitle segments) for playback."""
    options = options or SegmentationOptions()
    chunks: list[TextChunk] = []
    for segment in segments:
        text_value = str(segment.get("text", "")).strip()
        if not text_value:
            continue
        chunks.append(
            TextChunk(
                text=text_value,
                pause_ms=options.sentence_pause_ms,
                kind="sentence",
                sentence_indexes=[int(segment.get("index", 0) or 0)],
            )
        )
    return chunks


def estimate_duration_ms(text: str, *, characters_per_second: float = 13.0) -> int:
    """Rough spoken-duration estimate (used for progress/ETA display).

    Persian speech averages ~12-14 characters per second with a normal rate;
    the constant is deliberately conservative and clearly labelled as an
    estimate in the UI.
    """
    stripped = strip_rtl_markers(text or "")
    if not stripped:
        return 0
    return int(max(1.0, len(stripped) / max(1.0, characters_per_second)) * 1000)


def iter_sentence_texts(text: str) -> Iterator[str]:
    for sentence in segment_sentences(text):
        yield sentence.text


def sentence_count(text: str) -> int:
    return len(segment_sentences(text))


def join_with_pauses(chunks: list[TextChunk], export_text: bool = False) -> str:
    """Join chunks back into plain text (optionally marking pauses)."""
    if export_text:
        return "\n".join(chunk.text for chunk in chunks)
    return " ".join(chunk.text for chunk in chunks)
