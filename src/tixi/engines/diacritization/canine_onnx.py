"""Neural Persian diacritization through an ONNX character-level model.

The reference model is ``PedramR/canine-fa-diacritizer`` (MIT, Persian/Farsi,
132 M parameters, built on ``google/canine-s``): a CANINE encoder fine-tuned as
a **per-character token classifier** with ten labels — ``NONE``, ``FATHA``,
``DAMMA``, ``KASRA``, ``SOKUN``, ``SHADDA``, the three ``SHADDA + vowel``
combinations and ``FATHATAN``.

Why ONNX and not ``transformers`` + PyTorch?
    Torch is a ~2 GB dependency.  The exported ONNX graph is ~130 MB (int8) and
    runs on the ONNX Runtime that the Piper engine pack already installs, which
    keeps the application small while still using a real trained model.

CANINE is tokenizer-free: every Unicode character maps directly to
``input_ids = ord(char)``, with ``[CLS] = 0xE000`` and ``[SEP] = 0xE001``.
That gives an exact 1:1 alignment between predictions and source characters, so
no offset mapping is needed.

Expected model directory layout::

    <model dir>/
        model.onnx           # input_ids (+ optional attention_mask/token_type_ids)
        config.json          # must contain id2label / label2id
        metrics.json         # optional, copied into the UI as-is
        tokenizer_config.json# optional, used for the model's max length
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ...app.logging_config import get_logger
from ..diacritization.base import (
    DiacritizationEngine,
    DiacritizationMode,
    DiacritizationResult,
    DiacritizationUnavailable,
)
from ..text_normalization.persian import DIACRITIC_CHARS, ZWNJ

log = get_logger("tixi.diacritization.canine")

# CANINE special code points (see transformers.models.canine.tokenization_canine).
CLS_CODEPOINT = 0xE000
SEP_CODEPOINT = 0xE001
PAD_CODEPOINT = 0x0000
MASK_CODEPOINT = 0xE003
MAX_CHARACTERS = 2046

#: label token -> combining mark
MARK_TABLE: dict[str, str] = {
    "NONE": "",
    "FATHA": "\u064e",      # َ
    "DAMMA": "\u064f",      # ُ
    "KASRA": "\u0650",      # ِ
    "SOKUN": "\u0652",      # ْ
    "SHADDA": "\u0651",     # ّ
    "FATHATAN": "\u064b",   # ً
    "KASRATAN": "\u064d",
    "DAMMATAN": "\u064c",
    "SUKUN": "\u0652",
    "ALEF": "",
}

#: Order in which stacked marks are written (shadda always comes first).
MARK_ORDER = ("\u0651", "\u064e", "\u064f", "\u0650", "\u064b", "\u064c", "\u064d", "\u0652")

#: Characters that never take a vowel.
NON_VOWEL_CHARS = frozenset(
    " \t\n\r"
    "0123456789"
    "۰۱۲۳۴۵۶۷۸۹"
    "٠١٢٣٤٥٦٧٨٩"
    ".,!?;:،؛؟…«»\"'()[]{}<>-%/\\|@#$^&*_+=~`"
    "\u060c"       # Arabic comma
    "\u00ab\u00bb"  # guillemets
)

_PERSIAN_LETTER = re.compile(r"[\u0621-\u064a\u066e-\u06d5\u06fa-\u06fc]")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’\-]*")


@dataclass
class CanineConfig:
    """Everything read from the model directory."""

    model_path: Path
    labels: dict[int, str]
    max_length: int = MAX_CHARACTERS
    labels_count: int = 10
    metadata: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}

    @property
    def model_id(self) -> str:
        return self.model_path.parent.name if self.model_path.parent.name else self.model_path.name


class CanineOnnxDiacritizer(DiacritizationEngine):
    """Runs a CANINE per-character diacritizer with ONNX Runtime."""

    engine_id = "canine-fa-onnx"
    display_name = "CANINE Persian diacritizer (ONNX)"
    is_neural = True
    languages = ("fa",)
    supports_confidence = True
    memory_bytes = 180 * 1024 * 1024

    def __init__(
        self,
        model_path: str | Path,
        *,
        providers: Sequence[str] | None = None,
        intra_op_threads: int = 0,
        session_options: Any = None,
    ) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise DiacritizationUnavailable(f"diacritization model not found: {self.model_path}")
        try:
            import onnxruntime  # noqa: PLC0415 - optional heavy dependency
        except ImportError as exc:  # pragma: no cover - depends on the machine
            raise DiacritizationUnavailable(
                "ONNX Runtime is required for neural Persian diacritization. "
                "Install the 'Persian diacritization' engine pack from the AI Models page."
            ) from exc

        self._ort = onnxruntime
        self._session: Any = None
        self._input_names: list[str] = []
        self.config = self._read_config()
        self._providers = list(providers) if providers else _default_providers(onnxruntime)
        self._intra_threads = int(intra_op_threads or 0)
        self._session_options = session_options
        self._loaded_at = 0.0
        self._inference_count = 0

    # -- lifecycle ----------------------------------------------------------
    def _read_config(self) -> CanineConfig:
        config_file = self.model_path / "config.json"
        payload: dict[str, Any] = {}
        if config_file.exists():
            try:
                payload = json.loads(config_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
        id2label_raw = payload.get("id2label") or {}
        labels: dict[int, str] = {}
        for key, value in id2label_raw.items():
            try:
                labels[int(key)] = str(value).upper()
            except (TypeError, ValueError):
                continue
        if not labels:
            labels = {
                0: "NONE", 1: "FATHA", 2: "DAMMA", 3: "KASRA", 4: "SOKUN",
                5: "SHADDA", 6: "SHADDA_FATHA", 7: "SHADDA_DAMMA", 8: "SHADDA_KASRA",
                9: "FATHATAN",
            }
        tokenizer_config = payload.get("tokenizer_config")
        max_length = MAX_CHARACTERS
        tokenizer_file = self.model_path / "tokenizer_config.json"
        if tokenizer_file.exists():
            try:
                tokenizer_payload = json.loads(tokenizer_file.read_text(encoding="utf-8"))
                max_length = int(tokenizer_payload.get("model_max_length", max_length))
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        elif isinstance(tokenizer_config, dict):
            try:
                max_length = int(tokenizer_config.get("model_max_length", max_length))
            except (TypeError, ValueError):
                pass
        metadata: dict[str, Any] = {}
        metrics_file = self.model_path / "metrics.json"
        if metrics_file.exists():
            try:
                metadata = json.loads(metrics_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
        return CanineConfig(
            model_path=self.model_path,
            labels=labels,
            max_length=max(64, min(max_length, MAX_CHARACTERS)),
            labels_count=len(labels),
            metadata=metadata,
        )

    def load(self) -> None:
        if self._session is not None:
            return
        options = self._session_options or self._ort.SessionOptions()
        options.graph_optimization_level = self._ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if self._intra_threads > 0:
            options.intra_op_num_threads = self._intra_threads
        options.log_severity_level = 3
        started = time.perf_counter()
        try:
            self._session = self._ort.InferenceSession(
                str(self.model_path), sess_options=options, providers=self._providers
            )
        except Exception as exc:
            raise DiacritizationUnavailable(
                f"could not load the diacritization model {self.model_path.name}: {exc}"
            ) from exc
        self._input_names = [item.name for item in self._session.get_inputs()]
        self._loaded_at = time.time()
        log.info(
            "diacritization model loaded",
            extra={
                "event": "diacritizer_loaded",
                "model": self.config.model_id,
                "providers": self._session.get_providers(),
                "seconds": round(time.perf_counter() - started, 3),
                "inputs": self._input_names,
            },
        )

    def unload(self) -> None:
        if self._session is not None:
            self._session = None
            log.info("diacritization model unloaded", extra={"event": "diacritizer_unloaded"})

    def is_ready(self) -> bool:
        return self.model_path.exists()

    def describe(self) -> dict[str, Any]:
        payload = super().describe()
        payload.update(
            {
                "model_path": str(self.model_path),
                "labels": len(self.config.labels),
                "max_length": self.config.max_length,
                "loaded": self._session is not None,
                "inferences": self._inference_count,
                "metrics": self.config.metadata,
            }
        )
        return payload

    # -- tokenisation -------------------------------------------------------
    @staticmethod
    def encode(text: str) -> tuple[list[int], list[int], list[int]]:
        """``(input_ids, attention_mask, token_type_ids)`` for one string."""
        chars = list(text)
        input_ids = [CLS_CODEPOINT, *(ord(char) for char in chars), SEP_CODEPOINT]
        attention = [1] * len(input_ids)
        token_types = [0] * len(input_ids)
        return input_ids, attention, token_types

    # -- inference ----------------------------------------------------------
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
                engine_id=self.engine_id, engine_name=self.display_name, is_neural=True,
            )
        try:
            self.load()
        except DiacritizationUnavailable as exc:
            return DiacritizationResult(
                text=source, source_text=source, mode=mode_enum, engine_id=self.engine_id,
                engine_name=self.display_name, is_neural=True, warnings=[str(exc)],
            )

        windows = split_windows(source, self.config.max_length)
        output_parts: list[str] = []
        marks_added = 0
        words = 0
        confidences: list[float] = []
        warnings: list[str] = []
        cut = threshold if threshold is not None else _default_threshold(mode_enum)
        total = max(1, len(windows))

        for index, window in enumerate(windows):
            if progress:
                progress((index + 0.5) / total, f"Diacritizing part {index + 1} of {total}")
            try:
                marked, added, window_words, window_conf = self._diacritize_window(
                    window, mode_enum, cut
                )
            except Exception as exc:  # pragma: no cover - runtime failures
                log.exception("diacritization inference failed", extra={"event": "diacritize_error"})
                warnings.append(f"Inference failed on part {index + 1} ({type(exc).__name__}).")
                marked, added, window_words, window_conf = window, 0, 0, []
            output_parts.append(marked)
            marks_added += added
            words += window_words
            if window_conf:
                confidences.append(window_conf)

        result_text = "".join(output_parts)
        return DiacritizationResult(
            text=result_text,
            source_text=source,
            mode=mode_enum,
            engine_id=self.engine_id,
            engine_name=self.display_name,
            is_neural=True,
            marks_added=marks_added,
            words_processed=words,
            confidence=sum(confidences) / len(confidences) if confidences else 0.0,
            warnings=warnings,
            statistics={
                "windows": len(windows),
                "model": self.config.model_id,
                "threshold": cut,
            },
        )

    def _diacritize_window(
        self, window: str, mode: DiacritizationMode, threshold: float
    ) -> tuple[str, int, int, float]:
        import numpy as np  # noqa: PLC0415 - numpy is a core dependency

        input_ids, attention, token_types = self.encode(window)
        feed: dict[str, Any] = {}
        array_ids = np.asarray([input_ids], dtype=np.int64)
        array_attention = np.asarray([attention], dtype=np.int64)
        array_token_types = np.asarray([token_types], dtype=np.int64)
        for name in self._input_names:
            lowered = name.lower()
            if "pixel" in lowered:
                # CANINE-S accepts pixel input; the text path does not need it,
                # so feed zeros with a plausible shape only if the graph demands.
                shape = [1, 3, 32, 32]
                feed[name] = np.zeros(shape, dtype=np.float32)
            elif "token_type" in lowered:
                feed[name] = array_token_types
            elif "input_ids" in lowered:
                feed[name] = array_ids
            elif "attention" in lowered:
                feed[name] = array_attention
        outputs = self._session.run(None, feed)
        logits = _select_logits(outputs)
        if logits is None:
            raise RuntimeError("the ONNX model did not return a logits tensor")

        # logits: (1, seq, labels)
        logits = logits[0] if logits.ndim == 3 else logits
        chars = list(window)
        usable = min(len(chars), logits.shape[0] - 1)
        predictions = logits[1 : 1 + usable]

        probabilities = _softmax(predictions)
        best = probabilities.argmax(axis=-1)
        best_confidence = probabilities.max(axis=-1)

        marks_added = 0
        words = 0
        confidence_sum = 0.0
        confidence_count = 0
        output: list[str] = []
        in_word = False

        for position, char in enumerate(chars):
            output.append(char)
            if position >= usable:
                continue
            label = self.config.labels.get(int(best[position]), "NONE")
            confidence = float(best_confidence[position])
            if char.isspace() or char == ZWNJ:
                in_word = False
            elif _PERSIAN_LETTER.match(char):
                if not in_word:
                    words += 1
                in_word = True
            else:
                in_word = False
            mark = render_marks(label)
            if not mark or not _may_take_mark(char):
                continue
            if mode is DiacritizationMode.SMART and confidence < threshold:
                continue
            output.append(mark)
            marks_added += 1
            confidence_sum += confidence
            confidence_count += 1

        if len(chars) > usable:
            # Extremely defensive: never drop user text if the graph returns a
            # shorter sequence than requested.
            output.append("".join(chars[usable:]))

        self._inference_count += 1
        mean_conf = confidence_sum / confidence_count if confidence_count else 0.0
        return "".join(output), marks_added, words, mean_conf


def _may_take_mark(char: str) -> bool:
    """Vowels are only ever attached to letters."""
    if not char or char in NON_VOWEL_CHARS:
        return False
    if char in DIACRITIC_CHARS or char == ZWNJ:
        return False
    return bool(_PERSIAN_LETTER.match(char)) or "\u0600" <= char <= "\u06ff"


def render_marks(label: str) -> str:
    """``"SHADDA_KASRA"`` -> ``"ّ ِ"`` (shadda first, then the vowel)."""
    if not label or label.upper() == "NONE":
        return ""
    parts = [part for part in label.upper().split("_") if part and part != "NONE"]
    marks = [MARK_TABLE.get(part, "") for part in parts]
    joined = "".join(marks)
    if not joined:
        return ""
    ordered = [mark for mark in MARK_ORDER if mark in joined]
    return "".join(ordered) or joined


def _default_threshold(mode: DiacritizationMode) -> float:
    return 0.0 if mode is DiacritizationMode.FULL else 0.55


def _select_logits(outputs: Iterable[Any]) -> Any:
    import numpy as np  # noqa: PLC0415

    best = None
    for output in outputs:
        array = np.asarray(output)
        if array.ndim >= 2 and array.dtype in (np.float32, np.float64, np.float16):
            if best is None or array.shape[-1] > best.shape[-1]:
                best = array
    return best


def _softmax(values: Any) -> Any:
    import numpy as np  # noqa: PLC0415

    shifted = values - values.max(axis=-1, keepdims=True)
    exponentials = np.exp(shifted)
    return exponentials / exponentials.sum(axis=-1, keepdims=True)


def split_windows(text: str, max_characters: int = MAX_CHARACTERS) -> list[str]:
    """Split ``text`` into windows small enough for the model.

    Splitting happens on sentence → clause → whitespace boundaries so that
    context stays intact, and the original characters (including newlines) are
    preserved exactly: concatenating the returned windows rebuilds ``text``.
    """
    if len(text) <= max_characters:
        return [text]

    boundaries = [0]
    for match in re.finditer(r"(?<=[.!?؟…])\s|\n", text):
        boundaries.append(match.end())
    boundaries.append(len(text))

    windows: list[str] = []
    start = 0
    for boundary in boundaries:
        if boundary - start >= max_characters or boundary == len(text):
            chunk = text[start:boundary]
            while len(chunk) > max_characters:
                cut = chunk.rfind(" ", 0, max_characters)
                if cut <= 0:
                    cut = max_characters
                windows.append(chunk[:cut])
                chunk = chunk[cut:]
                start += cut
            if chunk:
                windows.append(chunk)
                start = boundary
    if start < len(text):
        windows.append(text[start:])
    return [window for window in windows if window]


def _default_providers(onnxruntime: Any) -> list[str]:
    available = onnxruntime.get_available_providers()
    ordered = [
        provider
        for provider in ("TensorrtExecutionProvider", "CUDAExecutionProvider", "DmlExecutionProvider")
        if provider in available
    ]
    ordered.append("CPUExecutionProvider")
    return ordered
