"""Engine interfaces shared by TTS, STT, diacritization and normalisation.

Everything the application does with AI goes through one of these interfaces,
which is what makes it possible to:

* lazy-load models and unload them when idle,
* swap engines without touching the UI,
* report *honest* capabilities (which languages a model really supports),
* run inference in a worker thread with cancellation and progress.
"""

from __future__ import annotations

import abc
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import numpy as np

from ..app.logging_config import get_logger

log = get_logger("tixi.engines")

ProgressCallback = Callable[[float, str], None]


class EngineError(RuntimeError):
    """Base class for engine level failures shown to the user."""


class EngineUnavailable(EngineError):
    """The engine (or one of its dependencies) is not installed."""


class ModelNotFound(EngineError):
    """The selected model could not be found on disk."""


class CancelledError(EngineError):
    """The operation was cancelled by the user."""


@dataclass
class EngineCapabilities:
    """What an engine can actually do — never speculative."""

    languages: tuple[str, ...] = ()
    voice_cloning: bool = False
    multi_speaker: bool = False
    pitch_control: bool = False
    speed_control: bool = True
    volume_control: bool = True
    pause_control: bool = True
    word_timestamps: bool = False
    segment_timestamps: bool = True
    language_detection: bool = False
    translation: bool = False
    diarization: bool = False
    streaming: bool = False
    gpu: bool = False
    offline: bool = True
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def supports(self, language: str) -> bool:
        code = (language or "").split("-")[0].lower()
        return code in {item.split("-")[0].lower() for item in self.languages} or "multi" in self.languages

    def describe(self) -> str:
        bits = [f"{len(self.languages)} language(s)" if self.languages else "language unknown"]
        for label, flag in (
            ("word timestamps", self.word_timestamps),
            ("language detection", self.language_detection),
            ("GPU acceleration", self.gpu),
            ("pitch control", self.pitch_control),
            ("speaker diarization", self.diarization),
        ):
            if flag:
                bits.append(label)
        return ", ".join(bits)


@dataclass
class Voice:
    """A voice exposed by a TTS engine."""

    voice_id: str
    name: str
    language: str = ""
    gender: str = ""
    quality: str = ""
    sample_rate: int = 0
    model_id: str = ""
    speakers: int = 1
    description: str = ""
    path: str = ""
    engine_id: str = ""
    persian_support: str = ""  # "", "native", "multilingual-verified", "unsupported"

    @property
    def display(self) -> str:
        parts = [self.name]
        if self.language:
            parts.append(f"({self.language})")
        if self.quality:
            parts.append(f"· {self.quality}")
        return " ".join(parts)


class BaseEngine(abc.ABC):
    """Common lifecycle for every engine implementation."""

    engine_id: str = "base"
    display_name: str = "Engine"
    version: str = "1.0"
    #: package(s) that must be importable for this engine to work
    requires: tuple[str, ...] = ()

    def __init__(self) -> None:
        self._loaded = False
        self._last_used = 0.0
        self._lock = threading.RLock()
        self._load_error = ""
        self._model_id = ""

    # -- lifecycle ----------------------------------------------------------
    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def load_error(self) -> str:
        return self._load_error

    def touch(self) -> None:
        import time

        self._last_used = time.time()

    @property
    def idle_seconds(self) -> float:
        import time

        return time.time() - self._last_used if self._last_used else 0.0

    @classmethod
    def is_available(cls) -> bool:
        """Is the underlying runtime importable?"""
        from importlib.util import find_spec

        for module in cls.requires:
            try:
                if find_spec(module) is None:
                    return False
            except (ImportError, ValueError):  # pragma: no cover
                return False
        return True

    @classmethod
    def unavailable_reason(cls) -> str:
        missing = []
        from importlib.util import find_spec

        for module in cls.requires:
            try:
                if find_spec(module) is None:
                    missing.append(module)
            except (ImportError, ValueError):  # pragma: no cover
                missing.append(module)
        if not missing:
            return ""
        from ..models.engine_packs import pack_for_modules

        pack = pack_for_modules(missing)
        if pack:
            return (
                f"The {cls.display_name} runtime is not installed "
                f"(missing: {', '.join(missing)}). Install the “{pack.name}” engine pack "
                "from the AI Models page to enable it."
            )
        return f"The {cls.display_name} runtime is not installed (missing: {', '.join(missing)})."

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities()

    def unload(self) -> None:
        """Release model resources."""
        with self._lock:
            self._loaded = False

    def describe(self) -> dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "name": self.display_name,
            "version": self.version,
            "available": self.is_available(),
            "loaded": self.is_loaded,
            "model_id": self.model_id,
            "capabilities": {
                "languages": list(self.capabilities.languages),
                "word_timestamps": self.capabilities.word_timestamps,
                "diarization": self.capabilities.diarization,
            },
        }


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------
@dataclass
class SynthesisRequest:
    """One synthesis job (a chunk of text)."""

    text: str
    voice_id: str = ""
    language: str = "fa"
    speed: float = 1.0
    pitch: float = 1.0
    volume: float = 1.0
    speaker_id: int | None = None
    sample_rate: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SynthesisResult:
    """Rendered audio for one request."""

    samples: np.ndarray
    sample_rate: int
    voice_id: str = ""
    engine_id: str = ""
    text: str = ""
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    native_format: str = "wav"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.duration_s and self.sample_rate:
            self.duration_s = float(self.samples.shape[0]) / self.sample_rate
        if self.samples.dtype != np.float32:
            self.samples = self.samples.astype(np.float32)


class TTSEngine(BaseEngine):
    """Text-to-speech engine interface."""

    @abc.abstractmethod
    def voices(self) -> list[Voice]:
        """Voices the engine can currently offer (installed models only)."""

    @abc.abstractmethod
    def synthesize(
        self,
        request: SynthesisRequest,
        *,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> SynthesisResult:
        """Render ``request.text`` to audio."""

    def load_voice(self, voice: Voice) -> None:
        """Optional hook: pre-load a specific voice."""

    def supports_voice(self, voice_id: str) -> bool:
        return any(voice.voice_id == voice_id for voice in self.voices())

    def preflight(self, request: SynthesisRequest) -> list[str]:
        """Return human-readable problems before synthesis starts."""
        issues: list[str] = []
        if not request.text.strip():
            issues.append("There is no text to synthesise.")
        voices = {voice.voice_id for voice in self.voices()}
        if not voices:
            issues.append(
                f"{self.display_name} has no voice models installed. "
                "Download one from the AI Models page."
            )
        elif request.voice_id and request.voice_id not in voices:
            issues.append(
                f"The selected voice ({request.voice_id}) is not installed for {self.display_name}."
            )
        return issues


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------
@dataclass
class TranscriptionRequest:
    """One speech-recognition job."""

    audio: np.ndarray | str | None = None
    sample_rate: int = 16000
    language: str = "fa"
    model_id: str = ""
    task: str = "transcribe"
    beam_size: int = 5
    temperature: float = 0.0
    vad_filter: bool = True
    vad_min_silence_ms: int = 500
    word_timestamps: bool = False
    condition_on_previous_text: bool = True
    initial_prompt: str = ""
    diarization: bool = False
    compute_type: str = "auto"
    max_duration_s: float | None = None
    preprocess: dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscriptSegment:
    index: int
    start: float
    end: float
    text: str
    confidence: float = 0.0
    speaker: str = ""
    words: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "confidence": round(self.confidence, 4),
        }
        if self.speaker:
            payload["speaker"] = self.speaker
        if self.words:
            payload["words"] = self.words
        return payload


@dataclass
class TranscriptionResult:
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str = ""
    language_probability: float = 0.0
    duration_s: float = 0.0
    model_id: str = ""
    engine_id: str = ""
    speaker_count: int = 0
    warnings: list[str] = field(default_factory=list)
    processing_s: float = 0.0
    words: list[dict[str, Any]] = field(default_factory=list)

    @property
    def speed_ratio(self) -> float:
        if self.processing_s <= 0 or self.duration_s <= 0:
            return 0.0
        return self.duration_s / self.processing_s

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "language_probability": round(self.language_probability, 3),
            "duration_s": round(self.duration_s, 3),
            "model_id": self.model_id,
            "engine_id": self.engine_id,
            "segments": [segment.as_dict() for segment in self.segments],
            "warnings": self.warnings,
        }


class STTEngine(BaseEngine):
    """Speech-to-text engine interface."""

    @abc.abstractmethod
    def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> TranscriptionResult:
        """Transcribe audio to text."""

    def supported_languages(self) -> list[tuple[str, str]]:
        """``[(code, display name), ...]`` for the language picker."""
        return [(code, code) for code in self.capabilities.languages]

    def detect_language(self, audio: np.ndarray, sample_rate: int) -> tuple[str, float]:
        """Optional: detect the spoken language."""
        return "", 0.0


def empty_audio() -> np.ndarray:
    return np.zeros(0, dtype=np.float32)


def iter_chunks(items: Iterable[Any], size: int) -> Iterable[list[Any]]:
    bucket: list[Any] = []
    for item in items:
        bucket.append(item)
        if len(bucket) >= size:
            yield bucket
            bucket = []
    if bucket:
        yield bucket
