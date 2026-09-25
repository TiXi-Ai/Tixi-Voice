"""Audio format metadata and export capability detection.

Tixi Voice can always write **WAV** (standard library) and — when the small
audio helpers are present — **FLAC**, **OGG/Vorbis** (libsndfile) and **MP3**
(bundled LAME encoder).  **Opus** and **M4A/AAC** need a conversion backend:
either an FFmpeg executable or the PyAV decoder that ships with the Speech
Recognition engine pack.

The UI reads :func:`format_capabilities` to label each choice as
"native" (model/encoder output) or "converted" (requires a backend), exactly as
the product requirements ask.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

NATIVE_FORMATS = ("wav", "flac", "mp3", "ogg")
CONVERTED_FORMATS = ("opus", "m4a")

#: Sensible sample rates per format (libsndfile/Opus constraints).
FORMAT_SAMPLE_RATES: dict[str, tuple[int, ...]] = {
    "wav": (16000, 22050, 24000, 32000, 44100, 48000, 96000),
    "flac": (16000, 22050, 24000, 32000, 44100, 48000, 96000),
    "mp3": (16000, 22050, 24000, 32000, 44100, 48000),
    "ogg": (22050, 32000, 44100, 48000),
    "opus": (8000, 12000, 16000, 24000, 48000),
    "m4a": (22050, 32000, 44100, 48000, 96000),
}

FORMAT_MIME = {
    "wav": "audio/wav",
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "opus": "audio/opus",
    "m4a": "audio/mp4",
}


@dataclass(frozen=True)
class FormatCapability:
    """Whether one export format is available and how it is produced."""

    format: str
    label: str
    available: bool
    native: bool
    backend: str = ""
    reason: str = ""
    supports_sample_rate: bool = True
    supports_bit_depth: bool = False
    supports_channels: bool = True

    def describe(self) -> str:
        if self.available:
            origin = "native encoder" if self.native else f"converted ({self.backend})"
            return f"{self.label} — {origin}"
        return f"{self.label} — unavailable: {self.reason}"


def _has_module(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


def has_lameenc() -> bool:
    return _has_module("lameenc")


def has_soundfile() -> bool:
    return _has_module("soundfile")


def has_pyav() -> bool:
    return _has_module("av")


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def format_capabilities() -> dict[str, FormatCapability]:
    """Availability of every export format on this machine, right now."""
    ffmpeg = has_ffmpeg()
    pyav = has_pyav()
    converted_backend = "FFmpeg" if ffmpeg else ("PyAV" if pyav else "")
    capabilities = {
        "wav": FormatCapability(
            "wav", "WAV", True, True, "standard library",
            supports_bit_depth=True,
        ),
        "flac": FormatCapability(
            "flac", "FLAC",
            available=has_soundfile(),
            native=True,
            backend="libsndfile",
            reason="" if has_soundfile() else "the 'soundfile' package is not installed",
            supports_bit_depth=True,
        ),
        "mp3": FormatCapability(
            "mp3", "MP3",
            available=has_lameenc() or ffmpeg or pyav,
            native=has_lameenc(),
            backend="lameenc" if has_lameenc() else converted_backend,
            reason="" if (has_lameenc() or ffmpeg or pyav) else "no MP3 encoder is available",
        ),
        "ogg": FormatCapability(
            "ogg", "OGG Vorbis",
            available=has_soundfile() or ffmpeg or pyav,
            native=has_soundfile(),
            backend="libsndfile" if has_soundfile() else converted_backend,
            reason="" if (has_soundfile() or ffmpeg or pyav) else "no Vorbis encoder is available",
        ),
        "opus": FormatCapability(
            "opus", "Opus",
            available=ffmpeg or pyav,
            native=False,
            backend=converted_backend,
            reason="" if (ffmpeg or pyav) else "install FFmpeg or the Speech Recognition engine pack",
        ),
        "m4a": FormatCapability(
            "m4a", "M4A / AAC",
            available=ffmpeg or pyav,
            native=False,
            backend=converted_backend,
            reason="" if (ffmpeg or pyav) else "install FFmpeg",
        ),
    }
    return capabilities


def available_formats() -> list[str]:
    return [name for name, capability in format_capabilities().items() if capability.available]


def is_format_available(fmt: str) -> bool:
    capability = format_capabilities().get(fmt.lower())
    return bool(capability and capability.available)


def conversion_backend_status() -> dict[str, object]:
    """Diagnostics shown in Settings ▸ Audio and About ▸ Diagnostics."""
    return {
        "ffmpeg": _ffmpeg_info(),
        "pyav": has_pyav(),
        "libsndfile": has_soundfile(),
        "lame": has_lameenc(),
        "native_formats": [name for name in NATIVE_FORMATS if is_format_available(name)],
        "converted_formats": [name for name in CONVERTED_FORMATS if is_format_available(name)],
    }


def _ffmpeg_info() -> dict[str, object]:
    path = shutil.which("ffmpeg")
    if not path:
        return {"available": False, "path": ""}
    return {"available": True, "path": path}


def suggest_format_for_purpose(purpose: str) -> str:
    """The most sensible default format for a use case."""
    return {
        "voice_typing": "wav",
        "preview": "wav",
        "sharing": "mp3",
        "archive": "flac",
        "web": "opus",
        "subtitles": "wav",
    }.get(purpose, "wav")


def extension_for(fmt: str) -> str:
    return "." + fmt.lower().lstrip(".")


def normalise_format(value: str) -> str:
    fmt = (value or "wav").lower().lstrip(".")
    aliases = {"wave": "wav", "oga": "ogg", "vorbis": "ogg", "aac": "m4a", "mp4": "m4a", "mpeg": "mp3"}
    return aliases.get(fmt, fmt)
