"""Audio conversion and export.

Backend order (first available wins):

1. **native encoders** — standard library WAV, ``lameenc`` (MP3),
   ``soundfile``/libsndfile (FLAC, OGG Vorbis),
2. **FFmpeg** — if an ``ffmpeg`` executable is on ``PATH`` or configured,
   used for Opus/M4A and as a general fallback,
3. **PyAV** — shipped with the Speech Recognition engine pack.

Every write is atomic, and existing files are never overwritten without an
explicit ``overwrite=True`` (the caller offers numbering or confirmation).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from ..app.logging_config import get_logger
from ..utils.atomic import atomic_file, unique_path
from . import formats as fmt
from .dsp import to_float32, to_mono

log = get_logger("tixi.audio.converter")

ProgressCallback = Callable[[float, str], None]


class ConversionError(RuntimeError):
    """Raised when a format cannot be produced on this machine."""


@dataclass
class ConversionResult:
    path: Path
    format: str
    backend: str
    sample_rate: int
    channels: int
    duration_s: float
    size_bytes: int
    warnings: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "format": self.format,
            "backend": self.backend,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "duration_s": round(self.duration_s, 3),
            "size_bytes": self.size_bytes,
            "warnings": self.warnings or [],
        }


class AudioConverter:
    """Writes float32 sample buffers to any supported container."""

    def __init__(self, ffmpeg_path: str | Path | None = None) -> None:
        self._ffmpeg = Path(ffmpeg_path) if ffmpeg_path else None

    # -- backend discovery --------------------------------------------------
    def ffmpeg(self) -> str | None:
        if self._ffmpeg and Path(self._ffmpeg).exists():
            return str(self._ffmpeg)
        return find_ffmpeg()

    def set_ffmpeg(self, path: str | Path | None) -> None:
        self._ffmpeg = Path(path) if path else None

    # -- main entry point ---------------------------------------------------
    def save(
        self,
        samples: np.ndarray,
        sample_rate: int,
        target: str | Path,
        *,
        format: str = "wav",
        bit_depth: int = 16,
        channels: int = 1,
        bitrate_kbps: int = 128,
        overwrite: bool = False,
        progress: ProgressCallback | None = None,
        id3: dict[str, str] | None = None,
    ) -> ConversionResult:
        """Write ``samples`` to ``target`` in the requested format."""
        target_path = Path(target)
        requested = fmt.normalise_format(format or target_path.suffix)
        capabilities = fmt.format_capabilities()
        capability = capabilities.get(requested)
        if capability is None:
            raise ConversionError(f"Unsupported audio format: {format}")
        if not capability.available:
            raise ConversionError(
                f"{capability.label} export is not available: {capability.reason}."
            )

        array = to_float32(np.asarray(samples))
        if channels == 1:
            array = to_mono(array)
            actual_channels = 1
        else:
            if array.ndim == 1:
                array = np.stack([array, array], axis=1)
            actual_channels = min(int(array.shape[1]), int(channels))

        if target_path.exists() and not overwrite:
            target_path = unique_path(target_path)
        if target_path.suffix.lower().lstrip(".") != requested and requested in ("wav", "flac", "mp3", "ogg", "opus", "m4a"):
            target_path = target_path.with_suffix(fmt.extension_for(requested))

        duration = array.shape[0] / sample_rate if sample_rate else 0.0
        if progress:
            progress(0.05, f"Encoding {capability.label}")

        backend: str
        warnings: list[str] = []
        if requested == "wav":
            backend = "standard library"
            self._write_wav(target_path, array, sample_rate, bit_depth, overwrite=True)
        elif requested == "flac" and fmt.has_soundfile():
            backend = "libsndfile"
            self._write_soundfile(target_path, array, sample_rate, "FLAC", f"PCM_{bit_depth}")
        elif requested == "ogg" and fmt.has_soundfile():
            backend = "libsndfile"
            self._write_soundfile(target_path, array, sample_rate, "OGG", "VORBIS")
        elif requested == "mp3" and fmt.has_lameenc():
            backend = "lameenc"
            self._write_mp3(target_path, array, sample_rate, bitrate_kbps, actual_channels, id3)
        else:
            ffmpeg = self.ffmpeg()
            if ffmpeg:
                backend = "FFmpeg"
                self._encode_with_ffmpeg(
                    ffmpeg, target_path, array, sample_rate, requested, bit_depth,
                    bitrate_kbps, actual_channels, id3, progress,
                )
            elif fmt.has_pyav():
                backend = "PyAV"
                self._encode_with_pyav(
                    target_path, array, sample_rate, requested, bitrate_kbps, actual_channels
                )
            else:
                raise ConversionError(
                    f"{capability.label} needs a conversion backend ({capability.reason})"
                )

        if progress:
            progress(1.0, "Export complete")
        size = target_path.stat().st_size if target_path.exists() else 0
        return ConversionResult(
            path=target_path,
            format=requested,
            backend=backend,
            sample_rate=int(sample_rate),
            channels=actual_channels,
            duration_s=duration,
            size_bytes=size,
            warnings=warnings,
        )

    # -- native writers -----------------------------------------------------
    def _write_wav(
        self, target: Path, array: np.ndarray, sample_rate: int, bit_depth: int, *, overwrite: bool
    ) -> None:
        from .wav_io import write_wav  # noqa: PLC0415

        write_wav(target, array, sample_rate, bit_depth=bit_depth, overwrite=overwrite)

    def _write_soundfile(
        self, target: Path, array: np.ndarray, sample_rate: int, container: str, subtype: str
    ) -> None:
        import soundfile as sf  # noqa: PLC0415

        with atomic_file(target, "wb") as handle:
            sf.write(handle, array, int(sample_rate), format=container, subtype=subtype)  # type: ignore[arg-type]

    def _write_mp3(
        self,
        target: Path,
        array: np.ndarray,
        sample_rate: int,
        bitrate_kbps: int,
        channels: int,
        id3: dict[str, str] | None,
    ) -> None:
        import lameenc  # noqa: PLC0415

        rates = fmt.FORMAT_SAMPLE_RATES["mp3"]
        if sample_rate not in rates:
            sample_rate = min(rates, key=lambda value: abs(value - sample_rate))
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(max(32, min(320, int(bitrate_kbps))))
        encoder.set_in_sample_rate(int(sample_rate))
        encoder.set_channels(int(channels))
        encoder.set_quality(2)
        if id3:
            with contextlib.suppress(Exception):
                encoder.set_id3_tag("TIT2", (id3.get("title") or "Tixi Voice")[:60])
                if id3.get("comment"):
                    encoder.set_id3_tag("TIT3", id3["comment"][:200])
        pcm = (np.clip(array, -1.0, 1.0) * 32767.0).astype(np.int16)
        stream = pcm.reshape(-1) if pcm.ndim > 1 else pcm
        data = encoder.encode(stream.tobytes())
        data += encoder.flush()
        with atomic_file(target, "wb") as handle:
            handle.write(data)

    def _encode_with_pyav(
        self,
        target: Path,
        array: np.ndarray,
        sample_rate: int,
        container: str,
        bitrate_kbps: int,
        channels: int,
    ) -> None:
        import av  # noqa: PLC0415

        codecs = {
            "mp3": ("mp3", "mp3"),
            "opus": ("libopus", "ogg"),
            "m4a": ("aac", "mp4"),
            "ogg": ("libvorbis", "ogg"),
            "flac": ("flac", "flac"),
        }
        if container not in codecs:
            raise ConversionError(f"PyAV cannot write {container}")
        codec, format_name = codecs[container]
        layout = "mono" if channels == 1 else "stereo"
        frame = av.AudioFrame.from_ndarray(
            (np.clip(array, -1, 1) * 32767).astype(np.int16).reshape(1, -1),
            format="s16",
            layout=layout,
        )
        frame.rate = sample_rate
        with atomic_file(target, "wb") as handle:
            with av.open(handle, mode="w", format=format_name) as output:  # type: ignore[arg-type]
                stream = output.add_stream(codec, rate=sample_rate)
                stream.bit_rate = bitrate_kbps * 1000
                for packet in stream.encode(frame):
                    output.mux(packet)
                for packet in stream.encode(None):
                    output.mux(packet)

    # -- FFmpeg -------------------------------------------------------------
    def _encode_with_ffmpeg(
        self,
        ffmpeg: str,
        target: Path,
        array: np.ndarray,
        sample_rate: int,
        container: str,
        bit_depth: int,
        bitrate_kbps: int,
        channels: int,
        id3: dict[str, str] | None,
        progress: ProgressCallback | None,
    ) -> None:
        codec_args = {
            "wav": ["-c:a", f"pcm_s{bit_depth}le"],
            "flac": ["-c:a", "flac", "-compression_level", "5"],
            "mp3": ["-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k"],
            "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
            "opus": ["-c:a", "libopus", "-b:a", f"{max(16, bitrate_kbps)}k", "-application", "audio"],
            "m4a": ["-c:a", "aac", "-b:a", f"{bitrate_kbps}k"],
        }.get(container)
        if codec_args is None:
            raise ConversionError(f"FFmpeg export for {container} is not configured")

        args = [
            "-hide_banner",
            "-loglevel", "error",
            "-nostdin",
            "-f", "f32le",
            "-ar", str(int(sample_rate)),
            "-ac", str(int(channels)),
            "-i", "pipe:0",
            # The output always goes to a fresh temporary file, so "-n"
            # (never overwrite) is correct and never harms user data.
            "-n",
            *codec_args,
        ]
        for key, value in (id3 or {}).items():
            tag = {"title": "title", "artist": "artist", "comment": "comment", "album": "album"}.get(key)
            if tag and value:
                args += ["-metadata", f"{tag}={value}"]
        with atomic_file(target, "wb") as handle:
            args.append(str(handle.name))
            process = subprocess.Popen(
                [ffmpeg, *args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_no_window_flags(),
            )
            raw = (np.clip(array, -1.0, 1.0) * 1.0).astype("<f4").tobytes()
            try:
                _, stderr = process.communicate(input=raw, timeout=900)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()
                raise ConversionError("FFmpeg timed out while converting the audio") from None
            if process.returncode != 0:
                message = (stderr or b"").decode("utf-8", "replace").strip() or "unknown FFmpeg error"
                raise ConversionError(f"FFmpeg failed: {message}")
        if progress:
            progress(0.95, "Finalising file")

    # -- inspection ---------------------------------------------------------
    def probe(self, path: str | Path) -> dict[str, Any]:
        """Return container/codec/duration information for a media file."""
        file_path = Path(path)
        info: dict[str, Any] = {"path": str(file_path), "exists": file_path.exists()}
        if not info["exists"]:
            return info
        info["size_bytes"] = file_path.stat().st_size
        try:
            import soundfile as sf  # noqa: PLC0415

            if file_path.suffix.lower() in (".wav", ".flac", ".ogg", ".oga"):
                details = sf.info(str(file_path))
                info.update(
                    {
                        "format": details.format,
                        "subtype": details.subtype,
                        "sample_rate": details.samplerate,
                        "channels": details.channels,
                        "frames": details.frames,
                        "duration_s": details.duration,
                    }
                )
                return info
        except Exception:  # noqa: BLE001
            pass
        ffmpeg = self.ffmpeg()
        if ffmpeg:
            probe = shutil.which("ffprobe")
            if probe:
                try:
                    result = subprocess.run(
                        [
                            probe, "-v", "quiet", "-print_format", "json",
                            "-show_format", "-show_streams", str(file_path),
                        ],
                        capture_output=True,
                        timeout=30,
                        creationflags=_no_window_flags(),
                    )
                    payload = json.loads(result.stdout or b"{}")
                    audio_stream = next(
                        (s for s in payload.get("streams", []) if s.get("codec_type") == "audio"),
                        {},
                    )
                    duration = float(payload.get("format", {}).get("duration", 0) or 0)
                    info.update(
                        {
                            "format": payload.get("format", {}).get("format_name", ""),
                            "codec": audio_stream.get("codec_name", ""),
                            "sample_rate": int(audio_stream.get("sample_rate", 0) or 0),
                            "channels": int(audio_stream.get("channels", 0) or 0),
                            "duration_s": duration,
                        }
                    )
                except Exception:  # noqa: BLE001
                    pass
        return info


# ---------------------------------------------------------------------------
# FFmpeg helpers (module level so audio/wav_io.py can reuse them)
# ---------------------------------------------------------------------------
def find_ffmpeg() -> str | None:
    """Locate an FFmpeg executable: setting → PATH → common Windows locations."""
    override = os.environ.get("TIXI_FFMPEG")
    if override and Path(override).exists():
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
    ]
    for candidate in candidates:
        with contextlib.suppress(OSError):
            if candidate.exists():
                return str(candidate)
    return None


def _no_window_flags() -> int:
    if os.name == "nt":
        return 0x08000000  # CREATE_NO_WINDOW
    return 0


def run_ffmpeg(args: Sequence[str], *, capture_stdout: bool = False, timeout: int = 600) -> bytes:
    """Run FFmpeg with the given arguments and return stdout."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise ConversionError("FFmpeg was not found on this system")
    result = subprocess.run(
        [ffmpeg, *args],
        capture_output=True,
        timeout=timeout,
        creationflags=_no_window_flags(),
    )
    if result.returncode != 0 and not capture_stdout:
        message = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise ConversionError(message or "FFmpeg failed")
    return result.stdout if capture_stdout else b""


def probe_duration(path: Path) -> float:
    """Duration via FFprobe/FFmpeg, returning 0.0 when unknown."""
    probe = shutil.which("ffprobe")
    if probe:
        try:
            result = subprocess.run(
                [
                    probe, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(path),
                ],
                capture_output=True,
                timeout=30,
                creationflags=_no_window_flags(),
            )
            return float(result.stdout.decode().strip() or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0
    return 0.0


class ConversionWorker:
    """Background conversion with cancellation (used by the export dialog)."""

    def __init__(self, converter: AudioConverter | None = None) -> None:
        self.converter = converter or AudioConverter()
        self._cancelled = threading.Event()
        self._thread: threading.Thread | None = None

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def start(
        self,
        samples: np.ndarray,
        sample_rate: int,
        target: Path,
        *,
        on_done: Callable[[ConversionResult], None],
        on_error: Callable[[Exception], None],
        **kwargs: Any,
    ) -> None:
        def _run() -> None:
            try:
                started = time.perf_counter()
                result = self.converter.save(samples, sample_rate, target, **kwargs)
                log.info(
                    "audio exported",
                    extra={
                        "event": "audio_export",
                        "format": result.format,
                        "backend": result.backend,
                        "seconds": round(time.perf_counter() - started, 2),
                        "size": result.size_bytes,
                    },
                )
                if not self._cancelled.is_set():
                    on_done(result)
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI thread
                on_error(exc)

        self._cancelled.clear()
        self._thread = threading.Thread(target=_run, name="tixi-export", daemon=True)
        self._thread.start()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)
