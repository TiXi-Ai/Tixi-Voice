"""Sentence segmentation and chunk packing."""

from __future__ import annotations

from tixi.engines.text_normalization.sentences import (
    SegmentationOptions,
    build_chunks,
    segment_sentences,
)


def test_persian_sentences_are_split() -> None:
    sentences = segment_sentences("سلام. حال شما چطور است؟ خوبم!")
    assert len(sentences) == 3


def test_abbreviations_do_not_split() -> None:
    sentences = segment_sentences("نمونه Dr. Smith آمد. تمام.")
    assert len(sentences) == 2


def test_chunks_respect_the_limit() -> None:
    text = " ".join(f"جمله شماره {index} است." for index in range(60))
    chunks = build_chunks(text, SegmentationOptions(max_chunk_chars=120))
    assert chunks
    assert all(chunk.length <= 160 for chunk in chunks)


def test_empty_input_yields_no_chunks() -> None:
    assert build_chunks("") == []
