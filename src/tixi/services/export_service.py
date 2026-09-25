"""Export everything the user can create.

Three families of exports, all of them with the same guarantees:

* **audio** — WAV / FLAC / MP3 / OGG (+ Opus/M4A when FFmpeg is present), with
  optional peak normalisation, silence trimming, gain and sample-rate changes,
  written through :class:`tixi.audio.converter.AudioConverter` (atomic writes);
* **transcripts** — txt, srt, vtt, json, csv, md;
* **tables** — history and library listings as CSV/JSON/Markdown.

No file is ever overwritten: :func:`tixi.utils.atomic.unique_path` appends
`` (2)``, `` (3)``… and the caller is told which path was really used.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from ..app.logging_config import get_logger
from ..audio.converter import AudioConverter, ConversionError
from ..audio.dsp import (
    apply_gain,
    normalise as normalise_audio,
    noise_gate,
    peak_level,
    resample,
    to_mono,
    trim_silence,
)
from ..audio.wav_io import read_audio
from ..services.transcription_service import TranscriptionOutcome
from ..utils.atomic import ensure_free_space, unique_path
from ..utils.humanize import human_duration, human_size

log = get_logger("tixi.services.exports")


@dataclass
class ExportRequest:
    """Everything an export dialog collects before the work starts."""

    source: Path
    destination: Path
    format: str = "wav"
    bit_depth: int = 16
    bitrate_kbps: int = 128
    sample_rate: int = 0            # 0 = keep the source rate
    channels: int = 1
    gain_db: float = 0.0
    normalise: bool = False
    trim: bool = False
    trim_threshold_db: float = -45.0
    denoise: bool = False
    mono: bool = False
    fade_ms: int = 0
    metadata: dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        bits = [self.format.upper()]
        if self.sample_rate:
            bits.append(f"{self.sample_rate / 1000:g} kHz")
        if self.format in ("mp3", "ogg", "opus", "m4a"):
            bits.append(f"{self.bitrate_kbps} kbps")
        else:
            bits.append(f"{self.bit_depth}-bit")
        if self.normalise:
            bits.append("normalised")
        if self.trim:
            bits.append("trimmed")
        if self.denoise:
            bits.append("denoised")
        if self.channels == 1:
            bits.append("mono")
        return " · ".join(bits)


@dataclass
class ExportResult:
    """Outcome of one export."""

    destination: Path | None = None
    requested: Path | None = None
    skipped: bool = False
    error: str = ""
    bytes_written: int = 0
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.destination is not None and not self.error

    @property
    def renamed(self) -> bool:
        return bool(self.destination and self.requested and self.destination != self.requested)

    def summary(self) -> str:
        if self.error:
            return self.error
        if self.destination is None:
            return "Nothing was exported."
        text = f"Exported to {self.destination.name}"
        if self.renamed:
            text += f" (the original name was already taken, so this one was used)"
        if self.bytes_written:
            text += f" · {human_size(self.bytes_written)}"
        return text


class ExportService:
    """Audio, transcript and table exports."""

    def __init__(
        self,
        library: Any = None,
        transcription: Any = None,
        *,
        settings_provider: Callable[[], Any] | None = None,
        converter: AudioConverter | None = None,
    ) -> None:
        self.library = library
        self.transcription = transcription
        self.settings_provider = settings_provider or (lambda: None)
        self.converter = converter or AudioConverter()

    # -- defaults -----------------------------------------------------------
    def default_dir(self) -> Path:
        settings = self.settings_provider()
        tts = getattr(settings, "tts", None)
        configured = str(getattr(tts, "export_dir", "") or "")
        if configured:
            return Path(configured)
        from ..app.paths import paths

        return paths().exports

    def suggested_name(self, source: Path, *, suffix: str = "", fmt: str = "") -> str:
        stem = Path(source).stem or "export"
        extension = (fmt or Path(source).suffix.lstrip(".") or "wav").lower()
        return f"{stem}{suffix}.{extension}"

    # -- audio --------------------------------------------------------------
    def export_audio(
        self,
        request: ExportRequest,
        *,
        progress: Callable[[float, str], None] | None = None,
    ) -> ExportResult:
        import time

        started = time.perf_counter()
        result = ExportResult(requested=Path(request.destination))
        source = Path(request.source)
        if not source.exists():
            result.error = f"The source file does not exist: {source}"
            return result
        try:
            data = read_audio(source, mono=request.mono)
        except Exception as exc:  # noqa: BLE001
            result.error = f"The audio could not be read: {exc}"
            return result

        samples = np.asarray(data.samples, dtype=np.float32)
        sample_rate = int(request.sample_rate or data.sample_rate)
        if request.mono and samples.ndim > 1:
            samples = to_mono(samples)
        if request.gain_db:
            samples = apply_gain(samples, request.gain_db)
        if request.denoise:
            samples = noise_gate(samples, sample_rate, threshold_db=-45.0)
        if request.trim:
            samples = trim_silence(samples, sample_rate, threshold_db=request.trim_threshold_db)
        if request.normalise:
            samples = normalise_audio(samples, target_db=-1.0)
        elif peak_level(samples) > 1.0:
            samples = np.clip(samples, -1.0, 1.0)
        if sample_rate != data.sample_rate:
            samples = resample(samples, data.sample_rate, sample_rate, quality="best")

        destination = unique_path(Path(request.destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        needed = int(samples.nbytes * 1.6) + 8 * 1024 * 1024
        try:
            ensure_free_space(destination.parent, needed)
        except OSError as exc:
            result.error = str(exc)
            return result
        try:
            if progress is not None:
                progress(0.3, f"Encoding {request.format.upper()}…")
            self.converter.save(
                samples,
                sample_rate,
                destination,
                format=request.format,
                bit_depth=request.bit_depth,
                channels=1 if request.mono or samples.ndim == 1 else int(samples.shape[1]),
                bitrate_kbps=request.bitrate_kbps,
                overwrite=False,
                id3=request.metadata or None,
            )
        except ConversionError as exc:
            result.error = str(exc)
            return result
        except Exception as exc:  # noqa: BLE001
            log.exception("audio export failed", extra={"event": "export_failed"})
            result.error = f"The export failed: {exc}"
            return result
        if progress is not None:
            progress(1.0, "Finished")
        result.destination = destination
        try:
            result.bytes_written = destination.stat().st_size
        except OSError:
            result.bytes_written = 0
        result.seconds = time.perf_counter() - started
        return result

    def export_asset(self, asset: Any, *, fmt: str = "", destination: Path | None = None, **kwargs: Any) -> ExportResult:
        """Export one library asset, converting it when a format is given."""
        source = Path(getattr(asset, "path", asset))
        target_format = (fmt or Path(source).suffix.lstrip(".") or "wav").lower()
        target = destination or (self.default_dir() / self.suggested_name(source, fmt=target_format))
        return self.export_audio(
            ExportRequest(source=source, destination=target, format=target_format, **kwargs)
        )

    # -- transcripts --------------------------------------------------------
    def export_transcript(
        self,
        outcome: TranscriptionOutcome | str,
        destination: Path,
        *,
        fmt: str = "txt",
        include_timestamps: bool = True,
        include_speakers: bool = True,
    ) -> ExportResult:
        result = ExportResult(requested=Path(destination))
        target = unique_path(Path(destination))
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if isinstance(outcome, str):
                text = outcome
            elif self.transcription is not None:
                text = self.transcription.render(
                    outcome,
                    fmt=fmt,
                    include_timestamps=include_timestamps,
                    include_speakers=include_speakers,
                )
            else:  # pragma: no cover - transcription service is always injected
                text = getattr(outcome, "text", "") or ""
            target.write_text(text, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            result.error = f"The transcript could not be written: {exc}"
            return result
        result.destination = target
        result.bytes_written = target.stat().st_size
        return result

    def transcript_bytes(
        self,
        outcome: TranscriptionOutcome | str,
        *,
        fmt: str = "txt",
        include_timestamps: bool = True,
        include_speakers: bool = True,
    ) -> bytes:
        if isinstance(outcome, str):
            text = outcome
        elif self.transcription is not None:
            text = self.transcription.render(
                outcome, fmt=fmt, include_timestamps=include_timestamps, include_speakers=include_speakers
            )
        else:  # pragma: no cover
            text = getattr(outcome, "text", "") or ""
        return text.encode("utf-8")

    # -- tables -------------------------------------------------------------
    def export_history(self, entries: Sequence[Any], destination: Path, *, fmt: str = "csv") -> ExportResult:
        rows = [
            {
                "id": getattr(entry, "id", ""),
                "kind": getattr(entry, "kind", ""),
                "title": getattr(entry, "title", ""),
                "created_at": getattr(entry, "created_at", ""),
                "language": getattr(entry, "language", ""),
                "model_id": getattr(entry, "model_id", ""),
                "duration_ms": getattr(entry, "duration_ms", 0),
                "characters": getattr(entry, "char_count", 0),
                "words": getattr(entry, "word_count", 0),
                "pinned": getattr(entry, "pinned", False),
                "text": getattr(entry, "text", ""),
            }
            for entry in entries
        ]
        return self._write_table(rows, destination, fmt=fmt, title="Tixi Voice history")

    def export_library(self, assets: Sequence[Any], destination: Path, *, fmt: str = "csv") -> ExportResult:
        rows = [
            {
                "id": getattr(asset, "id", ""),
                "file_name": getattr(asset, "file_name", ""),
                "format": getattr(asset, "format", ""),
                "duration_ms": getattr(asset, "duration_ms", 0),
                "sample_rate": getattr(asset, "sample_rate", 0),
                "channels": getattr(asset, "channels", 0),
                "size_bytes": getattr(asset, "size_bytes", 0),
                "created_at": getattr(asset, "created_at", ""),
                "favourite": getattr(asset, "favourite", False),
                "missing": getattr(asset, "missing", False),
                "voice_id": getattr(asset, "voice_id", ""),
                "model_id": getattr(asset, "model_id", ""),
                "path": getattr(asset, "path", ""),
                "source_text": getattr(asset, "source_text", ""),
            }
            for asset in assets
        ]
        return self._write_table(rows, destination, fmt=fmt, title="Tixi Voice audio library")

    def export_dictionary(self, entries: Sequence[Any], destination: Path, *, fmt: str = "json") -> ExportResult:
        rows = [
            {
                "word": entry.get("word", "") if isinstance(entry, dict) else getattr(entry, "word", ""),
                "replacement": entry.get("replacement", "") if isinstance(entry, dict) else getattr(entry, "replacement", ""),
                "language": entry.get("language", "") if isinstance(entry, dict) else getattr(entry, "language", ""),
                "case_sensitive": bool(entry.get("case_sensitive", False)) if isinstance(entry, dict) else bool(getattr(entry, "case_sensitive", False)),
                "priority": entry.get("priority", 0) if isinstance(entry, dict) else getattr(entry, "priority", 0),
                "note": entry.get("note", "") if isinstance(entry, dict) else getattr(entry, "note", ""),
            }
            for entry in entries
        ]
        return self._write_table(rows, destination, fmt=fmt, title="Tixi Voice pronunciation dictionary")

    def _write_table(self, rows: list[dict[str, Any]], destination: Path, *, fmt: str, title: str) -> ExportResult:
        result = ExportResult(requested=Path(destination))
        fmt = (fmt or "csv").lower()
        target = unique_path(Path(destination).with_suffix(f".{fmt}"))
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if fmt == "csv":
                buffer = io.StringIO()
                writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()) if rows else ["empty"])
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
                text = buffer.getvalue()
            elif fmt == "json":
                text = json.dumps({"generated_by": "Tixi Voice", "title": title, "rows": rows},
                                  ensure_ascii=False, indent=2)
            elif fmt in ("md", "markdown"):
                text = _markdown_table(rows, title)
            else:
                result.error = f"Unsupported table format: {fmt}"
                return result
            target.write_text(text, encoding="utf-8")
        except OSError as exc:
            result.error = f"The file could not be written: {exc}"
            return result
        result.destination = target
        result.bytes_written = target.stat().st_size
        return result

    # -- helpers ------------------------------------------------------------
    def describe_source(self, path: Path) -> str:
        """One line describing an audio file for the export dialog."""
        try:
            data = read_audio(path, mono=True)
        except Exception:  # noqa: BLE001
            return "Unknown audio"
        seconds = data.samples.shape[0] / max(1, data.sample_rate)
        return (
            f"{human_duration(seconds)} · {data.sample_rate / 1000:g} kHz · "
            f"{data.channels} channel(s) · {human_size(path.stat().st_size)}"
        )

    def available_formats(self) -> list[tuple[str, str]]:
        """``[(format, note), ...]`` — only formats this machine can actually write."""
        from ..audio.formats import format_capabilities

        rows: list[tuple[str, str]] = []
        for name, capability in format_capabilities().items():
            if not getattr(capability, "available", False):
                continue
            rows.append((name, capability.describe()))
        return rows


def estimate_export_size(
    seconds: float, *, fmt: str = "wav", bit_depth: int = 16, bitrate_kbps: int = 128, sample_rate: int = 22050
) -> int:
    """Rough output size, used to warn about disk space before encoding."""
    fmt = fmt.lower()
    if fmt in ("mp3", "ogg", "opus", "m4a"):
        return int(bitrate_kbps * 1000 / 8 * max(0.0, seconds))
    return int(max(0.0, seconds) * sample_rate * (bit_depth / 8) * 1.1)


def _markdown_table(rows: list[dict[str, Any]], title: str) -> str:
    if not rows:
        return f"# {title}\n\n_No rows._\n"
    columns = list(rows[0].keys())
    lines = [f"# {title}", "", "| " + " | ".join(columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        values = [str(row.get(column, "")).replace("|", "\\|").replace("\n", " ") for column in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"
