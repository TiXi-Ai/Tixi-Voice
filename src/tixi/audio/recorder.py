"""Microphone recording with level metering, pause/resume and safe stopping.

The recorder runs PortAudio's callback in its own thread and hands finished
buffers to the caller.  It is deliberately free of Qt imports so that voice
typing (which must keep working while the GUI is busy) can use it directly, and
so it can be unit-tested with a fake stream.

Design notes
------------
* Frames are accumulated in a list and joined once at the end: no per-frame
  copies, no locks held while the callback runs.
* ``pause()`` keeps the stream open (fast resume) but discards incoming frames.
* ``max_duration_s`` protects the user from a stuck hotkey filling the disk.
* Errors from PortAudio are translated into actionable messages.
"""

from __future__ import annotations

import contextlib
import enum
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..app.logging_config import get_logger
from .devices import AudioBackendUnavailable, backend, describe_device, microphone_permission_hint, resolve_input_device
from .dsp import apply_gain, level_to_meter, noise_gate, peak_level, rms_level

log = get_logger("tixi.audio.recorder")

LevelCallback = Callable[[float, float], None]  # (level 0..1, elapsed seconds)


class RecorderState(str, enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class RecorderOptions:
    device: str | int | None = None
    sample_rate: int = 48000
    channels: int = 1
    block_ms: int = 20
    gain: float = 1.0
    max_duration_s: int = 120
    min_duration_s: float = 0.25
    noise_gate_db: float | None = None
    dtype: str = "float32"


@dataclass
class RecordingResult:
    samples: np.ndarray
    sample_rate: int
    channels: int
    duration_s: float
    peak: float
    clipped: bool = False
    device_name: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "duration_s": round(self.duration_s, 3),
            "peak": round(self.peak, 4),
            "clipped": self.clipped,
            "device": self.device_name,
            "warnings": self.warnings,
        }


class RecordingTooShortError(RuntimeError):
    """Raised when a dictation clip is shorter than the configured minimum."""


class AudioRecorder:
    """Blocking-safe recorder built on ``sounddevice.InputStream``."""

    def __init__(self, options: RecorderOptions | None = None) -> None:
        self.options = options or RecorderOptions()
        self._stream = None
        self._frames: list[np.ndarray] = []
        self._lock = threading.RLock()
        self._state = RecorderState.IDLE
        self._started_at = 0.0
        self._paused_at = 0.0
        self._paused_total = 0.0
        self._level = 0.0
        self._error: str = ""
        self._on_level: LevelCallback | None = None
        self._clipped = False

    # -- state --------------------------------------------------------------
    @property
    def state(self) -> RecorderState:
        return self._state

    @property
    def is_recording(self) -> bool:
        return self._state in (RecorderState.RECORDING, RecorderState.PAUSED)

    @property
    def error(self) -> str:
        return self._error

    def elapsed_s(self) -> float:
        with self._lock:
            if not self._started_at:
                return 0.0
            now = self._paused_at if self._state is RecorderState.PAUSED else time.monotonic()
            return max(0.0, now - self._started_at - self._paused_total)

    def level(self) -> float:
        return self._level

    def frame_count(self) -> int:
        with self._lock:
            return sum(frame.shape[0] for frame in self._frames)

    def set_level_callback(self, callback: LevelCallback | None) -> None:
        self._on_level = callback

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        """Open the input stream and begin capturing."""
        with self._lock:
            if self._state in (RecorderState.RECORDING, RecorderState.PAUSED):
                return
            self._frames = []
            self._level = 0.0
            self._error = ""
            self._clipped = False
            self._paused_total = 0.0
            self._paused_at = 0.0
            try:
                sd = backend()
            except AudioBackendUnavailable as exc:
                self._fail(str(exc))
                raise

            device_index = resolve_input_device(self.options.device)
            block = max(1, int(self.options.sample_rate * self.options.block_ms / 1000))
            try:
                self._stream = sd.InputStream(
                    device=device_index,
                    samplerate=self.options.sample_rate,
                    channels=self.options.channels,
                    dtype=self.options.dtype,
                    blocksize=block,
                    callback=self._callback,
                    latency="low",
                )
                self._stream.start()
            except Exception as exc:  # noqa: BLE001 - PortAudio raises various errors
                self._fail(_friendly_error(exc))
                raise RuntimeError(self._error) from exc

            self._started_at = time.monotonic()
            self._state = RecorderState.RECORDING
            self._device_name = _device_label(device_index)
            log.info(
                "recording started",
                extra={
                    "event": "record_start",
                    "device": self._device_name,
                    "sample_rate": self.options.sample_rate,
                    "channels": self.options.channels,
                },
            )

    def pause(self) -> None:
        with self._lock:
            if self._state is RecorderState.RECORDING:
                self._state = RecorderState.PAUSED
                self._paused_at = time.monotonic()
                log.debug("recording paused", extra={"event": "record_pause"})

    def resume(self) -> None:
        with self._lock:
            if self._state is RecorderState.PAUSED:
                self._paused_total += time.monotonic() - self._paused_at
                self._paused_at = 0.0
                self._state = RecorderState.RECORDING
                log.debug("recording resumed", extra={"event": "record_resume"})

    def stop(self, *, allow_short: bool = False, trim: bool = False) -> RecordingResult:
        """Stop capturing and return the recorded audio."""
        with self._lock:
            if self._state is RecorderState.IDLE and not self._frames:
                raise RuntimeError("Recording has not been started.")
            self._state = RecorderState.STOPPING
            self._close_stream()
            frames, self._frames = self._frames, []
            duration = self.elapsed_s()
            clipped = self._clipped
            device_name = getattr(self, "_device_name", "")
            self._started_at = 0.0
            self._state = RecorderState.IDLE

        if not frames:
            raise RuntimeError(
                "No audio was captured. " + microphone_permission_hint()
            )
        samples = np.concatenate(frames).astype(np.float32)
        if self.options.gain and self.options.gain != 1.0:
            samples = apply_gain(samples, self.options.gain)
        if self.options.noise_gate_db is not None:
            samples = noise_gate(
                samples, self.options.sample_rate, threshold_db=self.options.noise_gate_db
            )
        if trim:
            from .dsp import trim_silence  # noqa: PLC0415

            samples = trim_silence(samples, self.options.sample_rate)

        duration = samples.shape[0] / self.options.sample_rate if self.options.sample_rate else duration
        if not allow_short and duration < self.options.min_duration_s:
            raise RecordingTooShortError(
                f"The recording was only {duration * 1000:.0f} ms long "
                f"(minimum {int(self.options.min_duration_s * 1000)} ms). "
                "Hold the shortcut a little longer."
            )
        result = RecordingResult(
            samples=samples,
            sample_rate=self.options.sample_rate,
            channels=self.options.channels,
            duration_s=duration,
            peak=peak_level(samples),
            clipped=clipped,
            device_name=device_name,
        )
        if result.peak < 0.005:
            result.warnings.append(
                "The recording is almost silent — check the selected microphone and its level."
            )
        if clipped:
            result.warnings.append("The input clipped; lower the microphone level or gain.")
        log.info(
            "recording finished",
            extra={
                "event": "record_stop",
                "seconds": round(duration, 2),
                "peak": round(result.peak, 4),
                "device": device_name,
            },
        )
        return result

    def cancel(self) -> None:
        """Abandon the recording without producing audio."""
        with self._lock:
            self._close_stream()
            self._frames = []
            self._started_at = 0.0
            self._state = RecorderState.IDLE
        log.info("recording cancelled", extra={"event": "record_cancel"})

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._close_stream()
        self._state = RecorderState.IDLE

    # -- internals ----------------------------------------------------------
    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        with contextlib.suppress(Exception):
            stream.stop()
        with contextlib.suppress(Exception):
            stream.close()

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001 - PortAudio signature
        if status:  # overflow / underflow: keep going, but note clipping
            if getattr(status, "input_overflow", False):
                self._clipped = True
        with self._lock:
            if self._state is not RecorderState.RECORDING:
                return
            block = np.array(indata, dtype=np.float32, copy=True)
            self._frames.append(block)
            amplitude = peak_level(block)
            self._level = level_to_meter(amplitude)
            if amplitude >= 0.999:
                self._clipped = True
            if self.options.max_duration_s and self.elapsed_s() >= self.options.max_duration_s:
                # Stop on the audio thread: the caller notices via state.
                self._state = RecorderState.STOPPING
                log.warning(
                    "maximum recording length reached",
                    extra={"event": "record_max_duration", "seconds": self.options.max_duration_s},
                )
        if self._on_level:
            with contextlib.suppress(Exception):
                self._on_level(self._level, self.elapsed_s())

    def _fail(self, message: str) -> None:
        self._error = message
        self._state = RecorderState.ERROR
        self._close_stream()
        log.error("recording failed", extra={"event": "record_error", "reason": message})


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "invalid number of channels" in lowered:
        return (
            f"The selected microphone does not support this channel configuration ({text}). "
            "Try setting the input to mono in Settings ▸ Audio."
        )
    if "invalid sample rate" in lowered:
        return (
            f"The selected microphone does not support this sample rate ({text}). "
            "Try 44100 Hz or 48000 Hz in Settings ▸ Audio."
        )
    if "device unavailable" in lowered or "unanticipated host error" in lowered:
        return (
            "The microphone is busy or unavailable. Another application may be using it "
            "exclusively, or the device was unplugged. " + microphone_permission_hint()
        )
    if "permission" in lowered or "access" in lowered:
        return microphone_permission_hint()
    return f"Could not start recording: {text}"


def _device_label(index: int | None) -> str:
    return describe_device(index)


def record_for(
    seconds: float,
    *,
    options: RecorderOptions | None = None,
    on_level: LevelCallback | None = None,
) -> RecordingResult:
    """Convenience helper used by the Speech-to-Text view and by tests."""
    recorder = AudioRecorder(options)
    if on_level:
        recorder.set_level_callback(on_level)
    recorder.start()
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            time.sleep(0.05)
            if recorder.state is RecorderState.STOPPING:
                break
        return recorder.stop(allow_short=True)
    finally:
        recorder.close()


def save_recording(result: RecordingResult, path: Path, *, bit_depth: int = 16) -> Path:
    """Persist a recording as WAV (atomically)."""
    from .wav_io import write_wav  # noqa: PLC0415

    return write_wav(path, result.samples, result.sample_rate, bit_depth=bit_depth)
