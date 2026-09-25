"""Piper text-to-speech engine.

`Piper <https://github.com/OHF-voice/piper1-gpl>`_ is a fast, local VITS
text-to-speech engine (GPL-3.0-or-later, installed on demand as an *engine
pack*, never bundled) with a large catalogue of per-language voices.  It ships
the espeak-ng phonemiser, which is what makes Persian, Arabic, Turkish etc.
work without any cloud service.

Verified capabilities of the voices we ship in the catalogue:

* **Persian (fa_IR: amir, gyro)** — real native Persian support.  The `gyro`
  voice was trained on a Persian TTS dataset and handles the Ezafe kasra, which
  is exactly why Tixi Voice offers diacritization before synthesis.
* **English / Arabic / German / French / Turkish / Russian / ...** — via the
  corresponding espeak-ng voices.

Pitch is **not** a Piper parameter, so a pitch change is applied as real DSP
post-processing (see :func:`tixi.audio.dsp.pitch_shift`) and the UI labels it as
such instead of pretending the model supports it.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from ...app.logging_config import get_logger
from ...audio.dsp import apply_gain, concat_with_pauses, pitch_factor_to_semitones, pitch_shift
from ...models.model_registry import InstalledModel, ModelRegistry
from ..base import (
    EngineCapabilities,
    EngineUnavailable,
    ModelNotFound,
    ProgressCallback,
    SynthesisRequest,
    SynthesisResult,
    TTSEngine,
    Voice,
)

log = get_logger("tixi.tts.piper")

#: espeak-ng voice code -> BCP-47 code (only what espeak-ng actually provides).
ESPEAK_LANGUAGE_MAP: dict[str, tuple[str, str]] = {
    "fa": ("fa", "Persian (فارسی)"),
    "fa-latn": ("fa", "Persian (Latin transliteration)"),
    "en": ("en", "English"),
    "en-us": ("en", "English (US)"),
    "en-gb": ("en-GB", "English (UK)"),
    "ar": ("ar", "Arabic (العربية)"),
    "de": ("de", "German (Deutsch)"),
    "fr": ("fr", "French (Français)"),
    "es": ("es", "Spanish (Español)"),
    "es-la": ("es", "Spanish (Latin America)"),
    "it": ("it", "Italian (Italiano)"),
    "pt": ("pt", "Portuguese (Português)"),
    "pt-br": ("pt-BR", "Portuguese (Brazil)"),
    "nl": ("nl", "Dutch (Nederlands)"),
    "pl": ("pl", "Polish (Polski)"),
    "ru": ("ru", "Russian (Русский)"),
    "tr": ("tr", "Turkish (Türkçe)"),
    "uk": ("uk", "Ukrainian (Українська)"),
    "hi": ("hi", "Hindi (हिन्दी)"),
    "ur": ("ur", "Urdu (اردو)"),
    "zh": ("zh", "Chinese (中文)"),
    "yue": ("yue", "Cantonese"),
    "ja": ("ja", "Japanese (日本語)"),
    "ko": ("ko", "Korean (한국어)"),
    "vi": ("vi", "Vietnamese (Tiếng Việt)"),
    "sv": ("sv", "Swedish (Svenska)"),
    "da": ("da", "Danish (Dansk)"),
    "nb": ("nb", "Norwegian (Norsk)"),
    "fi": ("fi", "Finnish (Suomi)"),
    "cs": ("cs", "Czech (Čeština)"),
    "sk": ("sk", "Slovak (Slovenčina)"),
    "hu": ("hu", "Hungarian (Magyar)"),
    "ro": ("ro", "Romanian (Română)"),
    "bg": ("bg", "Bulgarian (Български)"),
    "el": ("el", "Greek (Ελληνικά)"),
    "he": ("he", "Hebrew (עברית)"),
    "ka": ("ka", "Georgian (ქართული)"),
    "kk": ("kk", "Kazakh (Қазақша)"),
    "sw": ("sw", "Swahili (Kiswahili)"),
    "id": ("id", "Indonesian (Bahasa Indonesia)"),
    "ne": ("ne", "Nepali (नेपाली)"),
    "bn": ("bn", "Bengali (বাংলা)"),
    "ta": ("ta", "Tamil (தமிழ்)"),
    "sr": ("sr", "Serbian (Српски)"),
    "is": ("is", "Icelandic (Íslenska)"),
    "lv": ("lv", "Latvian (Latviešu)"),
    "lt": ("lt", "Lithuanian (Lietuvių)"),
}

#: Languages where Piper's espeak front-end is known (from the training data) to
#: produce genuinely native pronunciation rather than an approximation.
NATIVE_LANGUAGES = frozenset(ESPEAK_LANGUAGE_MAP)


@dataclass
class VoiceProfile:
    """Voice metadata read from the ``.onnx.json`` config next to a model."""

    voice_id: str
    name: str
    model_path: Path
    config_path: Path
    language: str = ""
    language_name: str = ""
    espeak_voice: str = ""
    sample_rate: int = 22050
    num_speakers: int = 1
    quality: str = ""
    size_bytes: int = 0
    speaker_names: dict[str, int] = None  # type: ignore[assignment]
    persian_support: str = ""

    def __post_init__(self) -> None:
        if self.speaker_names is None:
            self.speaker_names = {}

    def to_voice(self, engine_id: str = "piper") -> Voice:
        return Voice(
            voice_id=self.voice_id,
            name=self.name,
            language=self.language,
            gender="",
            quality=self.quality,
            sample_rate=self.sample_rate,
            model_id=self.voice_id,
            speakers=max(1, self.num_speakers),
            description=f"{self.language_name or self.language} · espeak voice '{self.espeak_voice}'",
            path=str(self.model_path),
            engine_id=engine_id,
            persian_support=self.persian_support,
        )


class PiperTTSEngine(TTSEngine):
    """Piper synthesis with sentence-level streaming and real cancellation."""

    engine_id = "piper"
    display_name = "Piper (local VITS)"
    version = "1.8"
    requires = ("piper", "onnxruntime")

    def __init__(self, registry: ModelRegistry | None = None, *, use_cuda: bool | None = None) -> None:
        super().__init__()
        self.registry = registry
        self._voice_cache: dict[str, Any] = {}
        self._profiles: dict[str, VoiceProfile] = {}
        self._use_cuda = use_cuda
        self._espeak_data_dir: Path | None = None

    # -- availability -------------------------------------------------------
    @classmethod
    def is_available(cls) -> bool:
        return super().is_available()

    @property
    def capabilities(self) -> EngineCapabilities:
        voices = list(self._profiles.values())
        languages = tuple(sorted({voice.language for voice in voices if voice.language}))
        return EngineCapabilities(
            languages=languages,
            multi_speaker=any(voice.num_speakers > 1 for voice in voices),
            pitch_control=False,
            speed_control=True,
            volume_control=True,
            pause_control=True,
            word_timestamps=False,
            segment_timestamps=True,
            language_detection=False,
            offline=True,
            gpu=self._cuda_available(),
            notes=(
                "Pitch is applied as DSP post-processing (Piper has no pitch parameter). "
                "Pauses are inserted between sentences by Tixi Voice."
            ),
            extra={"pitch_via_postprocessing": True, "engine_pack": "piper"},
        )

    def _cuda_available(self) -> bool:
        if self._use_cuda is not None:
            return self._use_cuda
        try:
            import onnxruntime  # noqa: PLC0415

            return "CUDAExecutionProvider" in onnxruntime.get_available_providers()
        except Exception:  # noqa: BLE001
            return False

    # -- voice discovery ----------------------------------------------------
    def _scan_profiles(self, force: bool = False) -> dict[str, VoiceProfile]:
        if self._profiles and not force:
            return self._profiles
        profiles: dict[str, VoiceProfile] = {}
        for model in self._voice_models():
            profile = self._profile_for(model)
            if profile is not None:
                profiles[profile.voice_id] = profile
        self._profiles = profiles
        return profiles

    def _voice_models(self) -> list[InstalledModel]:
        if self.registry is None:
            return []
        models = list(self.registry.by_category("tts_voice"))
        models.extend(self.registry.by_category("tts_multilingual"))
        return [model for model in models if model.status in ("installed", "active")]

    def _profile_for(self, model: InstalledModel) -> VoiceProfile | None:
        path = model.resolved_path()
        if path is None:
            return None
        if path.is_dir():
            candidates = sorted(path.glob("*.onnx"))
            if not candidates:
                return None
            model_file = candidates[0]
        else:
            model_file = path
        config_file = model_file.with_suffix(model_file.suffix + ".json")
        if not config_file.exists():
            config_file = model_file.with_name(model_file.name + ".json")
        payload: dict[str, Any] = {}
        if config_file.exists():
            try:
                payload = json.loads(config_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
        espeak_voice = str(payload.get("espeak", {}).get("voice", "")).lower()
        language, language_name = ESPEAK_LANGUAGE_MAP.get(
            espeak_voice.split("+")[0], (str(model.language or ""), "")
        )
        sample_rate = int(payload.get("audio", {}).get("sample_rate", 22050) or 22050)
        num_speakers = int(payload.get("num_speakers", 1) or 1)
        speaker_map = payload.get("speaker_id_map") or {}
        quality = model.metadata.get("quality") or _quality_from_name(model_file.name)
        persian_support = ""
        if language == "fa":
            persian_support = "native"
        elif not espeak_voice:
            persian_support = "unknown"
        else:
            persian_support = "unsupported"
        return VoiceProfile(
            voice_id=model.id,
            name=model.name or model_file.stem,
            model_path=model_file,
            config_path=config_file,
            language=language or str(model.language or ""),
            language_name=language_name,
            espeak_voice=espeak_voice,
            sample_rate=sample_rate,
            num_speakers=max(1, num_speakers),
            quality=str(quality or ""),
            size_bytes=model.size_bytes or (model_file.stat().st_size if model_file.exists() else 0),
            speaker_names=dict(speaker_map),
            persian_support=persian_support,
        )

    def voices(self) -> list[Voice]:
        return [
            profile.to_voice(self.engine_id)
            for profile in sorted(self._scan_profiles().values(), key=lambda item: item.name.lower())
        ]

    def supported_languages(self) -> list[tuple[str, str]]:
        return sorted(
            {(profile.language, profile.language_name or profile.language) for profile in self._scan_profiles().values()},
            key=lambda item: item[1],
        )

    def profile_for(self, voice_id: str) -> VoiceProfile | None:
        profiles = self._scan_profiles()
        if not voice_id:
            return next(iter(profiles.values()), None)
        return profiles.get(voice_id)

    # -- model loading ------------------------------------------------------
    def load_voice(self, voice: Voice) -> Any:
        return self._voice(voice.voice_id)

    def _voice(self, voice_id: str) -> Any:
        profile = self.profile_for(voice_id)
        if profile is None:
            raise ModelNotFound(
                f"The voice '{voice_id}' is not installed. "
                "Open AI Models ▸ Persian / Multilingual Text-to-Speech to download it."
            )
        cached = self._voice_cache.get(profile.voice_id)
        if cached is not None:
            self._loaded = True
            return cached
        if not self.is_available():
            raise EngineUnavailable(self.unavailable_reason())
        try:
            from piper import PiperVoice  # noqa: PLC0415 - optional engine pack
        except ImportError as exc:  # pragma: no cover - engine pack missing
            raise EngineUnavailable(self.unavailable_reason()) from exc

        started = time.perf_counter()
        try:
            voice = PiperVoice.load(
                profile.model_path,
                config_path=profile.config_path if profile.config_path.exists() else None,
                use_cuda=bool(self._cuda_available()),
            )
        except Exception as exc:  # noqa: BLE001
            raise EngineUnavailable(
                f"Could not load the Piper voice '{profile.name}': {exc}. "
                "The model file may be corrupted — re-download it from the AI Models page."
            ) from exc
        self._voice_cache[profile.voice_id] = voice
        self._loaded = True
        self._model_id = profile.voice_id
        log.info(
            "piper voice loaded",
            extra={
                "event": "piper_voice_loaded",
                "voice": profile.voice_id,
                "language": profile.language,
                "seconds": round(time.perf_counter() - started, 2),
            },
        )
        return voice

    def unload(self) -> None:
        with self._lock:
            self._voice_cache.clear()
            self._profiles = {}
            super().unload()
        log.info("piper voices unloaded", extra={"event": "piper_unloaded"})

    def unload_voice(self, voice_id: str) -> bool:
        removed = self._voice_cache.pop(voice_id, None) is not None
        if not self._voice_cache:
            self._loaded = False
        return removed

    # -- synthesis ----------------------------------------------------------
    def synthesize(
        self,
        request: SynthesisRequest,
        *,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> SynthesisResult:
        profile = self.profile_for(request.voice_id)
        if profile is None:
            raise ModelNotFound(
                "No Piper voice is installed. Download one from AI Models ▸ Text-to-Speech."
            )
        from piper import SynthesisConfig  # noqa: PLC0415 - engine pack

        voice = self._voice(profile.voice_id)
        config = SynthesisConfig(
            length_scale=_length_scale(request.speed),
            volume=max(0.0, min(4.0, request.volume)),
            normalize_audio=True,
            speaker_id=_speaker_id(request, profile),
        )

        chunks: list[np.ndarray] = []
        pauses: list[int] = []
        warnings: list[str] = []
        started = time.perf_counter()
        sentences = _sentence_count(request.text)
        produced = 0
        for index, audio_chunk in enumerate(voice.synthesize(request.text, syn_config=config)):
            if cancel is not None and cancel.is_set():
                log.info("piper synthesis cancelled", extra={"event": "piper_cancelled", "sentences": produced})
                break
            samples = np.asarray(audio_chunk.audio_float_array, dtype=np.float32)
            if samples.size == 0:
                continue
            chunks.append(samples)
            pauses.append(int(max(0, request.extra.get("sentence_pause_ms", 120))))
            produced += 1
            if progress:
                fraction = min(0.99, (index + 1) / max(1, sentences))
                progress(fraction, f"Synthesising sentence {index + 1} of {max(1, sentences)}")

        if not chunks:
            raise EngineUnavailable(
                "The synthesizer produced no audio. The text may contain only characters the "
                "selected voice cannot pronounce."
            )

        if cancel is not None and cancel.is_set() and produced == 0:
            raise EngineUnavailable("Synthesis was cancelled before any audio was produced.")

        sample_rate = int(getattr(voice.config, "sample_rate", profile.sample_rate) or profile.sample_rate)
        audio = concat_with_pauses(chunks, sample_rate, pauses[:-1] + [0]) if len(chunks) > 1 else chunks[0]

        # Pitch and any residual volume shaping are real DSP, applied here.
        if abs(request.pitch - 1.0) > 1e-3:
            audio = pitch_shift(audio, sample_rate, pitch_factor_to_semitones(request.pitch))
        if request.extra.get("target_peak"):
            audio = apply_gain(audio, float(request.extra["target_peak"]))
        if request.sample_rate and int(request.sample_rate) != sample_rate:
            from ...audio.dsp import resample  # noqa: PLC0415

            audio = resample(audio, sample_rate, int(request.sample_rate))
            sample_rate = int(request.sample_rate)

        self.touch()
        processing = time.perf_counter() - started
        duration = audio.shape[0] / sample_rate if sample_rate else 0.0
        return SynthesisResult(
            samples=audio,
            sample_rate=sample_rate,
            voice_id=profile.voice_id,
            engine_id=self.engine_id,
            text=request.text,
            duration_s=duration,
            warnings=warnings,
            native_format="wav",
            metadata={
                "voice_name": profile.name,
                "language": profile.language,
                "espeak_voice": profile.espeak_voice,
                "sentences": produced,
                "processing_s": round(processing, 3),
                "real_time_factor": round(duration / processing, 2) if processing > 0 else 0.0,
                "pitch_applied_dsp": abs(request.pitch - 1.0) > 1e-3,
                "persian_support": profile.persian_support,
            },
        )

    def synthesize_preview(self, text: str, voice_id: str, *, speed: float = 1.0) -> SynthesisResult:
        """Short preview used by the voice picker (max ~220 characters)."""
        snippet = " ".join((text or "").split())[:220] or "Tixi Voice"
        return self.synthesize(
            SynthesisRequest(text=snippet, voice_id=voice_id, speed=speed)
        )

    def preflight(self, request: SynthesisRequest) -> list[str]:
        issues = super().preflight(request)
        profile = self.profile_for(request.voice_id)
        if profile is not None and profile.persian_support == "unsupported":
            from ..text_normalization.numbers import is_persian_text  # noqa: PLC0415

            if is_persian_text(request.text):
                issues.append(
                    f"The voice '{profile.name}' is not a Persian voice (espeak voice "
                    f"'{profile.espeak_voice}'), so Persian text will be mispronounced. "
                    "Install a fa_IR voice such as 'Persian — Amir' or 'Persian — Gyro'."
                )
        return issues

    def describe(self) -> dict[str, Any]:
        payload = super().describe()
        payload.update(
            {
                "voices": len(self._profiles),
                "loaded_voices": list(self._voice_cache),
                "cuda": self._cuda_available(),
            }
        )
        return payload


def _length_scale(speed: float) -> float:
    """Piper's ``length_scale`` is the inverse of speaking rate."""
    speed = max(0.2, min(3.0, float(speed or 1.0)))
    return 1.0 / speed


def _speaker_id(request: SynthesisRequest, profile: VoiceProfile) -> int | None:
    if request.speaker_id is not None:
        return int(request.speaker_id)
    name = request.extra.get("speaker")
    if name and profile.speaker_names:
        if name in profile.speaker_names:
            return int(profile.speaker_names[name])
        for key, value in profile.speaker_names.items():
            if key.lower() == str(name).lower():
                return int(value)
    return None


def _quality_from_name(name: str) -> str:
    lowered = name.lower()
    for quality in ("x_low", "low", "medium", "high"):
        if quality in lowered:
            return quality
    return ""


def _sentence_count(text: str) -> int:
    from ..text_normalization.sentences import segment_sentences  # noqa: PLC0415

    sentences = segment_sentences(text or "")
    return max(1, len(sentences))


def piper_voice_urls(language: str, voice: str, quality: str = "medium") -> tuple[str, str]:
    """Canonical download URLs for a piper-voices checkpoint (v1.0.0 tag)."""
    base = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
    path = f"{language.split('-')[0]}/{language.replace('-', '_')}/{voice}/{quality}"
    return f"{base}/{path}/{language}-{voice}-{quality}.onnx", f"{base}/{path}/{language}-{voice}-{quality}.onnx.json"


def known_voices() -> Iterable[dict[str, Any]]:
    """Small, verified subset of the piper-voices catalogue (see MODELS.md)."""
    return (
        {"id": "piper-fa-amir", "language": "fa_IR", "voice": "amir", "quality": "medium", "name": "Persian — Amir (male)"},
        {"id": "piper-fa-gyro", "language": "fa_IR", "voice": "gyro", "quality": "medium", "name": "Persian — Gyro (male)"},
    )
