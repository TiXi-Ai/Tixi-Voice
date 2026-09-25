"""Diacritization engine interface.

A diacritization engine restores Persian short-vowel marks (حَرَکات) — and the
Ezafe kasra — to undiacritized text so that a TTS model or a human reader can
produce the right pronunciation.

Only *real* engines implement this interface:

* :class:`~tixi.engines.diacritization.canine_onnx.CanineOnnxDiacritizer`
  — a neural per-character sequence labeller exported to ONNX.
* :class:`~tixi.engines.diacritization.lexicon.LexiconDiacritizer`
  — an explicitly *heuristic* fallback: a curated pronunciation lexicon plus
  morphology/context rules.  It is always labelled as approximate in the UI and
  is never presented as a neural model.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DiacritizationMode(str, Enum):
    """How aggressively marks are inserted."""

    SMART = "smart"   # only marks that improve pronunciation (recommended)
    FULL = "full"     # every mark the engine can predict

    @classmethod
    def parse(cls, value: str | "DiacritizationMode") -> "DiacritizationMode":
        if isinstance(value, DiacritizationMode):
            return value
        normalised = (value or "smart").strip().lower()
        if normalised in ("full", "complete", "all", "کامل"):
            return cls.FULL
        return cls.SMART


@dataclass
class DiacritizationResult:
    """Output of one diacritization pass."""

    text: str
    source_text: str = ""
    mode: DiacritizationMode = DiacritizationMode.SMART
    engine_id: str = ""
    engine_name: str = ""
    is_neural: bool = False
    marks_added: int = 0
    words_processed: int = 0
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    statistics: dict[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.text != self.source_text

    def summary(self) -> str:
        kind = "neural model" if self.is_neural else "lexicon rules (approximate)"
        if self.marks_added == 0:
            return f"No marks added ({kind})."
        confidence = f", mean confidence {self.confidence:.0%}" if self.confidence else ""
        return (
            f"{self.marks_added} mark(s) across {self.words_processed} word(s) "
            f"using {kind}{confidence}."
        )


class DiacritizationEngine(abc.ABC):
    """Base class for every diacritization engine."""

    #: stable identifier used in settings and the database
    engine_id: str = "base"
    #: human readable name shown in the UI
    display_name: str = "Diacritization engine"
    #: ``True`` for models that were trained for the task (never for heuristics)
    is_neural: bool = False
    #: languages the engine can process, as BCP-47 codes
    languages: tuple[str, ...] = ("fa",)
    #: does the engine report per-mark confidence?
    supports_confidence: bool = False
    #: approximate memory footprint when loaded, in bytes
    memory_bytes: int = 0

    @abc.abstractmethod
    def diacritize(self, text: str, *, mode: str | DiacritizationMode = DiacritizationMode.SMART,
                   threshold: float | None = None, progress: Any = None) -> DiacritizationResult:
        """Add diacritics to ``text``.

        Implementations must never raise for ordinary linguistic input: they
        return the original text (plus a warning) when they cannot process it.
        """

    def unload(self) -> None:
        """Release model resources.  Engines that keep nothing loaded ignore this."""

    def is_ready(self) -> bool:
        return True

    def describe(self) -> dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "name": self.display_name,
            "neural": self.is_neural,
            "languages": list(self.languages),
            "ready": self.is_ready(),
        }


class DiacritizationUnavailable(RuntimeError):
    """Raised when an engine cannot be constructed on this machine."""
