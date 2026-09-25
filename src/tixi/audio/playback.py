"""Audio playback with position tracking, pause/resume, seeking and looping.

Playback uses ``sounddevice.OutputStream`` and a small mixer thread, which keeps
it independent from the GUI thread and lets the Speech-to-Text view sync the
transcript with the audio cursor.  When PortAudio is unavailable the player
falls back to a "silent" mode so previews still report progress (useful in
tests and on machines without a sound card).
"""

from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..app.logging_config import get_logger
from .devices import AudioBackendUnavailable, backend, describe_device, resolve_output_device
from .dsp import to_float32, to_mono

log = get_logger("tixi.audio.playback")

PositionCallback = Callable[[float, float], None]  # (position_s, duration_s)
StateCallback = Callable[[str], None]


@dataclass
class PlaybackOptions:
    device: str | int | None = None
    volume: float = 1.0
    block_ms: int = 40
    loop: bool = False
    start_position_s: float = 0.0
    trim_leading_silence: bool = False


@dataclass
class PlaybackState:
    playing: bool = False
    position_s: float = 0.0
    duration_s: float = 0.0
    error: str = ""
    device: str = ""

    @property
    def finished(self) -> bool:
        return self.duration_s > 0 and self.position_s >= self.duration_s - 0.01


class AudioPlayer:
    """Reusable single-stream player (one audio at a time per instance)."""

    def __init__(self, options: PlaybackOptions | None = None) -> None:
        self.options = options or PlaybackOptions()
        self._samples: np.ndarray | None = None
        self._sample_rate = 24000
        self._position = 0
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._lock = threading.RLock()
        self._volume = float(self.options.volume)
        self._on_position: PositionCallback | None = None
        self._on_state: StateCallback | None = None
        self._error = ""
        self._device_name = ""
        self._stream = None
        self._finished_event = threading.Event()

    # -- callbacks ----------------------------------------------------------
    def set_position_callback(self, callback: PositionCallback | None) -> None:
        self._on_position = callback

    def set_state_callback(self, callback: StateCallback | None) -> None:
        self._on_state = callback

    # -- properties ---------------------------------------------------------
    @property
    def is_playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._pause_event.is_set()

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set() and self._thread is not None and self._thread.is_alive()

    @property
    def position_s(self) -> float:
        return self._position / self._sample_rate if self._sample_rate else 0.0

    @property
    def duration_s(self) -> float:
        if self._samples is None or not self._sample_rate:
            return 0.0
        return self._samples.shape[0] / self._sample_rate

    @property
    def error(self) -> str:
        return self._error

    def state(self) -> PlaybackState:
        return PlaybackState(
            playing=self.is_playing,
            position_s=self.position_s,
            duration_s=self.duration_s,
            error=self._error,
            device=self._device_name,
        )

    # -- loading ------------------------------------------------------------
    def load(self, samples: np.ndarray, sample_rate: int) -> None:
        """Load an in-memory buffer (float32, mono or stereo)."""
        self.stop()
        array = to_float32(np.asarray(samples))
        if array.ndim > 1 and array.shape[1] > 2:
            array = array[:, :2]
        with self._lock:
            self._samples = array
            self._sample_rate = int(sample_rate) or 24000
            self._position = min(
                int(self.options.start_position_s * self._sample_rate),
                max(0, array.shape[0] - 1),
            )
            self._error = ""

    def load_file(self, path: str | Path) -> None:
        from .wav_io import read_audio  # noqa: PLC0415

        audio = read_audio(path)
        self.load(audio.samples, audio.sample_rate)
        self._source = str(path)

    def clear(self) -> None:
        self.stop()
        with self._lock:
            self._samples = None
            self._position = 0

    # -- transport ----------------------------------------------------------
    def play(self, *, restart: bool = False) -> bool:
        """Start (or resume) playback.  Returns ``False`` when there is nothing to play."""
        with self._lock:
            if self._samples is None or self._samples.size == 0:
                self._error = "There is no audio loaded to play."
                return False
            if restart or self.state().finished:
                self._position = 0
            if self._thread is not None and self._thread.is_alive():
                self._pause_event.clear()
                self._notify_state("playing")
                return True

            self._stop_event.clear()
            self._pause_event.clear()
            self._finished_event.clear()
            self._thread = threading.Thread(target=self._run, name="tixi-playback", daemon=True)
            self._thread.start()
        self._notify_state("playing")
        return True

    def pause(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._pause_event.set()
            self._notify_state("paused")

    def toggle(self) -> None:
        if self.is_paused:
            self.play()
        elif self.is_playing:
            self.pause()

    def stop(self, *, wait: bool = False) -> None:
        self._stop_event.set()
        self._pause_event.clear()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0 if wait else 0.5)
        self._thread = None
        self._close_stream()
        self._notify_state("stopped")

    def seek(self, position_s: float) -> None:
        with self._lock:
            if self._samples is None:
                return
            self._position = int(max(0, min(position_s, self.duration_s)) * self._sample_rate)
        self._notify_position()

    def seek_relative(self, delta_s: float) -> None:
        self.seek(self.position_s + delta_s)

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(2.0, float(volume)))

    def wait_until_finished(self, timeout: float | None = None) -> bool:
        return self._finished_event.wait(timeout)

    # -- internals ----------------------------------------------------------
    def _run(self) -> None:
        try:
            sd = backend()
        except AudioBackendUnavailable as exc:
            self._error = str(exc)
            self._simulate_playback()
            return

        device_index = resolve_output_device(self.options.device)
        self._device_name = describe_device(device_index)
        block = max(1, int(self._sample_rate * self.options.block_ms / 1000))
        try:
            self._stream = sd.OutputStream(
                device=device_index,
                samplerate=self._sample_rate,
                channels=2 if (self._samples is not None and self._samples.ndim > 1) else 1,
                dtype="float32",
                blocksize=block,
                latency="low",
            )
            self._stream.start()
        except Exception as exc:  # noqa: BLE001
            self._error = f"Could not open the output device: {exc}"
            log.warning("playback device failed", extra={"event": "playback_device_error", "error": str(exc)})
            self._simulate_playback()
            return

        try:
            while not self._stop_event.is_set():
                if self._pause_event.is_set():
                    time.sleep(0.02)
                    continue
                with self._lock:
                    if self._samples is None:
                        break
                    end = min(self._position + block, self._samples.shape[0])
                    chunk = self._samples[self._position : end]
                    self._position = end
                    done = end >= self._samples.shape[0]
                if chunk.shape[0] == 0:
                    break
                if chunk.shape[0] < block:
                    padding = np.zeros((block - chunk.shape[0],) + chunk.shape[1:], dtype=chunk.dtype)
                    chunk = np.concatenate([chunk, padding])
                output = chunk * self._volume
                self._stream.write(output.astype(np.float32))
                self._notify_position()
                if done:
                    if self.options.loop:
                        with self._lock:
                            self._position = 0
                    else:
                        break
        except Exception as exc:  # noqa: BLE001
            self._error = f"Playback failed: {exc}"
            log.warning("playback failed", extra={"event": "playback_error", "error": str(exc)})
        finally:
            self._close_stream()
            self._notify_position()
            self._notify_state("finished")
            self._finished_event.set()

    def _simulate_playback(self) -> None:
        """Walk the cursor without a device so the UI still shows progress."""
        while not self._stop_event.is_set():
            if self._pause_event.is_set():
                time.sleep(0.02)
                continue
            with self._lock:
                if self._samples is None:
                    break
                step = int(self._sample_rate * 0.04)
                self._position = min(self._position + step, self._samples.shape[0])
                done = self._position >= self._samples.shape[0]
            self._notify_position()
            time.sleep(0.04)
            if done:
                break
        self._notify_state("finished")
        self._finished_event.set()

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        with contextlib.suppress(Exception):
            stream.stop()
        with contextlib.suppress(Exception):
            stream.close()

    def _notify_position(self) -> None:
        if self._on_position:
            with contextlib.suppress(Exception):
                self._on_position(self.position_s, self.duration_s)

    def _notify_state(self, state: str) -> None:
        if self._on_state:
            with contextlib.suppress(Exception):
                self._on_state(state)


@dataclass
class PlaybackQueue:
    """A tiny playlist used by the Audio Library for "play all"."""

    player: AudioPlayer = field(default_factory=AudioPlayer)
    paths: list[Path] = field(default_factory=list)
    index: int = 0
    _advance_callback: Callable[[Path], None] | None = None

    def set_items(self, paths: list[Path]) -> None:
        self.paths = list(paths)
        self.index = 0

    def play_current(self) -> Path | None:
        if not self.paths:
            return None
        path = self.paths[self.index]
        try:
            self.player.load_file(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("queue item failed to load", extra={"event": "queue_load_failed", "error": str(exc)})
            return None
        self.player.play(restart=True)
        if self._advance_callback:
            self._advance_callback(path)
        return path

    def next(self) -> Path | None:
        if not self.paths:
            return None
        self.index = (self.index + 1) % len(self.paths)
        return self.play_current()

    def previous(self) -> Path | None:
        if not self.paths:
            return None
        self.index = (self.index - 1) % len(self.paths)
        return self.play_current()
