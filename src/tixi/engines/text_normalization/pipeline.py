"""The Persian text-processing pipeline used before synthesis.

``TextPipeline`` chains the deterministic stages that every TTS/voice-typing
operation shares:

    1. optional ``normalize``   — character folding, spacing, numerals
    2. optional ``diacritize``  — Persian vowel restoration (neural or lexical)
    3. optional ``chunk``       — sentence segmentation + pause hints

Each stage is individually switchable and reports what it changed, which is what
the Text-to-Speech view shows in its "Processing" panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol

from ...app.logging_config import get_logger
from .numbers import NumberRules, is_persian_text
from .persian import (
    NormalizationOptions,
    NormalizationReport,
    PersianTextNormalizer,
    text_statistics,
)
from .pronunciation import PronunciationDictionary, default_pronunciation_dictionary
from .sentences import SegmentationOptions, TextChunk, build_chunks

if TYPE_CHECKING:  # pragma: no cover
    from ..diacritization.base import DiacritizationEngine, DiacritizationResult

log = get_logger("tixi.text")


class DiacritizerLike(Protocol):
    """Structural type for anything that can restore Persian diacritics."""

    def diacritize(self, text: str, **kwargs: Any) -> "DiacritizationResult": ...


@dataclass
class PipelineOptions:
    """Switches mirroring the "Persian text processing" settings card."""

    normalize: bool = True
    diacritize: bool = False
    diacritization_mode: str = "smart"          # smart | full
    chunk: bool = True
    chunk_max_chars: int = 900
    sentence_pause_ms: int = 180
    paragraph_pause_ms: int = 400
    language: str = "fa"
    verbalise_numbers: bool = True
    apply_dictionary: bool = True
    apply_pronunciation_overrides: bool = True
    number_rules: NumberRules = field(default_factory=NumberRules)


@dataclass
class PipelineResult:
    """Everything a view needs to display before/after text side by side."""

    source_text: str
    normalized_text: str
    final_text: str
    diacritized_text: str = ""
    report: NormalizationReport | None = None
    diacritization: "DiacritizationResult | None" = None
    chunks: list[TextChunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats_before: dict[str, int] = field(default_factory=dict)
    stats_after: dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.final_text != self.source_text

    def stage_summary(self) -> list[tuple[str, str]]:
        """``[(stage, description), ...]`` for the processing checklist UI."""
        rows: list[tuple[str, str]] = []
        if self.report is not None:
            rows.append(("Normalization", self.report.summary()))
        if self.diacritization is not None:
            rows.append(("Diacritization", self.diacritization.summary()))
        if self.chunks:
            rows.append(
                (
                    "Chunking",
                    f"{len(self.chunks)} chunk(s), longest {max((c.length for c in self.chunks), default=0)} chars",
                )
            )
        return rows


class TextPipeline:
    """Configurable, reusable text processing pipeline."""

    def __init__(
        self,
        *,
        normalizer: PersianTextNormalizer | None = None,
        dictionary: PronunciationDictionary | None = None,
        diacritizer: DiacritizerLike | None = None,
    ) -> None:
        self.dictionary = dictionary or default_pronunciation_dictionary()
        self.normalizer = normalizer or PersianTextNormalizer(dictionary=self.dictionary)
        self.diacritizer = diacritizer

    # -- configuration ------------------------------------------------------
    def set_diacritizer(self, diacritizer: DiacritizerLike | None) -> None:
        self.diacritizer = diacritizer

    def set_dictionary_entries(self, entries: list[tuple[str, str, bool]]) -> None:
        self.dictionary.extend(entries)

    def set_pronunciation_overrides(self, overrides: dict[str, dict[str, str]]) -> None:
        self.dictionary.set_overrides(overrides)

    # -- main entry point ---------------------------------------------------
    def process(
        self,
        text: str,
        options: PipelineOptions | None = None,
        *,
        progress: Callable[[str, float], None] | None = None,
    ) -> PipelineResult:
        """Run the enabled stages.  Never raises for linguistic problems."""
        options = options or PipelineOptions()
        source = text or ""
        result = PipelineResult(source_text=source, normalized_text=source, final_text=source)
        result.stats_before = text_statistics(source)
        if not source.strip():
            result.stats_after = result.stats_before
            return result

        persian = is_persian_text(source)
        working = source

        if progress:
            progress("Normalizing text", 0.05)

        if options.normalize:
            report = self._run_normalisation(working, options, persian)
            result.report = report
            working = report.normalised
        else:
            report = None

        result.normalized_text = working
        result.final_text = working

        needs_diacritization = options.diacritize and persian
        if needs_diacritization:
            if self.diacritizer is None:
                result.warnings.append(
                    "Diacritization was requested but no diacritization engine is installed. "
                    "Install a Persian diacritization model from the AI Models page."
                )
            else:
                if progress:
                    progress("Adding Persian diacritics", 0.35)
                try:
                    diacritization = self.diacritizer.diacritize(  # type: ignore[union-attr]
                        working, mode=options.diacritization_mode
                    )
                    result.diacritization = diacritization
                    working = diacritization.text
                    result.diacritized_text = diacritization.text
                    result.final_text = working
                    if diacritization.warnings:
                        result.warnings.extend(diacritization.warnings)
                except Exception as exc:  # pragma: no cover - engine errors are reported
                    log.exception("diacritization failed", extra={"event": "diacritization_failed"})
                    result.warnings.append(
                        f"Diacritization failed ({type(exc).__name__}: {exc}). "
                        "The text was used without added diacritics."
                    )
        elif options.diacritize and not persian:
            result.warnings.append(
                "Diacritization was skipped because the text does not look like Persian."
            )

        if options.apply_pronunciation_overrides:
            before = working
            working = self.dictionary.apply_overrides(working)
            if working != before:
                result.final_text = working

        if options.chunk:
            if progress:
                progress("Splitting into sentences", 0.75)
            result.chunks = build_chunks(
                working,
                SegmentationOptions(
                    max_chunk_chars=options.chunk_max_chars,
                    sentence_pause_ms=options.sentence_pause_ms,
                    paragraph_pause_ms=options.paragraph_pause_ms,
                ),
            )

        result.final_text = working
        result.stats_after = text_statistics(working)
        if progress:
            progress("Ready", 1.0)
        return result

    # -- stages -------------------------------------------------------------
    def _run_normalisation(
        self, text: str, options: PipelineOptions, persian: bool
    ) -> NormalizationReport:
        number_rules = NumberRules(
            **{
                **options.number_rules.__dict__,
                "language": "fa" if persian else "en",
                "expand_abbreviations": options.number_rules.expand_abbreviations,
            }
        )
        self.normalizer.options = NormalizationOptions(
            fold_arabic_characters=persian,
            normalise_zwnj=True,
            fix_spacing=True,
            persian_punctuation=persian,
            convert_digits_to_persian=False,
            verbalise_numbers=options.verbalise_numbers,
            number_rules=number_rules,
            expand_abbreviations=options.verbalise_numbers,
            apply_dictionary=options.apply_dictionary,
            insert_zwnj_for_mi=True,
        )
        return self.normalizer.normalise_with_report(text)
