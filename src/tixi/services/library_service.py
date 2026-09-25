"""Audio library: importing files, generated clips and recordings.

Rules enforced here (they are product requirements, not preferences):

* an imported file is **copied** into the library, never moved,
* an existing file is **never overwritten** — a unique name is chosen instead,
* obvious duplicates are detected (same size + duration + name stem) and can be
  skipped, but nothing is ever deleted without asking,
* metadata (duration, sample rate, channels, peaks) is probed once and handed to
  the caller/UI,
* deleting an asset removes the database row first and the file only when asked.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from ..app.logging_config import get_logger
from ..audio.converter import AudioConverter
from ..audio.dsp import analyse, peak_level, resample, to_mono
from ..audio.formats import available_formats, normalise_format
from ..audio.wav_io import SUPPORTED_INPUT_SUFFIXES, audio_duration, read_audio, write_wav
from ..storage.library_repository import AudioAsset, AudioLibraryRepository
from ..utils.atomic import unique_path

log = get_logger("tixi.services.library")

WAVEFORM_PEAKS = 240


@dataclass
class ImportOutcome:
    """Result of importing one file."""

    asset: AudioAsset | None = None
    path: Path | None = None
    skipped: bool = False
    duplicate_of: Path | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.asset is not None and not self.error

    def summary(self) -> str:
        if self.error:
            return self.error
        if self.skipped and self.duplicate_of is not None:
            return f"Already in the library: {self.duplicate_of.name}"
        if self.asset is not None:
            return f"Imported {self.asset.file_name}"
        return "Nothing was imported."


@dataclass
class AudioMetadata:
    """Everything the UI needs to know about one audio file."""

    duration_s: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    bit_depth: int = 16
    format: str = ""
    peak: float = 0.0
    rms: float = 0.0
    notes: str = ""
    peaks: list[float] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "duration_s": round(self.duration_s, 3),
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bit_depth": self.bit_depth,
            "format": self.format,
            "peak": round(self.peak, 4),
            "rms": round(self.rms, 4),
            "notes": self.notes,
        }


class LibraryService:
    """High-level operations on the audio library."""

    def __init__(
        self,
        repository: AudioLibraryRepository,
        library_dir: Path,
        *,
        converter: AudioConverter | None = None,
    ) -> None:
        self.repository = repository
        self.library_dir = Path(library_dir)
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.converter = converter or AudioConverter()

    # -- importing ----------------------------------------------------------
    @staticmethod
    def is_supported(path: str | Path) -> bool:
        suffix = Path(path).suffix.lower()
        return suffix in SUPPORTED_INPUT_SUFFIXES or suffix.lstrip(".") in available_formats()

    def import_file(
        self,
        source: str | Path,
        *,
        history_id: int | None = None,
        source_text: str = "",
        language: str = "",
        tags: Sequence[str] = (),
        copy: bool = True,
        check_duplicates: bool = True,
        replace_path: str = "",
    ) -> ImportOutcome:
        """Bring ``source`` into the library and register it."""
        source_path = Path(source)
        if not source_path.exists():
            return ImportOutcome(error=f"The file does not exist: {source_path}")
        if not source_path.is_file():
            return ImportOutcome(error=f"Not a file: {source_path}")
        if not self.is_supported(source_path):
            return ImportOutcome(
                error=(
                    f"{source_path.name} is not a supported audio format "
                    f"(supported: {', '.join(sorted(available_formats()))})."
                )
            )

        metadata = self.read_metadata(source_path)
        if check_duplicates:
            existing = self.find_duplicate(source_path, metadata)
            if existing is not None and existing.path != str(source_path):
                log.info("duplicate import skipped", extra={"event": "library_duplicate"})
                return ImportOutcome(
                    asset=existing, path=Path(existing.path), skipped=True, duplicate_of=Path(existing.path)
                )

        if replace_path:
            destination = Path(replace_path)
        elif copy:
            destination = unique_path(self.library_dir / source_path.name)
        else:
            destination = source_path

        if destination != source_path:
            try:
                shutil.copy2(source_path, destination)
            except OSError as exc:
                return ImportOutcome(error=f"Could not copy the file: {exc}")

        asset_id = self.repository.add(
            destination,
            history_id=history_id,
            source_text=source_text,
            language=language,
            duration_ms=int(metadata.duration_s * 1000),
            sample_rate=metadata.sample_rate,
            channels=metadata.channels,
            bit_depth=metadata.bit_depth,
            tags=list(tags),
        )
        asset = self.repository.get(asset_id)
        log.info(
            "audio imported",
            extra={
                "event": "library_import",
                "seconds": round(metadata.duration_s, 2),
                "bytes": destination.stat().st_size,
            },
        )
        return ImportOutcome(asset=asset, path=destination)

    def import_many(
        self,
        paths: Iterable[str | Path],
        *,
        tags: Sequence[str] = (),
        skip_duplicates: bool = True,
    ) -> tuple[list[ImportOutcome], int]:
        outcomes: list[ImportOutcome] = []
        imported = 0
        for item in paths:
            outcome = self.import_file(item, tags=tags, check_duplicates=skip_duplicates)
            outcomes.append(outcome)
            if outcome.ok and not outcome.skipped:
                imported += 1
        return outcomes, imported

    def register_generated(
        self,
        path: str | Path,
        *,
        history_id: int | None = None,
        source_text: str = "",
        voice_id: str = "",
        model_id: str = "",
        language: str = "",
        tags: Sequence[str] = (),
    ) -> AudioAsset | None:
        """Register a file the app just produced (TTS output, recording, export)."""
        file_path = Path(path)
        if not file_path.exists():
            return None
        metadata = self.read_metadata(file_path)
        asset_id = self.repository.add(
            file_path,
            history_id=history_id,
            source_text=source_text,
            voice_id=voice_id,
            model_id=model_id,
            language=language,
            duration_ms=int(metadata.duration_s * 1000),
            sample_rate=metadata.sample_rate,
            channels=metadata.channels,
            bit_depth=metadata.bit_depth,
            tags=list(tags),
        )
        return self.repository.get(asset_id)

    # -- metadata -----------------------------------------------------------
    def read_metadata(self, path: str | Path, *, with_peaks: bool = False) -> AudioMetadata:
        """Read duration/sample rate/channels (and optionally the peak envelope)."""
        file_path = Path(path)
        metadata = AudioMetadata(format=normalise_format(file_path.suffix.lstrip(".") or "wav"))
        if not file_path.exists():
            metadata.notes = "File is missing."
            return metadata
        try:
            data = read_audio(file_path, mono=False)
        except Exception as exc:  # noqa: BLE001 - reported as a note, never fatal
            log.warning("metadata probe failed", extra={"event": "probe_failed", "error": str(exc)})
            metadata.notes = f"Could not read the audio: {exc}"
            return metadata
        audio = np.asarray(data.samples, dtype=np.float32)
        mono = to_mono(audio)
        metadata.sample_rate = int(data.sample_rate)
        metadata.channels = int(data.channels or (1 if audio.ndim == 1 else audio.shape[1]))
        metadata.bit_depth = int(data.bit_depth or 16)
        metadata.duration_s = float(data.duration_s or (mono.shape[0] / max(1, data.sample_rate)))
        stats = analyse(mono, data.sample_rate) if mono.size else None
        if stats is not None:
            metadata.peak = float(stats.peak)
            metadata.rms = float(stats.rms)
            if stats.clipping_ratio > 0.01:
                metadata.notes = "Parts of the recording clipped."
            elif stats.silence_ratio > 0.97:
                metadata.notes = "The clip is almost silent."
        if metadata.duration_s < 0.05:
            metadata.notes = "The clip is extremely short."
        if with_peaks:
            metadata.peaks = self.waveform_peaks(mono)
        return metadata

    def waveform_peaks(self, samples: np.ndarray, *, buckets: int = WAVEFORM_PEAKS) -> list[float]:
        mono = to_mono(np.asarray(samples, dtype=np.float32))
        if mono.size == 0:
            return []
        buckets = max(8, min(4000, buckets))
        absolute = np.abs(mono)
        if mono.size <= buckets:
            return [float(value) for value in absolute]
        edges = np.linspace(0, mono.size, buckets + 1, dtype=np.int64)
        return [
            float(absolute[start:end].max()) if end > start else 0.0
            for start, end in zip(edges[:-1], edges[1:])
        ]

    def peak_list(self, path: str | Path, *, buckets: int = WAVEFORM_PEAKS) -> list[float]:
        """Load a file and return its peak envelope (for waveform views)."""
        try:
            data = read_audio(path, mono=True)
            return self.waveform_peaks(np.asarray(data.samples), buckets=buckets)
        except Exception:  # noqa: BLE001 - a missing preview must not break the UI
            return []

    def duration_of(self, path: str | Path) -> float:
        try:
            return float(audio_duration(path))
        except Exception:  # noqa: BLE001
            return 0.0

    # -- maintenance --------------------------------------------------------
    def normalise_file(self, path: str | Path, *, target_peak: float = 0.97) -> Path:
        """Write a peak-normalised copy next to the original (never overwrite)."""
        source = Path(path)
        data = read_audio(source, mono=False)
        audio = np.asarray(data.samples, dtype=np.float32)
        current = peak_level(audio)
        if current <= 1e-6:
            return source
        gain = min(8.0, target_peak / current)
        normalised = np.clip(audio * gain, -1.0, 1.0).astype(np.float32)
        target = unique_path(source.with_name(f"{source.stem} (normalised).wav"))
        write_wav(target, normalised, data.sample_rate, bit_depth=data.bit_depth or 16)
        return target

    def resample_file(self, path: str | Path, sample_rate: int) -> Path:
        source = Path(path)
        data = read_audio(source, mono=False)
        converted = resample(np.asarray(data.samples, dtype=np.float32), data.sample_rate, sample_rate)
        target = unique_path(source.with_name(f"{source.stem} ({sample_rate // 1000}k).wav"))
        write_wav(target, converted, sample_rate, bit_depth=data.bit_depth or 16)
        return target

    def export_copy(self, asset: AudioAsset, destination: Path) -> Path:
        """Copy an asset to ``destination`` (parent dirs created, no overwrite)."""
        target = unique_path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset.path, target)
        return target

    def delete_asset(self, asset_id: int, *, remove_file: bool = False) -> bool:
        return self.repository.delete(asset_id, remove_file=remove_file)

    def total_size(self) -> int:
        return self.repository.total_size()

    def count(self) -> int:
        return self.repository.count()

    def reconcile(self) -> list[AudioAsset]:
        """Return assets whose file vanished (shown as "missing" in the UI)."""
        return [asset for asset in self.repository.list(limit=10000) if not asset.exists]

    def checksum(self, path: str | Path) -> str:
        return _sha256(Path(path))

    def find_duplicate(self, path: str | Path, metadata: AudioMetadata | None = None) -> AudioAsset | None:
        """Find an asset that looks like the same audio (size + duration + stem)."""
        candidate = Path(path)
        try:
            size = candidate.stat().st_size
        except OSError:
            return None
        meta = metadata or self.read_metadata(candidate)
        for asset in self.repository.list(limit=5000):
            if Path(asset.path) == candidate:
                return asset
            if asset.size_bytes != size:
                continue
            same_stem = Path(asset.file_name).stem.lower() == candidate.stem.lower()
            if same_stem or abs(asset.duration_ms - int(meta.duration_s * 1000)) <= 50:
                return asset
        return None


def _sha256(path: Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(chunk)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()
