"""Text-to-speech service.

Wraps the engine layer with everything the UI needs but the engine must not
know about: text pipeline (normalisation + optional diacritization), sentence
chunking with pauses, writing the result to a collision-safe file, registering
it in the library/history and reporting progress.

No engine is loaded here — the service asks the :class:`EngineManager` for one
when a job actually starts, so an "engine not installed" state is reported as a
normal, explained error instead of a crash.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from ..app.logging_config import get_logger
from ..audio.dsp import concat_with_pauses, normalise as normalise_audio, peak_level
from ..audio.wav_io import write_wav
from ..engines.base import CancelledError, EngineError, SynthesisRequest, SynthesisResult
from ..engines.text_normalization.pipeline import PipelineOptions, PipelineResult, TextPipeline
from ..utils.atomic import unique_path

log = get_logger("tixi.services.tts")


@dataclass
class SynthesisOutcome:
    """What a synthesis run produced."""

    result: SynthesisResult | None = None
    pipeline: PipelineResult | None = None
    text_used: str = ""
    raw_text: str = ""
    output_path: Path | None = None
    duration_s: float = 0.0
    seconds_taken: float = 0.0
    warnings: list[str] = field(default_factory=list)
    chunks: int = 0
    cues: list[Any] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result is not None and not self.warnings_are_fatal()

    def warnings_are_fatal(self) -> bool:
        return any(w.startswith("ERROR:") for w in self.warnings)

    @property
    def realtime_factor(self) -> float:
        if self.seconds_taken <= 0 or self.duration_s <= 0:
            return 0.0
        return self.duration_s / self.seconds_taken

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text_used,
            "characters": len(self.text_used),
            "chunks": self.chunks,
            "duration_s": round(self.duration_s, 3),
            "seconds_taken": round(self.seconds_taken, 2),
            "output": str(self.output_path) if self.output_path else "",
            "warnings": list(self.warnings),
        }


class SynthesisService:
    """Runs Persian-aware synthesis, chunk by chunk, off the GUI thread."""

    def __init__(
        self,
        engine_provider: Callable[[], Any],
        pipeline: TextPipeline | None = None,
        *,
        sample_rate: int = 22050,
    ) -> None:
        self.engine_provider = engine_provider
        self.pipeline = pipeline or TextPipeline()
        self.sample_rate = int(sample_rate)

    # -- text preparation ---------------------------------------------------
    def prepare_text(
        self,
        text: str,
        *,
        normalize: bool = True,
        diacritize: bool = False,
        diacritization_mode: str = "smart",
        apply_dictionary: bool = True,
        language: str = "fa",
        chunk: bool = True,
        chunk_chars: int = 900,
        sentence_pause_ms: int = 180,
        paragraph_pause_ms: int = 400,
        diacritizer: Any = None,
    ) -> tuple[PipelineResult, str, list[Any]]:
        """Normalise/diacritize the text and split it into synthesis chunks."""
        if diacritizer is not None:
            self.pipeline.set_diacritizer(diacritizer)
        result = self.pipeline.process(
            text,
            PipelineOptions(
                normalize=normalize,
                diacritize=diacritize,
                diacritization_mode=diacritization_mode,  # type: ignore[arg-type]
                apply_dictionary=apply_dictionary,
                language=language,
                chunk=chunk,
                chunk_max_chars=chunk_chars,
                sentence_pause_ms=sentence_pause_ms,
                paragraph_pause_ms=paragraph_pause_ms,
            ),
        )
        ready = result.final_text or text
        chunks = list(result.chunks or [])
        if not chunks:
            from ..engines.text_normalization.sentences import (  # noqa: PLC0415
                SegmentationOptions,
                build_chunks,
            )

            chunks = build_chunks(
                ready,
                SegmentationOptions(
                    max_chunk_chars=chunk_chars,
                    sentence_pause_ms=sentence_pause_ms,
                    paragraph_pause_ms=paragraph_pause_ms,
                ),
            )
        return result, ready, chunks

    # -- synthesis ----------------------------------------------------------
    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        language: str = "fa",
        speed: float = 1.0,
        pitch: float = 1.0,
        volume: float = 1.0,
        normalize: bool = True,
        diacritize: bool = False,
        diacritization_mode: str = "smart",
        apply_dictionary: bool = True,
        chunk_chars: int = 900,
        sentence_pause_ms: int = 180,
        paragraph_pause_ms: int = 400,
        sample_rate: int | None = None,
        output_format: str = "wav",
        bit_depth: int = 16,
        write_to: Path | None = None,
        diacritizer: Any = None,
        progress: Callable[[float, str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> SynthesisOutcome:
        started = time.perf_counter()
        outcome = SynthesisOutcome(raw_text=text)
        if not text or not text.strip():
            outcome.warnings.append("ERROR: There is no text to synthesise.")
            return outcome

        def report(fraction: float, detail: str) -> None:
            if progress is not None:
                progress(max(0.0, min(0.99, fraction)), detail)

        report(0.01, "Preparing the text…")
        pipeline_result, ready, chunks = self.prepare_text(
            text,
            normalize=normalize,
            diacritize=diacritize,
            diacritization_mode=diacritization_mode,
            apply_dictionary=apply_dictionary,
            language=language,
            chunk_chars=chunk_chars,
            sentence_pause_ms=sentence_pause_ms,
            paragraph_pause_ms=paragraph_pause_ms,
            diacritizer=diacritizer,
        )
        outcome.pipeline = pipeline_result
        outcome.text_used = ready
        outcome.chunks = len(chunks)
        outcome.cues = list(chunks)
        outcome.warnings.extend(pipeline_result.warnings)
        if not ready.strip():
            outcome.warnings.append("ERROR: The text became empty after normalisation.")
            return outcome

        engine = self.engine_provider()
        if engine is None:
            outcome.warnings.append(
                "ERROR: Piper TTS is not installed. Open AI Models ▸ Text to Speech to install the "
                "engine pack and a Persian voice."
            )
            return outcome

        target_rate = int(sample_rate or self.sample_rate)
        rendered: list[SynthesisResult] = []
        total = max(1, len(chunks))
        for index, chunk in enumerate(chunks):
            if cancel is not None and cancel.is_set():
                raise CancelledError("cancelled")
            chunk_text = getattr(chunk, "text", str(chunk))
            request = SynthesisRequest(
                text=chunk_text,
                voice_id=voice_id,
                language=language,
                speed=speed,
                pitch=pitch,
                volume=volume,
                sample_rate=target_rate,
            )
            report(index / total, f"Synthesising part {index + 1} of {total}…")
            piece = engine.synthesize(request, cancel=cancel)
            rendered.append(piece)

        if not rendered:
            outcome.warnings.append("ERROR: The engine produced no audio.")
            return outcome

        samples, sample_rate_used, warnings = self._merge(rendered, chunks, target_rate)
        outcome.warnings.extend(warnings)
        if samples.size == 0:
            outcome.warnings.append("ERROR: The engine produced an empty buffer.")
            return outcome
        if volume != 1.0:
            samples = np.clip(samples * float(volume), -1.0, 1.0).astype(np.float32)
        if peak_level(samples) > 1.0:
            samples = normalise_audio(samples, target_db=-1.0, peak_limit=0.999)
            outcome.warnings.append("The audio was normalised to avoid clipping.")

        outcome.result = rendered[-1]
        outcome.duration_s = samples.shape[0] / float(sample_rate_used)
        if write_to is not None:
            path = self.write_output(
                samples, sample_rate_used, write_to, fmt=output_format, bit_depth=bit_depth
            )
            outcome.output_path = path
        outcome.seconds_taken = time.perf_counter() - started
        report(1.0, "Finished")
        log.info(
            "synthesis finished",
            extra={
                "event": "tts_finished",
                "chunks": len(chunks),
                "audio_seconds": round(outcome.duration_s, 2),
                "seconds": round(outcome.seconds_taken, 2),
                "voice": voice_id,
            },
        )
        return outcome

    # -- output -------------------------------------------------------------
    def write_output(
        self,
        samples: np.ndarray,
        sample_rate: int,
        destination: Path,
        *,
        fmt: str = "wav",
        bit_depth: int = 16,
    ) -> Path:
        """Write audio to ``destination`` (never overwriting) and return the path."""
        destination = Path(destination)
        target = unique_path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if fmt.lower() in ("wav", "wave"):
            write_wav(target, samples, sample_rate, bit_depth=bit_depth)
            return target
        from ..audio.converter import AudioConverter

        converter = AudioConverter()
        temp_wav = unique_path(target.with_suffix(".tmp.wav"))
        write_wav(temp_wav, samples, sample_rate, bit_depth=bit_depth)
        try:
            converter.convert(temp_wav, target, format=fmt)
        finally:
            temp_wav.unlink(missing_ok=True)
        return target

    # -- helpers ------------------------------------------------------------
    def _merge(
        self,
        pieces: Sequence[SynthesisResult],
        chunks: Sequence[Any],
        target_rate: int,
    ) -> tuple[np.ndarray, int, list[str]]:
        warnings: list[str] = []
        buffers: list[np.ndarray] = []
        pauses: list[int] = []
        rate = target_rate
        for index, piece in enumerate(pieces):
            samples = np.asarray(piece.samples, dtype=np.float32)
            if samples.ndim > 1:
                samples = samples.mean(axis=1)
            if piece.sample_rate and piece.sample_rate != rate:
                if index == 0:
                    rate = int(piece.sample_rate)
                else:
                    from ..audio.dsp import resample

                    samples = resample(samples, piece.sample_rate, rate)
            if samples.size == 0:
                warnings.append(f"Part {index + 1} produced no audio and was skipped.")
                continue
            buffers.append(samples)
            pause_ms = getattr(chunks[index], "pause_ms", 0) if index < len(chunks) else 0
            pauses.append(int(pause_ms or 0))
        if not buffers:
            return np.zeros(0, dtype=np.float32), rate, warnings
        merged = concat_with_pauses(buffers, rate, pauses)
        return merged, rate, warnings


def estimate_duration(text: str, *, chars_per_second: float = 14.0) -> float:
    """Rough speaking-time estimate used before a synthesis run starts."""
    characters = len(text or "")
    return max(0.0, characters / max(1.0, chars_per_second))
