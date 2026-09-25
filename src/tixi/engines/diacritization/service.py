"""Diacritization service: engine selection, learned corrections, safety nets.

The service is what the rest of the application talks to.  It:

* picks the configured engine (neural model first, lexicon fallback second),
* applies the user's pronunciation overrides and learned corrections,
* protects non-Persian content (Latin words, URLs, code, numbers, punctuation)
  from being modified,
* reports honestly when it had to fall back to the approximate engine,
* never lets a broken engine destroy the user's text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ...app.logging_config import get_logger
from ..diacritization.base import (
    DiacritizationEngine,
    DiacritizationMode,
    DiacritizationResult,
    DiacritizationUnavailable,
)
from ..diacritization.canine_onnx import CanineOnnxDiacritizer
from ..diacritization.lexicon import LexiconDiacritizer, ezafe_needed
from ..text_normalization.persian import DIACRITIC_CHARS, ZWNJ, has_diacritics, strip_diacritics

log = get_logger("tixi.diacritization")

#: Regions that must never be modified by a diacritizer.
_PROTECTED_PATTERNS = (
    re.compile(r"https?://\S+"),
    re.compile(r"www\.\S+"),
    re.compile(r"[A-Za-z][A-Za-z0-9'’._\-]*"),
    re.compile(r"`[^`]*`"),
    re.compile(r"\b\S+@\S+\.\S+\b"),
    re.compile(r"\d[\d.,:/\\-]*"),
)

_PERSIAN_WORD = re.compile(r"[\u0600-\u06FF\u200c]+")


class PlaceholderLossError(RuntimeError):
    """Raised when an engine modified a protected (non-modifiable) span."""


@dataclass
class DiacritizationSettings:
    """Runtime view of the settings that influence diacritization."""

    mode: str = "smart"
    threshold: float = 0.55
    use_neural: bool = True
    insert_ezafe: bool = True
    preserve_latin: bool = True
    learn_from_corrections: bool = True


@dataclass
class DiacritizationService:
    """Facade over the available diacritization engines."""

    settings: DiacritizationSettings = field(default_factory=DiacritizationSettings)
    neural: DiacritizationEngine | None = None
    lexicon: DiacritizationEngine = field(default_factory=LexiconDiacritizer)
    overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    learned: dict[str, str] = field(default_factory=dict)

    # -- engine management --------------------------------------------------
    def set_neural_model(self, model_path: str | Path | None, **kwargs: Any) -> bool:
        """Attach a CANINE ONNX model directory.  Returns ``True`` on success."""
        if model_path is None:
            self.neural = None
            return False
        try:
            engine = CanineOnnxDiacritizer(model_path, **kwargs)
        except DiacritizationUnavailable as exc:
            log.warning("neural diacritizer unavailable", extra={"event": "diacritizer_unavailable", "reason": str(exc)})
            self.neural = None
            return False
        self.neural = engine
        return True

    @property
    def active_engine(self) -> DiacritizationEngine:
        if self.settings.use_neural and self.neural is not None and self.neural.is_ready():
            return self.neural
        return self.lexicon

    @property
    def is_neural_active(self) -> bool:
        return self.active_engine is self.neural

    def unload(self) -> None:
        for engine in (self.neural, self.lexicon):
            if engine is not None:
                engine.unload()

    def describe(self) -> dict[str, Any]:
        return {
            "active": self.active_engine.engine_id,
            "neural_available": self.neural is not None,
            "neural_model": getattr(self.neural, "model_path", None),
            "mode": self.settings.mode,
            "lexicon_entries": len(getattr(self.lexicon, "entries", {}) or {}),
            "learned_entries": len(self.learned),
        }

    # -- user data ----------------------------------------------------------
    def load_overrides(self, overrides: Mapping[str, Any]) -> None:
        self.overrides = {str(key): dict(value) if isinstance(value, dict) else {"diacritized": str(value)}
                          for key, value in (overrides or {}).items()}
        if isinstance(self.lexicon, LexiconDiacritizer):
            self.lexicon.load_overrides(self.overrides)

    def load_learned(self, learned: Mapping[str, str]) -> None:
        self.learned = dict(learned or {})
        if isinstance(self.lexicon, LexiconDiacritizer):
            for word, value in self.learned.items():
                self.lexicon.add_entry(word, value, learned=True)

    def remember_correction(self, original: str, corrected: str) -> dict[str, str]:
        """Learn word-level corrections from a user edit.

        Returns the mapping of words that were learned, which the caller stores
        in the database so the correction survives restarts.
        """
        learned: dict[str, str] = {}
        original_words = _PERSIAN_WORD.findall(strip_diacritics(original or ""))
        corrected_words = _PERSIAN_WORD.findall(corrected or "")
        if len(original_words) != len(corrected_words):
            return learned
        for bare, marked in zip(original_words, corrected_words):
            if bare == strip_diacritics(marked):
                if has_diacritics(marked):
                    learned[bare] = marked
                    if isinstance(self.lexicon, LexiconDiacritizer):
                        self.lexicon.add_entry(bare, marked, learned=True)
        if learned:
            log.info(
                "learned diacritization corrections",
                extra={"event": "diacritization_learned", "count": len(learned)},
            )
        return learned

    # -- main entry point ---------------------------------------------------
    def diacritize(
        self,
        text: str,
        *,
        mode: str | None = None,
        threshold: float | None = None,
        use_neural: bool | None = None,
        progress: Callable[[float, str], None] | None = None,
    ) -> DiacritizationResult:
        mode_enum = DiacritizationMode.parse(mode or self.settings.mode)
        source = text or ""
        if not source.strip():
            return DiacritizationResult(text=source, source_text=source, mode=mode_enum)
        if not _PERSIAN_WORD.search(source):
            return DiacritizationResult(
                text=source,
                source_text=source,
                mode=mode_enum,
                engine_id="none",
                engine_name="No engine",
                is_neural=False,
                warnings=["No Persian script was found, so nothing was diacritized."],
            )

        engine = self._select_engine(use_neural)
        masked, restore = self._mask_protected(source)
        try:
            result = engine.diacritize(
                masked,
                mode=mode_enum,
                threshold=(
                    threshold if threshold is not None else self.settings.threshold
                ),
                progress=progress,
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("diacritization engine failed", extra={"event": "diacritization_error"})
            return DiacritizationResult(
                text=source,
                source_text=source,
                mode=mode_enum,
                engine_id=getattr(engine, "engine_id", "unknown"),
                engine_name=getattr(engine, "display_name", "unknown"),
                is_neural=getattr(engine, "is_neural", False),
                warnings=[
                    f"The diacritization engine failed ({type(exc).__name__}: {exc}); "
                    "the original text is unchanged."
                ],
            )

        try:
            restored = restore(result.text)
        except PlaceholderLossError as exc:
            log.warning(
                "diacritization engine damaged protected spans; keeping the original text",
                extra={"event": "diacritization_placeholder_loss", "reason": str(exc)},
            )
            return DiacritizationResult(
                text=source,
                source_text=source,
                mode=mode_enum,
                engine_id=result.engine_id,
                engine_name=result.engine_name,
                is_neural=result.is_neural,
                warnings=[
                    "The diacritization engine did not return every protected span "
                    "unchanged, so the original text was kept. Please report this."
                ],
            )

        restored = self._apply_overrides(restored)
        if mode_enum is DiacritizationMode.SMART and self.settings.insert_ezafe:
            restored = self._add_conservative_ezafe(restored)

        result.text = restored
        result.source_text = source
        if not engine.is_neural:
            note = (
                "Approximate mode: the built-in Persian pronunciation lexicon was used. "
                "Install the Persian diacritization model (AI Models ▸ Persian Diacritization) "
                "for full, context-aware coverage."
            )
            if note not in result.warnings:
                result.warnings.append(note)
        result.statistics.setdefault("engine", result.engine_id)
        result.statistics["protected_spans"] = len(getattr(restore, "placeholders", []) or [])
        return result

    # -- helpers ------------------------------------------------------------
    def _select_engine(self, use_neural: bool | None) -> DiacritizationEngine:
        want_neural = self.settings.use_neural if use_neural is None else use_neural
        if want_neural and self.neural is not None and self.neural.is_ready():
            return self.neural
        return self.lexicon

    def _mask_protected(self, text: str) -> tuple[str, Callable[[str], str]]:
        """Replace Latin/URL/code spans with opaque sentinels.

        The sentinel is a control character that no model can emit, so the
        restoration step can verify that the engine returned every span intact.
        If it did not, the caller discards the engine output and keeps the
        original text instead of risking a mangled document.
        """
        if not self.settings.preserve_latin:
            def identity(value: str) -> str:
                return value

            return text, identity

        placeholders: list[str] = []
        working = text
        sentinel = "\u0002"
        expected = f"{sentinel}0{sentinel}"

        def _mask(text_value: str, pattern: re.Pattern[str]) -> str:
            def repl(match: re.Match[str]) -> str:
                value = match.group(0)
                if not value.strip():
                    return value
                placeholders.append(value)
                return f"{sentinel}{len(placeholders) - 1}{sentinel}"

            return pattern.sub(repl, text_value)

        for pattern in _PROTECTED_PATTERNS:
            working = _mask(working, pattern)

        if expected not in working:
            def passthrough(value: str) -> str:
                return value

            return text, passthrough

        def restore(text_value: str) -> str:
            if text_value.count(sentinel) != 2 * len(placeholders):
                raise PlaceholderLossError(
                    "the diacritization engine did not return every protected span"
                )

            def repl(match: re.Match[str]) -> str:
                index = int(match.group(1))
                return placeholders[index] if 0 <= index < len(placeholders) else ""

            restored = re.sub(rf"{sentinel}(\d+){sentinel}", repl, text_value)
            if sentinel in restored:  # pragma: no cover - covered by the count check
                raise PlaceholderLossError("unresolved placeholder after restoration")
            return restored

        return working, restore

    def _apply_overrides(self, text: str) -> str:
        if not self.overrides:
            return text
        result = text
        for word, payload in self.overrides.items():
            replacement = payload.get("diacritized") if isinstance(payload, dict) else None
            if not replacement:
                continue
            pattern = re.compile(rf"(?<![\u0600-\u06FF\u200c]){re.escape(strip_diacritics(word))}(?![\u0600-\u06FF\u200c])")
            result = pattern.sub(replacement, result)
        return result

    def _add_conservative_ezafe(self, text: str) -> str:
        """Add the Ezafe kasra only where the context makes it obligatory."""
        words = list(_PERSIAN_WORD.finditer(text))
        if len(words) < 2:
            return text
        output: list[str] = []
        cursor = 0
        for index, match in enumerate(words):
            word = match.group(0)
            output.append(text[cursor : match.start()])
            next_word = words[index + 1].group(0) if index + 1 < len(words) else ""
            if (
                has_diacritics(word)
                and not word.endswith((KASRA, FATHA, DAMMA, ZWNJ + KASRA))
                and next_word
                and ezafe_needed(word, next_word)
                and _word_takes_ezafe(word)
            ):
                output.append(word + KASRA)
            else:
                output.append(word)
            cursor = match.end()
        output.append(text[cursor:])
        return "".join(output)

    def latest_result(self) -> DiacritizationResult | None:
        return self._last

    _last: DiacritizationResult | None = None


KASRA = "\u0650"
FATHA = "\u064e"
DAMMA = "\u064f"

#: Word shapes that reliably take an Ezafe kasra at the end.
_EZAFE_ENDINGS = (
    "\u0647",        # ه
    "\u06cc",        # ی
    "\u0627\u062a",  # ات
    "\u0627\u0646",  # ان
    ZWNJ + "\u0647\u0627",   # ‌ها
    ZWNJ + "\u0647\u0627\u06cc",  # ‌های
)


def _word_takes_ezafe(word: str) -> bool:
    bare = strip_diacritics(word)
    return bare.endswith(_EZAFE_ENDINGS)


def coverage_report(text: str, lexicon: Mapping[str, str]) -> dict[str, Any]:
    """Diagnostics: how much of ``text`` the lexicon knows."""
    words = _PERSIAN_WORD.findall(text or "")
    known = sum(1 for word in words if strip_diacritics(word) in lexicon)
    return {
        "words": len(words),
        "known": known,
        "coverage": (known / len(words)) if words else 0.0,
    }


class DiacritizationPipelineStage:
    """Adapter so the text pipeline can call the service like an engine."""

    def __init__(self, service: DiacritizationService) -> None:
        self.service = service

    def diacritize(self, text: str, **kwargs: Any) -> DiacritizationResult:
        return self.service.diacritize(text, **kwargs)
