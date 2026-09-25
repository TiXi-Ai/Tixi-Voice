"""Reading and writing audio files (WAV natively, everything else via libsndfile).

``soundfile`` (libsndfile) covers WAV, FLAC and OGG/Vorbis and is a small
dependency.  WAV is additionally writable through the standard library so the
recorder keeps working even if libsndfile is missing.
"""

from __future__ import annotations

import contextlib
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..app.logging_config import get_logger
from ..utils.atomic import atomic_file, unique_path
from .dsp import to_float32, to_int16, to_mono

log = get_logger("tixi.audio.io")


class AudioReadError(RuntimeError):
    """Raised when an audio file cannot be decoded."""


@dataclass
class AudioData:
    """Decoded audio: float32 samples plus stream metadata."""

    samples: np.ndarray
    sample_rate: int
    channels: int = 1
    bit_depth: int = 16
    format: str = "wav"
    duration_s: float = 0.0
    source: str = ""

    def mono(self) -> np.ndarray:
        return to_mono(self.samples) if self.samples.ndim > 1 else self.samples

    def __post_init__(self) -> None:
        if not self.duration_s and self.sample_rate:
            self.duration_s = float(self.samples.shape[0]) / self.sample_rate


SUPPORTED_INPUT_SUFFIXES = (
    ".wav", ".wave", ".flac", ".ogg", ".oga", ".opus", ".mp3", ".m4a", ".aac",
    ".wma", ".aiff", ".aif", ".au", ".mp4", ".mkv", ".webm", ".mov", ".avi",
)


def read_audio(path: str | Path, *, mono: bool = False, sample_rate: int | None = None) -> AudioData:
    """Decode an audio (or video) file to float32 samples.

    WAV goes through the standard library when possible; other formats use
    libsndfile, and container formats (MP4/MKV/WebM…) are handed to FFmpeg or
    PyAV when either is available.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise AudioReadError(f"The audio file no longer exists: {file_path}")
    if file_path.stat().st_size == 0:
        raise AudioReadError(f"The audio file is empty: {file_path.name}")

    suffix = file_path.suffix.lower()
    errors: list[str] = []

    if suffix in (".wav", ".wave"):
        try:
            return _read_wav_stdlib(file_path, mono=mono)
        except Exception as exc:  # noqa: BLE001 - fall through to libsndfile
            errors.append(f"stdlib wave: {exc}")

    try:
        import soundfile as sf  # noqa: PLC0415

        with sf.SoundFile(str(file_path)) as handle:
            data = handle.read(dtype="float32", always_2d=False)
            rate = int(handle.samplerate)
            channels = int(handle.channels)
            subtype = handle.subtype or "PCM_16"
        samples = to_float32(np.asarray(data))
        if mono and samples.ndim > 1:
            samples = to_mono(samples)
        bit_depth = int(subtype.split("_")[-1]) if subtype.startswith("PCM") and subtype[-2:].isdigit() else 16
        audio = AudioData(
            samples=samples,
            sample_rate=rate,
            channels=channels,
            bit_depth=bit_depth,
            format=suffix.lstrip("."),
            source=str(file_path),
        )
        if sample_rate and sample_rate != rate:
            from .dsp import resample  # noqa: PLC0415

            audio.samples = resample(audio.samples, rate, sample_rate)
            audio.sample_rate = sample_rate
        return audio
    except ImportError:
        errors.append("soundfile is not installed")
    except Exception as exc:  # noqa: BLE001 - try the heavier backends next
        errors.append(f"libsndfile: {exc}")

    decoded = _decode_with_ffmpeg(file_path, mono=mono, sample_rate=sample_rate)
    if decoded is not None:
        return decoded

    decoded = _decode_with_pyav(file_path, mono=mono, sample_rate=sample_rate)
    if decoded is not None:
        return decoded

    detail = "; ".join(errors) if errors else "no decoder available"
    raise AudioReadError(
        f"Could not decode {file_path.name}. {detail}. "
        "Install FFmpeg or the Speech Recognition engine pack (which bundles a "
        "media decoder) to import this format."
    )


def _read_wav_stdlib(path: Path, *, mono: bool = False) -> AudioData:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width == 1:
        array = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        array = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        values = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
        values = np.where(values & 0x800000, values - 0x1000000, values)
        array = values.astype(np.float32) / 8388608.0
    elif width == 4:
        array = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise AudioReadError(f"unsupported WAV bit depth: {width * 8} bit")
    if channels > 1:
        array = array.reshape(-1, channels)
        if mono:
            array = to_mono(array)
    return AudioData(
        samples=array.astype(np.float32),
        sample_rate=rate,
        channels=1 if mono else channels,
        bit_depth=width * 8,
        format="wav",
        source=str(path),
    )


def _decode_with_ffmpeg(
    path: Path, *, mono: bool, sample_rate: int | None
) -> AudioData | None:
    from .converter import find_ffmpeg, run_ffmpeg  # noqa: PLC0415 - avoid import cycle

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    target_rate = sample_rate or 16000
    channels = 1 if mono else 2
    args = [
        "-i",
        str(path),
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(target_rate),
        "-f",
        "f32le",
        "-",
    ]
    try:
        raw = run_ffmpeg(args, capture_stdout=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("ffmpeg decode failed", extra={"event": "ffmpeg_decode_failed", "error": str(exc)})
        return None
    samples = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels)
    return AudioData(
        samples=samples,
        sample_rate=target_rate,
        channels=channels,
        bit_depth=32,
        format="decoded",
        source=str(path),
    )


def _decode_with_pyav(
    path: Path, *, mono: bool, sample_rate: int | None
) -> AudioData | None:
    try:
        import av  # noqa: PLC0415 - installed with the STT engine pack
    except ImportError:
        return None
    try:
        with av.open(str(path)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise AudioReadError(f"{path.name} contains no audio stream")
            target_rate = sample_rate or int(stream.rate or 16000)
            resampler = av.AudioResampler(
                format="fltp",
                layout="mono" if mono else "stereo",
                rate=target_rate,
            )
            chunks: list[np.ndarray] = []
            for frame in container.decode(stream):
                for converted in resampler.resample(frame):
                    array = converted.to_ndarray()
                    chunks.append(array.reshape(-1) if mono else array.T.reshape(-1, 2))
            for converted in resampler.resample(None):
                array = converted.to_ndarray()
                chunks.append(array.reshape(-1) if mono else array.T.reshape(-1, 2))
        if not chunks:
            raise AudioReadError(f"{path.name} produced no audio samples")
        samples = np.concatenate(chunks).astype(np.float32)
        return AudioData(
            samples=samples,
            sample_rate=target_rate,
            channels=1 if mono else 2,
            bit_depth=32,
            format="decoded",
            source=str(path),
        )
    except AudioReadError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("PyAV decode failed", extra={"event": "pyav_decode_failed", "error": str(exc)})
        return None


def write_wav(
    path: str | Path,
    samples: np.ndarray,
    sample_rate: int,
    *,
    bit_depth: int = 16,
    overwrite: bool = False,
    channels: int | None = None,
) -> Path:
    """Write float32 samples as a PCM WAV file (atomic, never overwrites silently)."""
    target = Path(path)
    if target.exists() and not overwrite:
        target = unique_path(target)
    array = to_float32(np.asarray(samples))
    expected_channels = channels or (1 if array.ndim == 1 else array.shape[1])
    if array.ndim == 1 and expected_channels == 2:
        array = np.stack([array, array], axis=1)
    if array.ndim > 1 and array.shape[1] != expected_channels:
        array = array[:, :expected_channels]

    with atomic_file(target, "wb") as handle:
        with contextlib.closing(wave.open(handle, "wb")) as writer:  # type: ignore[arg-type]
            writer.setnchannels(expected_channels)
            writer.setsampwidth(bit_depth // 8)
            writer.setframerate(int(sample_rate))
            writer.writeframes(_pcm_bytes(array, bit_depth))
    return target


def _pcm_bytes(array: np.ndarray, bit_depth: int) -> bytes:
    if bit_depth == 16:
        return to_int16(array).tobytes()
    if bit_depth == 24:
        clipped = np.clip(array, -1.0, 1.0)
        ints = (clipped * 8388607.0).astype(np.int32)
        raw = ints.view(np.uint32).astype(np.uint32)
        bytes_le = np.empty((ints.shape[0], 3), dtype=np.uint8)
        bytes_le[:, 0] = (raw & 0xFF).astype(np.uint8)
        bytes_le[:, 1] = ((raw >> 8) & 0xFF).astype(np.uint8)
        bytes_le[:, 2] = ((raw >> 16) & 0xFF).astype(np.uint8)
        return bytes_le.reshape(-1).tobytes()
    if bit_depth == 32:
        clipped = np.clip(array, -1.0, 1.0)
        return (clipped * 2147483647.0).astype(np.int32).tobytes()
    raise ValueError(f"unsupported bit depth: {bit_depth}")


def write_flac(
    path: str | Path,
    samples: np.ndarray,
    sample_rate: int,
    *,
    bit_depth: int = 16,
    overwrite: bool = False,
) -> Path:
    """Write FLAC through libsndfile."""
    import soundfile as sf  # noqa: PLC0415

    target = Path(path)
    if target.exists() and not overwrite:
        target = unique_path(target)
    array = to_float32(np.asarray(samples))
    with atomic_file(target, "wb") as handle:
        sf.write(handle, array, int(sample_rate), format="FLAC", subtype=f"PCM_{bit_depth}")  # type: ignore[arg-type]
    return target


def audio_duration(path: str | Path) -> float:
    """Duration in seconds using the cheapest available method."""
    file_path = Path(path)
    if file_path.suffix.lower() in (".wav", ".wave"):
        try:
            with wave.open(str(file_path), "rb") as handle:
                frames, rate = handle.getnframes(), handle.getframerate()
            return frames / rate if rate else 0.0
        except Exception:  # noqa: BLE001 - fall through
            pass
    try:
        import soundfile as sf  # noqa: PLC0415

        info = sf.info(str(file_path))
        return float(info.frames) / float(info.samplerate or 1)
    except Exception:  # noqa: BLE001
        pass
    from .converter import probe_duration  # noqa: PLC0415

    return probe_duration(file_path)
