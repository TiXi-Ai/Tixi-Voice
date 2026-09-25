"""Speech-to-text service.

Takes a path (or an in-memory buffer) and turns it into a transcript with
timestamps, optionally post-processing the result through the Persian text
pipeline.  Long files are transcribed in windows so progress can be reported
and the job can be cancelled — the engine itself handles the heavy lifting.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from ..app.logging_config import get_logger
from ..audio.dsp import resample, to_mono
from ..audio.formats import available_formats, normalise_format
from ..audio.wav_io import SUPPORTED_INPUT_SUFFIXES, read_audio
from ..engines.base import (
    CancelledError,
    EngineError,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptSegment,
)
from ..engines.text_normalization.pipeline import PipelineOptions, PipelineResult, TextPipeline
from ..utils.atomic import unique_path

log = get_logger("tixi.services.stt")

TARGET_RATE = 16000


@dataclass
class TranscriptionOutcome:
    """Result of a transcription run, ready for the History page."""

    result: TranscriptionResult | None = None
    pipeline: PipelineResult | None = None
    raw_text: str = ""
    text: str = ""
    language: str = ""
    duration_s: float = 0.0
    seconds_taken: float = 0.0
    source_path: Path | None = None
    warnings: list[str] = field(default_factory=list)
    exports: dict[str, Path] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.result is not None

    @property
    def segments(self) -> list[TranscriptSegment]:
        return list(self.result.segments) if self.result is not None else []

    @property
    def speed_ratio(self) -> float:
        if self.seconds_taken <= 0 or self.duration_s <= 0:
            return 0.0
        return self.duration_s / self.seconds_taken

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "duration_s": round(self.duration_s, 3),
            "seconds_taken": round(self.seconds_taken, 2),
            "segments": len(self.segments),
            "source": str(self.source_path or ""),
            "warnings": list(self.warnings),
        }


class TranscriptionService:
    """Wraps a speech-recognition engine with file loading, progress and cleanup."""

    def __init__(
        self,
        engine_provider: Callable[[], Any],
        pipeline: TextPipeline | None = None,
    ) -> None:
        self.engine_provider = engine_provider
        self.pipeline = pipeline or TextPipeline()

    # -- public API ---------------------------------------------------------
    def transcribe_file(
        self,
        path: str | Path,
        *,
        language: str = "",
        task: str = "transcribe",
        beam_size: int = 5,
        temperature: float = 0.0,
        vad_filter: bool = True,
        word_timestamps: bool = True,
        initial_prompt: str = "",
        compute_type: str = "",
        post_process: bool = True,
        normalize: bool = True,
        diacritize: bool = False,
        diacritization_mode: str = "smart",
        progress: Callable[[float, str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> TranscriptionOutcome:
        source = Path(path)
        if not source.exists():
            raise EngineError(f"The audio file does not exist: {source}")
        if not is_supported_audio(source):
            raise EngineError(
                f"Unsupported audio format ({source.suffix or source.name}). "
                f"Supported: {', '.join(sorted(available_formats()))}."
            )
        started = time.perf_counter()
        report = _make_reporter(progress)
        report(0.02, "Loading the audio…")
        data = read_audio(source, mono=True, sample_rate=TARGET_RATE)
        outcome = self.transcribe_samples(
            np.asarray(data.samples, dtype=np.float32),
            data.sample_rate,
            language=language,
            task=task,
            beam_size=beam_size,
            temperature=temperature,
            vad_filter=vad_filter,
            word_timestamps=word_timestamps,
            initial_prompt=initial_prompt,
            compute_type=compute_type,
            post_process=post_process,
            normalize=normalize,
            diacritize=diacritize,
            diacritization_mode=diacritization_mode,
            progress=lambda fraction, detail: report(0.05 + fraction * 0.9, detail),
            cancel=cancel,
        )
        outcome.source_path = source
        outcome.seconds_taken = time.perf_counter() - started
        report(1.0, "Finished")
        return outcome

    def transcribe_samples(
        self,
        samples: np.ndarray,
        sample_rate: int,
        *,
        language: str = "",
        task: str = "transcribe",
        beam_size: int = 5,
        temperature: float = 0.0,
        vad_filter: bool = True,
        word_timestamps: bool = True,
        initial_prompt: str = "",
        compute_type: str = "",
        post_process: bool = True,
        normalize: bool = True,
        diacritize: bool = False,
        diacritization_mode: str = "smart",
        progress: Callable[[float, str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> TranscriptionOutcome:
        """Transcribe an in-memory buffer (dictation, preview, re-run)."""
        started = time.perf_counter()
        outcome = TranscriptionOutcome()
        audio = to_mono(np.asarray(samples, dtype=np.float32))
        if sample_rate != TARGET_RATE:
            audio = resample(audio, sample_rate, TARGET_RATE, quality="fast")
        if audio.size == 0:
            outcome.warnings.append("There is no audio to transcribe.")
            return outcome
        outcome.duration_s = audio.shape[0] / float(TARGET_RATE)

        engine = self.engine_provider()
        if engine is None:
            outcome.warnings.append(
                "ERROR: The speech-recognition engine is not installed. "
                "Open AI Models ▸ Speech to Text to install it."
            )
            return outcome

        request = TranscriptionRequest(
            audio=audio,
            sample_rate=TARGET_RATE,
            language=language or "auto",
            task=task,  # type: ignore[arg-type]
            beam_size=int(beam_size),
            temperature=float(temperature),
            vad_filter=bool(vad_filter),
            word_timestamps=bool(word_timestamps),
            initial_prompt=initial_prompt,
            compute_type=compute_type,
        )
        _make_reporter(progress)(0.1, "Recognising speech…")
        result = engine.transcribe(request, progress=progress, cancel=cancel)
        outcome.result = result
        outcome.raw_text = result.text or ""
        outcome.language = result.language or (language if language != "auto" else "")
        outcome.warnings.extend(result.warnings)

        text = outcome.raw_text
        if post_process and text.strip():
            if diacritize:
                diacritizer = getattr(engine, "diacritizer", None) or self.pipeline.diacritizer
                self.pipeline.set_diacritizer(diacritizer)
            outcome.pipeline = self.pipeline.process(
                text,
                PipelineOptions(
                    normalize=normalize,
                    diacritize=diacritize,
                    diacritization_mode=diacritization_mode,  # type: ignore[arg-type]
                    language=language or "fa",
                    chunk=False,
                ),
            )
            for stage, detail in outcome.pipeline.stage_summary():
                if "skipped" in detail.lower():
                    continue
                outcome.warnings.append(f"{stage}: {detail}")
            if not diacritize or outcome.pipeline.diacritized_text:
                text = outcome.pipeline.final_text or text
        outcome.text = text
        outcome.seconds_taken = time.perf_counter() - started
        _make_reporter(progress)(1.0, "Finished")
        log.info(
            "transcription finished",
            extra={
                "event": "stt_finished",
                "seconds": round(outcome.seconds_taken, 2),
                "audio_seconds": round(outcome.duration_s, 2),
                "segments": len(outcome.segments),
                "language": outcome.language,
            },
        )
        return outcome

    def detect_language(self, samples: np.ndarray, sample_rate: int) -> tuple[str, float]:
        engine = self.engine_provider()
        if engine is None:
            return "", 0.0
        audio = to_mono(np.asarray(samples, dtype=np.float32))
        if sample_rate != TARGET_RATE:
            audio = resample(audio, sample_rate, TARGET_RATE, quality="fast")
        if audio.size == 0:
            return "", 0.0
        try:
            return engine.detect_language(audio, TARGET_RATE)
        except Exception as exc:  # noqa: BLE001 - detection is best effort
            log.warning("language detection failed", extra={"event": "detect_failed", "error": str(exc)})
            return "", 0.0

    def supported_languages(self) -> list[tuple[str, str]]:
        engine = self.engine_provider()
        if engine is None:
            return []
        try:
            return engine.supported_languages()
        except Exception:  # noqa: BLE001
            return []

    # -- exports ------------------------------------------------------------
    def export(
        self,
        outcome: TranscriptionOutcome,
        destination: Path,
        *,
        fmt: str = "txt",
        include_timestamps: bool = True,
        include_speakers: bool = True,
        title: str = "",
    ) -> Path:
        """Write the transcript next to nothing else — never overwriting."""
        destination = Path(destination)
        target = unique_path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = self.render(outcome, fmt=fmt, include_timestamps=include_timestamps, include_speakers=include_speakers)
        target.write_text(text, encoding="utf-8")
        outcome.exports[fmt] = target
        return target

    def render(
        self,
        outcome: TranscriptionOutcome,
        *,
        fmt: str = "txt",
        include_timestamps: bool = True,
        include_speakers: bool = True,
    ) -> str:
        """Serialise a transcript to txt / srt / vtt / json / md."""
        fmt = (fmt or "txt").lower().lstrip(".")
        segments = outcome.segments
        if fmt in ("json",):
            import json

            payload: dict[str, Any] = {
                "text": outcome.text,
                "language": outcome.language,
                "duration_s": round(outcome.duration_s, 3),
                "model_id": outcome.result.model_id if outcome.result else "",
                "engine_id": outcome.result.engine_id if outcome.result else "",
                "segments": [segment.as_dict() for segment in segments],
                "warnings": outcome.warnings,
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)
        if fmt in ("srt", "vtt"):
            return _render_captions(segments, text=outcome.text, vtt=fmt == "vtt", speakers=include_speakers)
        if fmt in ("md", "markdown"):
            header = title_line(outcome)
            body = _render_paragraphs(segments, include_timestamps, include_speakers, bracket=" *")
            return f"# {header}\n\n{body}\n"
        body = _render_paragraphs(segments, include_timestamps, include_speakers, bracket="")
        if not segments:
            return outcome.text
        return body

    # -- helpers ------------------------------------------------------------
    def to_history_entry(self, outcome: TranscriptionOutcome, title: str = "") -> dict[str, Any]:
        """Keyword arguments for :meth:`HistoryRepository.add`."""
        return {
            "kind": "stt",
            "title": title or _title_from_path(outcome.source_path),
            "text": outcome.text,
            "language": outcome.language,
            "model_id": outcome.result.model_id if outcome.result else "",
            "audio_path": outcome.source_path,
            "duration_ms": int(outcome.duration_s * 1000),
            "meta": {
                "segments": len(outcome.segments),
                "seconds_taken": round(outcome.seconds_taken, 2),
                "warnings": outcome.warnings,
            },
        }


# ---------------------------------------------------------------------------
# module helpers
# ---------------------------------------------------------------------------
def is_supported_audio(path: str | Path) -> bool:
    """``True`` when the installed audio backends can open this file."""
    source = Path(path)
    if source.suffix.lower() in SUPPORTED_INPUT_SUFFIXES:
        return True
    if not source.suffix:
        return False
    return normalise_format(source.suffix.lstrip(".")) in available_formats()


def _make_reporter(progress: Callable[[float, str], None] | None) -> Callable[[float, str], None]:
    if progress is None:
        return lambda fraction, detail="": None
    return lambda fraction, detail="": progress(max(0.0, min(1.0, fraction)), detail)


def _title_from_path(path: Path | None) -> str:
    if path is None:
        return "Microphone recording"
    return path.stem.replace("_", " ").strip() or path.name


def title_line(outcome: TranscriptionOutcome) -> str:
    if outcome.source_path is not None:
        return outcome.source_path.stem
    return "Transcript"


def _render_paragraphs(
    segments: Sequence[TranscriptSegment],
    include_timestamps: bool,
    include_speakers: bool,
    *,
    bracket: str = "",
) -> str:
    lines: list[str] = []
    for segment in segments:
        prefix = ""
        if include_timestamps:
            prefix = f"[{format_timestamp(segment.start)}{bracket}] "
        if include_speakers and segment.speaker:
            prefix += f"{segment.speaker}: "
        lines.append(f"{prefix}{segment.text.strip()}")
    return "\n\n".join(lines) if lines else ""


def _render_captions(
    segments: Sequence[TranscriptSegment],
    *,
    text: str = "",
    vtt: bool = False,
    speakers: bool = True,
) -> str:
    blocks: list[str] = ["WEBVTT\n"] if vtt else []
    used = list(segments)
    if not used and text.strip():
        used = [TranscriptSegment(index=0, start=0.0, end=0.0, text=text)]
    for index, segment in enumerate(used, start=1):
        start = format_timestamp(segment.start, vtt=vtt, always_hours=True)
        end = format_timestamp(segment.end or segment.start + 2.0, vtt=vtt, always_hours=True)
        body = segment.text.strip()
        if speakers and segment.speaker:
            body = f"{segment.speaker}: {body}"
        if vtt:
            blocks.append(f"{start} --> {end}\n{body}\n")
        else:
            blocks.append(f"{index}\n{start} --> {end}\n{body}\n")
    return "\n".join(blocks).strip() + "\n"


def format_timestamp(seconds: float, *, vtt: bool = False, always_hours: bool = False) -> str:
    """``00:01:02,500`` (SRT) or ``00:01:02.500`` / ``01:02.500`` (VTT)."""
    seconds = max(0.0, float(seconds))
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        millis = 999
    separator = "." if vtt else ","
    if hours or always_hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"
    return f"{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def estimate_transcription_time(duration_s: float, *, speed_ratio: float = 12.0) -> float:
    """Rough ETA: a small Whisper model on CPU runs ~10–15× realtime."""
    return max(1.0, float(duration_s) / max(1.0, speed_ratio))
