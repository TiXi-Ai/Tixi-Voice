"""Audio file I/O and conversion."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tixi.audio.converter import AudioConverter, ConversionError
from tixi.audio.wav_io import audio_duration, read_audio, write_wav
from tixi.utils.atomic import unique_path


def _tone(seconds: float = 0.25, rate: int = 22050) -> np.ndarray:
    t = np.arange(int(seconds * rate), dtype=np.float32) / rate
    return (0.4 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)


def test_wav_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "tone.wav"
    write_wav(target, _tone(), 22050)
    data = read_audio(target, mono=True)
    assert data.sample_rate == 22050
    assert abs(data.duration_s - 0.25) < 0.02
    assert data.samples.shape[0] > 5000


def test_24_bit_wav_is_written_and_read(tmp_path: Path) -> None:
    target = tmp_path / "tone24.wav"
    write_wav(target, _tone(), 22050, bit_depth=24)
    data = read_audio(target)
    assert data.bit_depth in (16, 24, 32)
    assert data.samples.size > 0


def test_duration_helper(tmp_path: Path) -> None:
    target = tmp_path / "tone.wav"
    write_wav(target, _tone(0.5), 22050)
    assert abs(audio_duration(target) - 0.5) < 0.05


def test_converter_never_overwrites_by_default(tmp_path: Path) -> None:
    converter = AudioConverter()
    target = tmp_path / "out.wav"
    converter.save(_tone(), 22050, target, format="wav")
    first = target.stat().st_size
    again = converter.save(np.zeros(100, dtype=np.float32), 22050, target, format="wav")
    assert again.path != target
    assert again.path.exists()
    assert target.stat().st_size == first


def test_converter_overwrite_is_explicit(tmp_path: Path) -> None:
    converter = AudioConverter()
    target = tmp_path / "out.wav"
    result = converter.save(_tone(), 22050, target, format="wav")
    first = result.path.stat().st_size
    other = converter.save(_tone(0.01), 22050, target, format="wav", overwrite=True)
    assert other.path == target
    assert target.stat().st_size != first


def test_converter_writes_flac_when_libsndfile_is_present(tmp_path: Path) -> None:
    pytest.importorskip("soundfile")
    converter = AudioConverter()
    target = tmp_path / "out.flac"
    result = converter.save(_tone(), 22050, target, format="flac")
    assert result.path.exists() or target.exists()


def test_unique_path_picks_a_free_name(tmp_path: Path) -> None:
    target = tmp_path / "clip.wav"
    target.write_bytes(b"x")
    alternative = unique_path(target)
    assert alternative != target
    assert alternative.name.startswith("clip")
