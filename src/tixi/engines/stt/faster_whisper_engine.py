"""Speech recognition with faster-whisper (CTranslate2 Whisper).

Why faster-whisper:

* **MIT / Apache-2.0** licensed, actively maintained by SYSTRAN,
* runs fully offline on CPU (int8) or NVIDIA GPUs (float16),
* 99 languages including Persian, with language auto-detection,
* word-level timestamps and a built-in Silero VAD filter,
* ~4× faster than reference Whisper at the same accuracy.

It is installed as an on-demand *engine pack* (``ctranslate2`` is ~37 MB) so the
application installer stays small; without it, the UI explains exactly what to
install instead of failing.

Honest limitations, surfaced in the UI rather than hidden:

* **No speaker diarization** — Whisper has no concept of speakers, so Tixi Voice
  does not offer it unless a diarization engine pack is present.  Every place
  that exports speaker columns says so.
* No streaming partial results in this version (segments arrive after decoding).
* ``large-v3`` on a CPU-only machine is slow by nature; the model picker shows
  the measured speed on this machine after the first run.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from ...app.logging_config import get_logger
from ...audio.dsp import to_float32, to_mono
from ...models.model_registry import InstalledModel, ModelRegistry
from ..base import (
    EngineCapabilities,
    EngineUnavailable,
    ModelNotFound,
    ProgressCallback,
    STTEngine,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptSegment,
)

log = get_logger("tixi.stt.whisper")

#: Whisper's 99 languages (subset shown in the UI as the common ones).
WHISPER_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("fa", "Persian (فارسی)"),
    ("en", "English"),
    ("ar", "Arabic (العربية)"),
    ("tr", "Turkish (Türkçe)"),
    ("ku", "Kurdish (Kurdî)"),
    ("ur", "Urdu (اردو)"),
    ("he", "Hebrew (עברית)"),
    ("de", "German (Deutsch)"),
    ("fr", "French (Français)"),
    ("es", "Spanish (Español)"),
    ("it", "Italian (Italiano)"),
    ("pt", "Portuguese (Português)"),
    ("nl", "Dutch (Nederlands)"),
    ("pl", "Polish (Polski)"),
    ("ru", "Russian (Русский)"),
    ("uk", "Ukrainian (Українська)"),
    ("hi", "Hindi (हिन्दी)"),
    ("bn", "Bengali (বাংলা)"),
    ("zh", "Chinese (中文)"),
    ("ja", "Japanese (日本語)"),
    ("ko", "Korean (한국어)"),
    ("vi", "Vietnamese (Tiếng Việt)"),
    ("th", "Thai (ไทย)"),
    ("id", "Indonesian (Bahasa Indonesia)"),
    ("sv", "Swedish (Svenska)"),
    ("da", "Danish (Dansk)"),
    ("fi", "Finnish (Suomi)"),
    ("no", "Norwegian (Norsk)"),
    ("cs", "Czech (Čeština)"),
    ("el", "Greek (Ελληνικά)"),
    ("ro", "Romanian (Română)"),
    ("hu", "Hungarian (Magyar)"),
    ("az", "Azerbaijani (Azərbaycan)"),
    ("kk", "Kazakh (Қазақша)"),
    ("ka", "Georgian (ქართული)"),
    ("hy", "Armenian (Հայերեն)"),
    ("sw", "Swahili (Kiswahili)"),
)

COMPUTE_TYPES = ("auto", "int8", "int8_float16", "float16", "float32")


class FasterWhisperSTTEngine(STTEngine):
    """Whisper transcription through CTranslate2."""

    engine_id = "faster-whisper"
    display_name = "Whisper (faster-whisper / CTranslate2)"
    version = "1.2"
    requires = ("faster_whisper", "ctranslate2")

    def __init__(self, registry: ModelRegistry | None = None, hardware: Any = None) -> None:
        super().__init__()
        self.registry = registry
        self.hardware = hardware
        self._model: Any = None
        self._model_path: Path | None = None
        self._device = "cpu"
        self._compute_type = "int8"

    # -- availability -------------------------------------------------------
    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            languages=tuple(code for code, _ in WHISPER_LANGUAGES),
            word_timestamps=True,
            segment_timestamps=True,
            language_detection=True,
            translation=True,
            diarization=False,
            streaming=False,
            gpu=self._gpu_available(),
            offline=True,
            notes=(
                "Whisper does not perform speaker diarization, so speaker labels are "
                "not produced. Word-level timestamps and VAD are supported."
            ),
            extra={"engine_pack": "faster-whisper", "translation": True},
        )

    def _gpu_available(self) -> bool:
        try:
            import ctranslate2  # noqa: PLC0415

            return int(ctranslate2.get_cuda_device_count()) > 0
        except Exception:  # noqa: BLE001
            return False

    def supported_languages(self) -> list[tuple[str, str]]:
        return list(WHISPER_LANGUAGES)

    # -- model management ---------------------------------------------------
    def available_models(self) -> list[InstalledModel]:
        if self.registry is None:
            return []
        return [
            model
            for model in self.registry.by_category("stt")
            if model.status in ("installed", "active")
        ]

    def _resolve_model_path(self, model_id: str) -> tuple[Path, InstalledModel | None]:
        if self.registry is not None:
            model = self.registry.get(model_id)
            if model is not None:
                path = model.resolved_path()
                if path is not None and path.exists():
                    return path, model
        candidate = Path(model_id).expanduser()
        if candidate.exists():
            return candidate, None
        raise ModelNotFound(
            f"The speech-recognition model '{model_id}' is not installed. "
            "Open AI Models ▸ Speech Recognition and download it (models are stored "
            "outside the application folder and work offline)."
        )

    def load_model(self, model_id: str, *, compute_type: str = "auto", device: str = "auto") -> Any:
        """Load (or reuse) a Whisper model, honouring the AI settings."""
        path, model = self._resolve_model_path(model_id)
        if self._model is not None and self._model_path == path:
            self._loaded = True
            return self._model
        if not self.is_available():
            raise EngineUnavailable(self.unavailable_reason())

        from faster_whisper import WhisperModel  # noqa: PLC0415 - engine pack

        device, compute = self._resolve_device(device, compute_type)
        cpu_threads = self._cpu_threads()
        started = time.perf_counter()
        try:
            self._model = WhisperModel(
                str(path),
                device=device,
                compute_type=compute,
                cpu_threads=cpu_threads,
                num_workers=1,
                download_root=None,  # never download: models are managed by Tixi Voice
            )
        except Exception as exc:  # noqa: BLE001
            # A GPU that cannot be initialised (driver/CUDA mismatch) must not be fatal.
            if device != "cpu":
                log.warning(
                    "GPU transcription unavailable, falling back to CPU",
                    extra={"event": "stt_gpu_fallback", "error": str(exc)},
                )
                try:
                    self._model = WhisperModel(
                        str(path), device="cpu", compute_type="int8", cpu_threads=cpu_threads
                    )
                    device, compute = "cpu", "int8"
                except Exception as inner:  # noqa: BLE001
                    raise EngineUnavailable(
                        f"Could not load the Whisper model '{model_id}': {inner}"
                    ) from inner
            else:
                raise EngineUnavailable(
                    f"Could not load the Whisper model '{model_id}': {exc}. "
                    "The download may be incomplete — verify or re-download it from AI Models."
                ) from exc
        self._model_path = path
        self._device = device
        self._compute_type = compute
        self._model_id = model_id
        self._loaded = True
        log.info(
            "whisper model loaded",
            extra={
                "event": "stt_model_loaded",
                "model": model_id,
                "device": device,
                "compute_type": compute,
                "cpu_threads": cpu_threads,
                "seconds": round(time.perf_counter() - started, 2),
            },
        )
        return self._model

    def _resolve_device(self, device: str, compute_type: str) -> tuple[str, str]:
        requested_device = (device or "auto").lower()
        requested_compute = (compute_type or "auto").lower()
        if requested_device == "auto":
            if self.hardware is not None and getattr(self.hardware, "cuda_available", False):
                requested_device = "cuda"
            else:
                requested_device = "cpu"
        if requested_compute == "auto":
            requested_compute = "float16" if requested_device == "cuda" else "int8"
        if requested_device == "cpu" and requested_compute in ("float16", "int8_float16"):
            requested_compute = "int8"
        return requested_device, requested_compute

    def _cpu_threads(self) -> int:
        if self.hardware is not None and getattr(self.hardware, "cpu_threads", 0):
            return max(1, int(self.hardware.cpu_threads))
        return max(1, (os.cpu_count() or 4) - 1)

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._model_path = None
            super().unload()
        log.info("whisper model unloaded", extra={"event": "stt_unloaded"})

    # -- transcription ------------------------------------------------------
    def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> TranscriptionResult:
        model_id = request.model_id or self._default_model_id()
        if not model_id:
            raise ModelNotFound(
                "No speech-recognition model is installed. "
                "Download one from AI Models ▸ Speech Recognition."
            )
        device = "auto"
        settings = getattr(self, "settings", None)
        if settings is not None:
            device = getattr(settings, "device", "auto")

        model = self.load_model(model_id, compute_type=request.compute_type, device=device)

        audio_input, duration = self._prepare_audio(request, cancel=cancel)
        if duration <= 0.05:
            raise EngineUnavailable(
                "The audio is too short to transcribe (less than 50 ms of speech)."
            )

        language = None if (request.language in ("", "auto") or request.diarization) else request.language
        if request.language == "auto":
            language = None

        started = time.perf_counter()
        try:
            segments_iter, info = model.transcribe(
                audio_input,
                language=language,
                task=request.task or "transcribe",
                beam_size=max(1, int(request.beam_size)),
                temperature=float(request.temperature),
                vad_filter=bool(request.vad_filter),
                vad_parameters=(
                    {
                        "min_silence_duration_ms": int(request.vad_min_silence_ms),
                        "speech_pad_ms": 200,
                    }
                    if request.vad_filter
                    else None
                ),
                word_timestamps=bool(request.word_timestamps),
                condition_on_previous_text=bool(request.condition_on_previous_text),
                initial_prompt=request.initial_prompt or None,
            )
        except Exception as exc:  # noqa: BLE001
            raise EngineUnavailable(f"Transcription failed: {exc}") from exc

        segments: list[TranscriptSegment] = []
        words: list[dict[str, Any]] = []
        collected: list[str] = []
        warnings: list[str] = []
        detected_language = getattr(info, "language", "") or ""
        language_probability = float(getattr(info, "language_probability", 0.0) or 0.0)
        index = 0
        for segment in segments_iter:
            if cancel is not None and cancel.is_set():
                warnings.append("Transcription was cancelled; the text below is partial.")
                log.info("transcription cancelled", extra={"event": "stt_cancelled", "segments": index})
                break
            text = (segment.text or "").strip()
            if not text:
                continue
            segment_words: list[dict[str, Any]] = []
            if request.word_timestamps and getattr(segment, "words", None):
                for word in segment.words:
                    entry = {
                        "start": round(float(word.start or 0.0), 3),
                        "end": round(float(word.end or 0.0), 3),
                        "word": word.word,
                        "probability": round(float(getattr(word, "probability", 0.0) or 0.0), 4),
                    }
                    segment_words.append(entry)
                    words.append(entry)
            segments.append(
                TranscriptSegment(
                    index=index,
                    start=float(segment.start or 0.0),
                    end=float(segment.end or 0.0),
                    text=text,
                    confidence=float(getattr(segment, "avg_logprob", 0.0) or 0.0),
                    words=segment_words,
                )
            )
            collected.append(text)
            index += 1
            if progress and duration > 0:
                position = min(0.99, float(segment.end or 0.0) / duration)
                progress(position, f"Transcribing — {_format_time(segment.end or 0.0)} of {_format_time(duration)}")

        processing = time.perf_counter() - started
        if not segments:
            warnings.append(
                "No speech was detected in this audio. If you are sure there is speech, "
                "try disabling the voice-activity filter in Settings ▸ AI."
            )
        full_text = " ".join(collected)
        if request.diarization:
            warnings.append(
                "Speaker diarization is not supported by the Whisper models — no speaker "
                "labels were produced. Install a diarization engine pack to enable it."
            )
        self.touch()
        result = TranscriptionResult(
            text=full_text,
            segments=segments,
            language=detected_language or (request.language or ""),
            language_probability=language_probability,
            duration_s=duration,
            model_id=model_id,
            engine_id=self.engine_id,
            speaker_count=0,
            warnings=warnings,
            processing_s=processing,
            words=words,
        )
        log.info(
            "transcription complete",
            extra={
                "event": "stt_complete",
                "model": model_id,
                "audio_seconds": round(duration, 1),
                "processing_seconds": round(processing, 1),
                "language": result.language,
                "segments": len(segments),
            },
        )
        return result

    def detect_language(self, audio: np.ndarray, sample_rate: int) -> tuple[str, float]:
        """Detect the spoken language without a full transcription."""
        model_id = self._default_model_id()
        if not model_id:
            return "", 0.0
        model = self.load_model(model_id)
        from faster_whisper.audio import decode_audio  # noqa: PLC0415 - engine pack

        buffer = to_mono(to_float32(np.asarray(audio)))
        if sample_rate != 16000:
            from ...audio.dsp import resample  # noqa: PLC0415

            buffer = resample(buffer, sample_rate, 16000)
        mono = np.asarray(buffer, dtype=np.float32)
        try:
            language, probability, _ = model.detect_language(
                mono if mono.size >= 1600 else decode_audio(mono, sampling_rate=16000)
            )
            return str(language), float(probability)
        except Exception as exc:  # noqa: BLE001
            log.warning("language detection failed", extra={"event": "stt_detect_failed", "error": str(exc)})
            return "", 0.0

    def _default_model_id(self) -> str:
        if self._model_id:
            return self._model_id
        models = self.available_models()
        return models[0].id if models else ""

    # -- input preparation --------------------------------------------------
    def _prepare_audio(self, request: TranscriptionRequest, *, cancel: threading.Event | None) -> tuple[Any, float]:
        """Return something Whisper accepts plus the audio duration in seconds."""
        from ...audio.wav_io import AudioReadError, read_audio  # noqa: PLC0415

        source = request.audio
        if source is None:
            raise EngineUnavailable("No audio was provided for transcription.")

        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.exists():
                raise EngineUnavailable(f"The audio file was not found: {path}")
            # Let faster-whisper decode containers itself (it bundles PyAV, which
            # handles MP4/MKV/WEBM and every audio format we support).
            duration = _probe_duration(path)
            if duration <= 0:
                duration = 0.0
            if request.sample_rate and request.sample_rate != 16000:
                audio = read_audio(path, mono=True, sample_rate=16000)
                return audio.samples, audio.duration_s
            return str(path), duration

        array = to_float32(np.asarray(source))
        mono = to_mono(array) if array.ndim > 1 else array
        if request.sample_rate and request.sample_rate != 16000:
            from ...audio.dsp import resample  # noqa: PLC0415

            mono = resample(mono, request.sample_rate, 16000)
        if request.preprocess.get("normalise"):
            from ...audio.dsp import normalise  # noqa: PLC0415

            mono = normalise(mono, target_db=-1.0)
        if request.preprocess.get("denoise"):
            from ...audio.dsp import spectral_denoise  # noqa: PLC0415

            mono = spectral_denoise(mono, 16000)
        return np.asarray(mono, dtype=np.float32), float(mono.shape[0]) / 16000.0

    def describe(self) -> dict[str, Any]:
        payload = super().describe()
        payload.update(
            {
                "loaded_model": self._model_id,
                "device": self._device,
                "compute_type": self._compute_type,
                "installed_models": [model.id for model in self.available_models()],
                "diarization": False,
            }
        )
        return payload


def _format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}:{remainder:02d}"


def _probe_duration(path: Path) -> float:
    try:
        from ...audio.wav_io import audio_duration  # noqa: PLC0415

        return audio_duration(path)
    except Exception:  # noqa: BLE001
        return 0.0


def model_size_bytes(model_id: str) -> int:
    """Approximate download size for a Whisper model id (used in the catalogue)."""
    import re

    match = re.match(r"whisper-(tiny|base|small|medium|large-[a-z0-9.-]+)", model_id)
    name = match.group(1) if match else model_id
    table = {
        "tiny": 75_000_000,
        "base": 145_000_000,
        "small": 487_000_000,
        "medium": 1_530_000_000,
        "large-v2": 3_090_000_000,
        "large-v3": 3_090_000_000,
        "large-v3-turbo": 1_620_000_000,
        "large-v1": 3_090_000_000,
    }
    return table.get(name, 1_000_000_000)


def iter_whisper_languages() -> Iterable[tuple[str, str]]:
    return WHISPER_LANGUAGES
