"""DSP helpers implemented with NumPy only (no SciPy dependency).

Everything here works on ``float32`` mono/stereo buffers in the [-1, 1] range and
is written so that it can be unit-tested without an audio device.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

FloatArray = np.ndarray
SAMPLE_DTYPES = {16: np.int16, 32: np.int32}


# ---------------------------------------------------------------------------
# Level metering
# ---------------------------------------------------------------------------
def peak_level(samples: FloatArray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.max(np.abs(samples)))


def rms_level(samples: FloatArray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def db_from_amplitude(amplitude: float) -> float:
    if amplitude <= 1e-9:
        return -120.0
    return float(20.0 * math.log10(amplitude))


def amplitude_from_db(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def level_to_meter(amplitude: float, *, floor_db: float = -60.0) -> float:
    """Map an amplitude to 0..1 for a VU meter (logarithmic)."""
    db = db_from_amplitude(amplitude)
    clamped = max(floor_db, min(0.0, db))
    return (clamped - floor_db) / abs(floor_db)


# ---------------------------------------------------------------------------
# Format conversions
# ---------------------------------------------------------------------------
def to_float32(samples: FloatArray) -> FloatArray:
    """Convert any common PCM integer representation to float32 in [-1, 1]."""
    array = np.asarray(samples)
    if array.dtype == np.float32:
        return array
    if array.dtype == np.float64:
        return array.astype(np.float32)
    if np.issubdtype(array.dtype, np.integer):
        info = np.iinfo(array.dtype)
        scale = float(max(abs(info.min), info.max))
        return (array.astype(np.float32) / scale).astype(np.float32)
    return array.astype(np.float32)


def to_int16(samples: FloatArray) -> FloatArray:
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16)


def to_pcm(samples: FloatArray, bit_depth: int = 16) -> FloatArray:
    if bit_depth == 16:
        return to_int16(samples)
    if bit_depth == 24:
        clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
        ints = (clipped * 8388607.0).astype(np.int32)
        return ints  # written as 24-bit by the WAV/FLAC writers
    if bit_depth == 32:
        clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
        return (clipped * 2147483647.0).astype(np.int32)
    raise ValueError(f"unsupported bit depth: {bit_depth}")


def to_mono(samples: FloatArray) -> FloatArray:
    if samples.ndim == 1:
        return samples
    if samples.shape[1] == 1:
        return samples[:, 0]
    return samples.mean(axis=1).astype(np.float32)


def to_stereo(samples: FloatArray) -> FloatArray:
    if samples.ndim == 2:
        return samples
    return np.stack([samples, samples], axis=1)


def channel_count(samples: FloatArray) -> int:
    return 1 if samples.ndim == 1 else int(samples.shape[1])


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------
def resample(
    samples: FloatArray,
    source_rate: int,
    target_rate: int,
    *,
    quality: Literal["fast", "balanced", "best"] = "balanced",
) -> FloatArray:
    """Resample audio with polyphase/linear interpolation.

    A windowed-sinc interpolator is used for the "best" setting (downmix-safe),
    linear interpolation for "fast".
    """
    if source_rate == target_rate or samples.size == 0:
        return np.asarray(samples, dtype=np.float32)
    source_rate = int(source_rate)
    target_rate = int(target_rate)
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")

    mono = to_mono(samples) if samples.ndim > 1 else samples
    ratio = target_rate / source_rate
    out_length = max(1, int(round(mono.shape[0] * ratio)))

    original_dtype_2d = samples.ndim > 1
    channels = samples.shape[1] if original_dtype_2d else 1
    working = samples if original_dtype_2d else mono.reshape(-1, 1)

    if quality == "fast" or out_length < 64:
        positions = np.linspace(0, working.shape[0] - 1, out_length, dtype=np.float64)
        left = np.floor(positions).astype(np.int64)
        right = np.minimum(left + 1, working.shape[0] - 1)
        weight = (positions - left).astype(np.float32).reshape(-1, 1)
        result = working[left] * (1.0 - weight) + working[right] * weight
    else:
        taps = 16 if quality == "best" else 8
        result = _sinc_resample(working, ratio, out_length, taps)

    result = result.astype(np.float32)
    if not original_dtype_2d and channels == 1:
        return result[:, 0]
    return result


def _sinc_resample(working: FloatArray, ratio: float, out_length: int, taps: int) -> FloatArray:
    """Windowed-sinc interpolation (Kaiser-windowed, tap count configurable)."""
    half = taps
    step = 1.0 / ratio
    cutoff = min(1.0, ratio)
    indices = np.arange(-half, half + 1, dtype=np.float64)
    window = np.kaiser(indices.size, 8.6)
    kernel = np.sinc(indices * cutoff) * cutoff * window
    kernel /= kernel.sum()

    positions = np.arange(out_length, dtype=np.float64) * step
    centres = np.floor(positions).astype(np.int64)
    offsets = positions - centres
    output = np.zeros((out_length, working.shape[1]), dtype=np.float32)
    length = working.shape[0]
    for tap in range(-half, half + 1):
        source_index = np.clip(centres + tap, 0, length - 1)
        weight = np.interp(offsets - tap, np.arange(indices.size, dtype=np.float64), kernel)
        output += working[source_index] * weight.astype(np.float32).reshape(-1, 1)
    return output


# ---------------------------------------------------------------------------
# Gain / dynamics
# ---------------------------------------------------------------------------
def apply_gain(samples: FloatArray, gain: float) -> FloatArray:
    if gain == 1.0:
        return samples
    return np.clip(np.asarray(samples, dtype=np.float32) * float(gain), -1.0, 1.0)


def normalise(samples: FloatArray, *, target_db: float = -1.0, peak_limit: float = 0.999) -> FloatArray:
    """Peak-normalise to ``target_db`` (no-op for silence)."""
    peak = peak_level(samples)
    if peak <= 1e-6:
        return samples
    target = min(amplitude_from_db(target_db), peak_limit)
    return (np.asarray(samples, dtype=np.float32) * (target / peak)).astype(np.float32)


def fade(samples: FloatArray, sample_rate: int, *, fade_in_ms: int = 0, fade_out_ms: int = 0) -> FloatArray:
    array = np.array(samples, dtype=np.float32, copy=True)
    length = array.shape[0]
    if fade_in_ms > 0:
        count = min(length, int(sample_rate * fade_in_ms / 1000))
        if count > 0:
            ramp = np.linspace(0.0, 1.0, count, dtype=np.float32)
            if array.ndim > 1:
                ramp = ramp.reshape(-1, 1)
            array[:count] *= ramp
    if fade_out_ms > 0:
        count = min(length, int(sample_rate * fade_out_ms / 1000))
        if count > 0:
            ramp = np.linspace(1.0, 0.0, count, dtype=np.float32)
            if array.ndim > 1:
                ramp = ramp.reshape(-1, 1)
            array[-count:] *= ramp
    return array


def trim_silence(
    samples: FloatArray,
    sample_rate: int,
    *,
    threshold_db: float = -45.0,
    padding_ms: int = 80,
    max_trim_ms: int = 2000,
) -> FloatArray:
    """Trim leading/trailing silence — useful for dictation clips."""
    if samples.size == 0:
        return samples
    mono = to_mono(samples) if samples.ndim > 1 else samples
    threshold = amplitude_from_db(threshold_db)
    frame = max(1, int(sample_rate * 0.02))
    if mono.shape[0] < frame * 2:
        return samples
    frames = mono[: mono.shape[0] // frame * frame].reshape(-1, frame)
    loud = np.max(np.abs(frames), axis=1) > threshold
    if not loud.any():
        return samples

    padding = int(sample_rate * padding_ms / 1000)
    max_trim = int(sample_rate * max_trim_ms / 1000)
    first = int(np.argmax(loud)) * frame
    last = (len(loud) - int(np.argmax(loud[::-1]))) * frame
    start = max(0, first - padding)
    end = min(samples.shape[0], last + padding)
    if end - start < sample_rate * 0.05:
        return samples
    if first > max_trim:
        start = max(0, start)
    return samples[start:end]


def noise_gate(
    samples: FloatArray, sample_rate: int, *, threshold_db: float = -45.0, attack_ms: int = 5, release_ms: int = 120
) -> FloatArray:
    """Simple downward expander to remove background hiss between words."""
    if samples.size == 0:
        return samples
    mono = to_mono(samples) if samples.ndim > 1 else samples
    threshold = amplitude_from_db(threshold_db)
    frame = max(1, int(sample_rate * 0.01))
    envelope = _frame_envelope(mono, frame)
    gain = np.ones_like(envelope)
    attack = max(1e-3, attack_ms / 1000.0)
    release = max(1e-3, release_ms / 1000.0)
    frame_seconds = frame / sample_rate
    attack_coeff = math.exp(-frame_seconds / attack)
    release_coeff = math.exp(-frame_seconds / release)
    current = 1.0
    for index, value in enumerate(envelope):
        target = 0.0 if value < threshold else 1.0
        coeff = attack_coeff if target > current else release_coeff
        current = target + (current - target) * coeff
        gain[index] = current
    interpolated = np.repeat(gain, frame)[: mono.shape[0]]
    if samples.ndim > 1:
        interpolated = interpolated.reshape(-1, 1)
    return (samples * interpolated).astype(np.float32)


def _frame_envelope(mono: FloatArray, frame: int) -> FloatArray:
    count = mono.shape[0] // frame
    if count == 0:
        return np.abs(mono)
    return np.max(np.abs(mono[: count * frame].reshape(count, frame)), axis=1)


def spectral_denoise(
    samples: FloatArray,
    sample_rate: int,
    *,
    noise_seconds: float = 0.35,
    strength: float = 0.65,
    frame_size: int = 1024,
) -> FloatArray:
    """Spectral-subtraction noise reduction using the leading noise estimate.

    This is a small, dependency-free implementation: estimate the noise
    magnitude spectrum from the first ``noise_seconds`` of audio (assumed to be
    room tone), subtract it with an over-subtraction factor and smooth the gain
    across frames to avoid musical noise.
    """
    if samples.size == 0 or frame_size <= 0:
        return samples
    mono = to_mono(samples) if samples.ndim > 1 else samples
    hop = frame_size // 2
    window = np.hanning(frame_size).astype(np.float32)
    noise_frames = max(1, int(noise_seconds * sample_rate / hop))
    frames = [
        mono[start : start + frame_size]
        for start in range(0, max(1, mono.shape[0] - frame_size), hop)
    ]
    if not frames:
        return samples
    padded = [np.pad(frame, (0, frame_size - frame.shape[0])) for frame in frames]
    spectra = np.fft.rfft(np.stack(padded) * window, axis=1)
    magnitude = np.abs(spectra)
    phase = np.angle(spectra)
    noise_profile = np.median(magnitude[: min(noise_frames, magnitude.shape[0])], axis=0)
    cleaned_magnitude = np.maximum(magnitude - strength * noise_profile, 0.0)

    # Smooth the gain across frequency and time to reduce artefacts.
    gain = np.divide(
        cleaned_magnitude,
        magnitude + 1e-8,
        out=np.zeros_like(cleaned_magnitude),
        where=magnitude > 1e-8,
    )
    kernel = np.array([0.25, 0.5, 0.25], dtype=np.float32)
    if gain.shape[1] > 3:
        gain = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="same"), 1, gain)
    if gain.shape[0] > 3:
        gain = np.apply_along_axis(lambda col: np.convolve(col, kernel, mode="same"), 0, gain)
    gain = np.clip(gain, 0.0, 1.0)

    output = np.zeros(mono.shape[0] + frame_size, dtype=np.float32)
    for index in range(len(frames)):
        frame_spectrum = (gain[index] * magnitude[index]) * np.exp(1j * phase[index])
        restored = np.fft.irfft(frame_spectrum, n=frame_size).astype(np.float32) * window
        start = index * hop
        output[start : start + frame_size] += restored
    result = output[: mono.shape[0]]
    peak = peak_level(result)
    if peak > 0.99:
        result = result * (0.99 / peak)
    if samples.ndim > 1:
        return np.stack([result] * samples.shape[1], axis=1).astype(np.float32)
    return result.astype(np.float32)


def highpass(samples: FloatArray, sample_rate: int, cutoff_hz: float = 80.0) -> FloatArray:
    """One-pole high-pass filter that removes rumble/DC offset."""
    if samples.size == 0:
        return samples
    alpha = 1.0 / (1.0 + (2.0 * math.pi * cutoff_hz / sample_rate))
    working = samples if samples.ndim > 1 else samples.reshape(-1, 1)
    output = np.empty_like(working)
    previous_input = np.zeros(working.shape[1], dtype=np.float32)
    previous_output = np.zeros(working.shape[1], dtype=np.float32)
    for index in range(working.shape[0]):
        current = working[index]
        filtered = alpha * (previous_output + current - previous_input)
        output[index] = filtered
        previous_input = current
        previous_output = filtered
    return output if samples.ndim > 1 else output[:, 0]


def concat_with_pauses(
    pieces: list[FloatArray], sample_rate: int, pauses_ms: list[int]
) -> FloatArray:
    """Join rendered chunks with silence between them."""
    if not pieces:
        return np.zeros(0, dtype=np.float32)
    buffers: list[FloatArray] = []
    for index, piece in enumerate(pieces):
        buffers.append(to_mono(piece) if piece.ndim > 1 else piece)
        if index < len(pauses_ms):
            pause = max(0, int(sample_rate * pauses_ms[index] / 1000))
            if pause:
                buffers.append(np.zeros(pause, dtype=np.float32))
    return np.concatenate(buffers).astype(np.float32)


@dataclass
class AudioStats:
    duration_s: float
    peak: float
    rms: float
    peak_db: float
    rms_db: float
    silence_ratio: float
    clipping_ratio: float

    def as_dict(self) -> dict[str, float]:
        return {
            "duration_s": self.duration_s,
            "peak": self.peak,
            "rms": self.rms,
            "peak_db": self.peak_db,
            "rms_db": self.rms_db,
            "silence_ratio": self.silence_ratio,
            "clipping_ratio": self.clipping_ratio,
        }


def analyse(samples: FloatArray, sample_rate: int, *, silence_db: float = -50.0) -> AudioStats:
    mono = to_mono(samples) if getattr(samples, "ndim", 1) > 1 else samples
    duration = mono.shape[0] / sample_rate if sample_rate else 0.0
    peak = peak_level(mono)
    rms = rms_level(mono)
    silence_threshold = amplitude_from_db(silence_db)
    frame = max(1, int(sample_rate * 0.02)) if sample_rate else 1
    if mono.shape[0] >= frame:
        frames = mono[: mono.shape[0] // frame * frame].reshape(-1, frame)
        quiet = np.max(np.abs(frames), axis=1) < silence_threshold
        silence_ratio = float(np.mean(quiet)) if quiet.size else 0.0
    else:
        silence_ratio = 0.0
    clipping_ratio = float(np.mean(np.abs(mono) > 0.995)) if mono.size else 0.0
    return AudioStats(
        duration_s=duration,
        peak=peak,
        rms=rms,
        peak_db=db_from_amplitude(peak),
        rms_db=db_from_amplitude(rms),
        silence_ratio=silence_ratio,
        clipping_ratio=clipping_ratio,
    )


def concatenate(buffers: list[FloatArray]) -> FloatArray:
    if not buffers:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate([to_mono(buffer) for buffer in buffers]).astype(np.float32)


# ---------------------------------------------------------------------------
# Time stretching and pitch shifting (post-processing)
# ---------------------------------------------------------------------------
def time_stretch(samples: FloatArray, factor: float, *, frame_size: int = 1024) -> FloatArray:
    """WSOLA-style time stretching without changing pitch.

    ``factor > 1`` makes the audio longer (slower), ``factor < 1`` shorter.
    Overlap-add with cross-correlation search keeps the result free of the
    "flanging" artefacts a naive resample would produce.  Used to keep duration
    correct when the user asks for a pitch change, which Piper cannot do
    natively.
    """
    if factor <= 0 or abs(factor - 1.0) < 1e-3 or samples.size == 0:
        return samples
    mono = samples if samples.ndim == 1 else to_mono(samples)
    window_size = max(256, int(frame_size))
    hop_analysis = window_size // 2
    hop_synthesis = max(1, int(hop_analysis * factor))
    window = np.hanning(window_size).astype(np.float32)

    output = np.zeros(int(mono.shape[0] * factor) + window_size, dtype=np.float32)
    norm = np.zeros_like(output)

    analysis = 0
    synthesis = 0
    search = max(1, hop_analysis // 8)
    while analysis + window_size < mono.shape[0]:
        frame = mono[analysis : analysis + window_size]
        best_offset = 0
        if synthesis > 0 and search > 0:
            # Align the new frame with the tail of the previous one.
            reference = output[synthesis : synthesis + search]
            best_score = None
            for offset in range(-search, search + 1):
                start = analysis + offset
                if start < 0 or start + search >= mono.shape[0]:
                    continue
                candidate = mono[start : start + search]
                score = float(np.dot(reference, candidate))
                if best_score is None or score > best_score:
                    best_score = score
                    best_offset = offset
        start = max(0, analysis + best_offset)
        frame = mono[start : start + window_size]
        if frame.shape[0] < window_size:
            break
        output[synthesis : synthesis + window_size] += frame * window
        norm[synthesis : synthesis + window_size] += window
        analysis += hop_analysis
        synthesis += hop_synthesis

    valid = norm > 1e-6
    output[valid] /= norm[valid]
    trimmed = output[:synthesis] if synthesis > 0 else output
    if samples.ndim > 1:
        return np.stack([trimmed] * samples.shape[1], axis=1).astype(np.float32)
    return trimmed.astype(np.float32)


def pitch_shift(samples: FloatArray, sample_rate: int, semitones: float = 0.0) -> FloatArray:
    """Shift the pitch by ``semitones`` while preserving duration."""
    if abs(semitones) < 0.01 or samples.size == 0:
        return samples
    ratio = 2.0 ** (semitones / 12.0)
    stretched = time_stretch(samples, ratio)
    shifted = resample(stretched, sample_rate, int(round(sample_rate * ratio)))
    target_length = samples.shape[0]
    if shifted.shape[0] > target_length:
        return shifted[:target_length].astype(np.float32)
    if shifted.shape[0] < target_length:
        pad_shape = (target_length - shifted.shape[0],) + shifted.shape[1:]
        return np.concatenate([shifted, np.zeros(pad_shape, dtype=np.float32)]).astype(np.float32)
    return shifted.astype(np.float32)


def pitch_factor_to_semitones(factor: float) -> float:
    """Convert a 0.5..1.5 pitch multiplier to semitones."""
    if factor <= 0:
        return 0.0
    import math as _math

    return 12.0 * _math.log2(max(0.05, factor))


def apply_speed_change(samples: FloatArray, sample_rate: int, speed: float) -> FloatArray:
    """Change speaking rate while keeping the pitch (post-processing fallback)."""
    if speed <= 0 or abs(speed - 1.0) < 1e-3:
        return samples
    stretched = time_stretch(samples, 1.0 / speed)
    return resample(stretched, sample_rate, int(round(sample_rate)))
