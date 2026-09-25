"""DSP helpers used by recording, export and playback."""

from __future__ import annotations

import numpy as np

from tixi.audio.dsp import (
    analyse,
    apply_gain,
    concat_with_pauses,
    normalise,
    peak_level,
    resample,
    to_mono,
    trim_silence,
)


def _tone(seconds: float = 0.5, rate: int = 16000, frequency: float = 440.0, amplitude: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * rate), dtype=np.float32) / rate
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def test_peak_level_and_gain() -> None:
    tone = _tone(amplitude=0.25)
    assert abs(peak_level(tone) - 0.25) < 1e-3
    louder = apply_gain(tone, 6.0)
    assert peak_level(louder) > peak_level(tone)


def test_normalise_lifts_quiet_audio_and_keeps_silence() -> None:
    tone = _tone(amplitude=0.1)
    normalised = normalise(tone, target_db=-1.0)
    assert 0.8 < peak_level(normalised) <= 1.0
    silence = np.zeros(1000, dtype=np.float32)
    assert peak_level(normalise(silence)) == 0.0


def test_resample_keeps_duration() -> None:
    tone = _tone(seconds=1.0, rate=48000)
    down = resample(tone, 48000, 16000)
    assert abs(down.shape[0] - 16000) < 200


def test_to_mono_averages_channels() -> None:
    stereo = np.stack([_tone(), _tone(amplitude=0.0)], axis=1)
    mono = to_mono(stereo)
    assert mono.ndim == 1
    assert mono.shape[0] == stereo.shape[0]


def test_trim_silence_removes_padding() -> None:
    padded = np.concatenate([np.zeros(4000, dtype=np.float32), _tone(0.3), np.zeros(4000, dtype=np.float32)])
    trimmed = trim_silence(padded, 16000, threshold_db=-45.0)
    assert trimmed.shape[0] < padded.shape[0]


def test_concat_with_pauses_inserts_silence() -> None:
    joined = concat_with_pauses([_tone(0.1), _tone(0.1)], 16000, [100, 0])
    assert joined.shape[0] > 3200


def test_analyse_reports_duration_and_clipping() -> None:
    stats = analyse(_tone(1.0), 16000)
    assert abs(stats.duration_s - 1.0) < 0.05
    clipped = np.ones(16000, dtype=np.float32)
    assert analyse(clipped, 16000).clipping_ratio > 0.5
