"""Voice-typing state machine.

This is the heart of the feature and is deliberately free of Qt so it can be
driven by the hotkey thread, unit-tested without a GUI, and reused by the CLI
diagnostics.

States::

    DISABLED ──enable──▶ IDLE ──hotkey──▶ RECORDING ──release──▶ TRANSCRIBING
       ▲                  ▲                    │                      │
       └──disable─────────┴────cancel/error────┴──────INSERTING◀──────┘
                                              │
                                        FALLBACK (manual copy)

Invariants that matter for reliability:

* at most one active session; a second press while transcribing is ignored (or
  queues in continuous mode),
* the recording is always released, even when transcription throws,
* the target window handle is captured *before* recording starts, so the text
  goes to the window the user was typing in,
* nothing is inserted when the recording is shorter than the minimum duration or
  the recognised text is empty/hallucinated,
* cancellation is honoured at every step.
"""

from __future__ import annotations

import contextlib
import enum
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..app.logging_config import get_logger
from ..audio.dsp import peak_level
from ..audio.recorder import AudioRecorder, RecorderOptions, RecordingResult, RecordingTooShortError
from ..engines.base import CancelledError, EngineError, TranscriptionRequest, TranscriptionResult
from ..utils.text_build import PostProcessOptions, looks_like_speech, post_process
from .hotkey_manager import HotkeyEvent
from .text_inserter import InsertionMethod, InsertionResult, TextInserter

log = get_logger("tixi.voice.controller")


class VoiceTypingState(str, enum.Enum):
    DISABLED = "disabled"
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    INSERTING = "inserting"
    FALLBACK = "fallback"
    ERROR = "error"

    @property
    def is_busy(self) -> bool:
        return self in (
            VoiceTypingState.RECORDING,
            VoiceTypingState.TRANSCRIBING,
            VoiceTypingState.INSERTING,
        )

    @property
    def label(self) -> str:
        return {
            VoiceTypingState.DISABLED: "Voice typing off",
            VoiceTypingState.IDLE: "Ready — hold the shortcut to dictate",
            VoiceTypingState.RECORDING: "Listening…",
            VoiceTypingState.TRANSCRIBING: "Transcribing…",
            VoiceTypingState.INSERTING: "Inserting text…",
            VoiceTypingState.FALLBACK: "Insertion needs your help",
            VoiceTypingState.ERROR: "Something went wrong",
        }[self]


class VoiceTypingMode(str, enum.Enum):
    PUSH_TO_TALK = "push_to_talk"
    TOGGLE = "toggle"
    PUSH_TO_TALK_PUNCTUATION = "push_to_talk_punctuation"
    CONTINUOUS = "continuous"

    @property
    def holds_shortcut(self) -> bool:
        return self in (
            VoiceTypingMode.PUSH_TO_TALK,
            VoiceTypingMode.PUSH_TO_TALK_PUNCTUATION,
        )

    @property
    def label(self) -> str:
        return {
            VoiceTypingMode.PUSH_TO_TALK: "Push to talk (hold to record)",
            VoiceTypingMode.TOGGLE: "Toggle (press to start, press again to stop)",
            VoiceTypingMode.PUSH_TO_TALK_PUNCTUATION: "Push to talk with automatic punctuation",
            VoiceTypingMode.CONTINUOUS: "Continuous dictation (stops after a pause)",
        }[self]


@dataclass
class VoiceTypingConfig:
    """Runtime configuration snapshot (rebuilt from Settings on every change)."""

    enabled: bool = True
    mode: VoiceTypingMode = VoiceTypingMode.PUSH_TO_TALK
    language: str = "fa"
    model_id: str = ""
    auto_punctuation: bool = True
    auto_capitalization: bool = True
    normalize_persian: bool = True
    diacritize: bool = False
    insert_method: InsertionMethod = InsertionMethod.AUTO
    restore_clipboard: bool = True
    paste_delay_ms: int = 90
    append_space: bool = True
    submit_key: str = "none"
    min_duration_ms: int = 250
    max_duration_s: int = 120
    sound_feedback: bool = True
    blocked_apps: list[str] = field(default_factory=list)
    post_process_rules: list[str] = field(default_factory=list)
    sample_rate: int = 48000
    input_device: str = ""
    mic_gain: float = 1.0
    noise_gate_db: float | None = None
    continuous_silence_s: float = 1.6
    beam_size: int = 5
    vad_filter: bool = True

    @classmethod
    def from_settings(cls, settings: Any) -> VoiceTypingConfig:
        voice = getattr(settings, "voice_typing", settings)
        stt = getattr(settings, "stt", None)
        audio = getattr(settings, "audio", None)
        ai = getattr(settings, "ai", None)
        mode = str(getattr(voice, "mode", "push_to_talk"))
        try:
            mode_enum = VoiceTypingMode(mode)
        except ValueError:
            mode_enum = VoiceTypingMode.PUSH_TO_TALK
        try:
            method = InsertionMethod(str(getattr(voice, "insert_method", "auto")))
        except ValueError:
            method = InsertionMethod.AUTO
        return cls(
            enabled=bool(getattr(voice, "enabled", True)),
            mode=mode_enum,
            language=str(getattr(voice, "language", "fa")),
            model_id=str(getattr(voice, "model_id", "") or getattr(stt, "model_id", "") if stt else ""),
            auto_punctuation=bool(getattr(voice, "auto_punctuation", True)),
            auto_capitalization=bool(getattr(voice, "auto_capitalization", True)),
            normalize_persian=bool(getattr(voice, "normalize_persian", True)),
            diacritize=bool(getattr(voice, "diacritize", False)),
            insert_method=method,
            restore_clipboard=bool(getattr(voice, "restore_clipboard", True)),
            paste_delay_ms=int(getattr(voice, "paste_delay_ms", 90)),
            append_space=bool(getattr(voice, "append_space", True)),
            submit_key=str(getattr(voice, "submit_key", "none")),
            min_duration_ms=int(getattr(voice, "min_duration_ms", 250)),
            max_duration_s=int(getattr(voice, "max_duration_s", 120)),
            sound_feedback=bool(getattr(voice, "sound_feedback", True)),
            blocked_apps=list(getattr(voice, "blocked_apps", []) or []),
            post_process_rules=list(getattr(voice, "post_process", []) or []),
            sample_rate=int(getattr(audio, "input_sample_rate", 48000) or 48000),
            input_device=str(getattr(audio, "input_device", "") or ""),
            mic_gain=float(getattr(audio, "mic_gain", 1.0) or 1.0),
            noise_gate_db=(
                float(getattr(audio, "noise_gate_db", -45.0))
                if str(getattr(audio, "noise_reduction", "off")) == "gate"
                else None
            ),
            beam_size=int(getattr(stt, "beam_size", 5) or 5),
            vad_filter=bool(getattr(stt, "vad_filter", True)),
        )


@dataclass
class DictationResult:
    """One completed dictation attempt."""

    text: str = ""
    raw_text: str = ""
    state: VoiceTypingState = VoiceTypingState.IDLE
    inserted: bool = False
    insertion: InsertionResult | None = None
    transcription: TranscriptionResult | None = None
    duration_s: float = 0.0
    language: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "raw_text": self.raw_text,
            "inserted": self.inserted,
            "duration_s": round(self.duration_s, 2),
            "language": self.language,
            "error": self.error,
            "warnings": list(self.warnings),
            "insertion": self.insertion.as_dict() if self.insertion else None,
        }


StateListener = Callable[[VoiceTypingState, str], None]
ResultListener = Callable[[DictationResult], None]
LevelListener = Callable[[float, float], None]


class VoiceTypingController:
    """Coordinates hotkeys, recording, transcription, processing and insertion."""

    def __init__(
        self,
        *,
        recorder: AudioRecorder | None = None,
        inserter: TextInserter | None = None,
        engine_provider: Callable[[], Any] | None = None,
        pipeline_provider: Callable[[], Any] | None = None,
        diacritizer_provider: Callable[[], Any] | None = None,
    ) -> None:
        self.config = VoiceTypingConfig()
        self.recorder = recorder or AudioRecorder()
        self.inserter = inserter or TextInserter()
        self.engine_provider = engine_provider
        self.pipeline_provider = pipeline_provider
        self.diacritizer_provider = diacritizer_provider

        self._state = VoiceTypingState.DISABLED
        self._lock = threading.RLock()
        self._session_id = 0
        self._target_hwnd = 0
        self._started_at = 0.0
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_activity = time.time()
        self._last_result: DictationResult | None = None
        self._history: list[DictationResult] = []
        self._state_listeners: list[StateListener] = []
        self._result_listeners: list[ResultListener] = []
        self._level_listeners: list[LevelListener] = []
        self._stats = {"sessions": 0, "inserted": 0, "failed": 0, "words": 0}

    # -- listeners ----------------------------------------------------------
    def on_state_change(self, listener: StateListener) -> None:
        if listener not in self._state_listeners:
            self._state_listeners.append(listener)

    def on_result(self, listener: ResultListener) -> None:
        if listener not in self._result_listeners:
            self._result_listeners.append(listener)

    def on_level(self, listener: LevelListener) -> None:
        if listener not in self._level_listeners:
            self._level_listeners.append(listener)

    # -- state --------------------------------------------------------------
    @property
    def state(self) -> VoiceTypingState:
        return self._state

    @property
    def last_result(self) -> DictationResult | None:
        return self._last_result

    @property
    def history(self) -> list[DictationResult]:
        return list(self._history)

    @property
    def statistics(self) -> dict[str, int]:
        return dict(self._stats)

    def elapsed_s(self) -> float:
        if self._state is not VoiceTypingState.RECORDING or not self._started_at:
            return 0.0
        return time.time() - self._started_at

    def _set_state(self, state: VoiceTypingState, message: str = "") -> None:
        with self._lock:
            if self._state is state and not message:
                return
            self._state = state
        for listener in list(self._state_listeners):
            with contextlib.suppress(Exception):
                listener(state, message or state.label)
        log.debug("voice typing state", extra={"event": "vt_state", "state": state.value, "message": message})

    def configure(self, config: VoiceTypingConfig) -> None:
        """Apply new settings; stops an active session when the mode changes."""
        previous_mode = self.config.mode
        self.config = config
        if not config.enabled:
            self.cancel(reason="voice typing was disabled")
            self._set_state(VoiceTypingState.DISABLED)
        elif self._state is VoiceTypingState.DISABLED:
            self._set_state(VoiceTypingState.IDLE)
        elif previous_mode is not config.mode and self._state in (
            VoiceTypingState.RECORDING,
            VoiceTypingState.TRANSCRIBING,
        ):
            self.cancel(reason="the dictation mode changed")
            self._set_state(VoiceTypingState.IDLE)

    # -- hotkey handling ----------------------------------------------------
    def handle_hotkey(self, event: HotkeyEvent) -> None:
        """Entry point for both the real hook and the in-app shortcut."""
        if not self.config.enabled:
            return
        mode = self.config.mode
        if mode.holds_shortcut:
            if event.pressed:
                self.start_session()
            else:
                self.stop_session()
            return
        if not event.pressed:
            return
        if self._state is VoiceTypingState.RECORDING:
            self.stop_session()
        elif self._state in (VoiceTypingState.IDLE, VoiceTypingState.FALLBACK, VoiceTypingState.ERROR):
            self.start_session()
        elif self._state.is_busy:
            log.debug("hotkey ignored while busy", extra={"event": "vt_busy_ignored", "state": self._state.value})

    # -- sessions -----------------------------------------------------------
    def start_session(self, *, target_hwnd: int = 0, reason: str = "hotkey") -> bool:
        with self._lock:
            if self._state is VoiceTypingState.RECORDING:
                return True
            if self._state.is_busy:
                log.info("dictation request ignored: still busy", extra={"event": "vt_busy", "state": self._state.value})
                return False
            if not self.config.enabled:
                return False
            self._session_id += 1
            self._target_hwnd = target_hwnd or self.inserter.foreground_window()
            self._cancel.clear()
            self._started_at = time.time()
            self._last_result = None

        self.recorder.options = RecorderOptions(
            device=self.config.input_device or None,
            sample_rate=self.config.sample_rate,
            channels=1,
            gain=self.config.mic_gain,
            max_duration_s=self.config.max_duration_s,
            min_duration_s=self.config.min_duration_ms / 1000.0,
            noise_gate_db=self.config.noise_gate_db,
        )
        self.recorder.set_level_callback(self._on_level)
        try:
            self.recorder.start()
        except Exception as exc:  # noqa: BLE001
            self._fail(f"Could not start recording: {exc}")
            return False
        self._stats["sessions"] += 1
        self._set_state(VoiceTypingState.RECORDING, f"Listening ({reason})…")
        self._play_feedback(start=True)
        log.info(
            "dictation started",
            extra={
                "event": "vt_start",
                "target": self.inserter.window_title(self._target_hwnd),
                "process": self.inserter.process_name(self._target_hwnd),
                "language": self.config.language,
            },
        )
        return True

    def stop_session(self) -> None:
        """Stop recording and begin transcription — always safe to call."""
        with self._lock:
            if self._state is not VoiceTypingState.RECORDING:
                return
            session = self._session_id
        try:
            recording = self.recorder.stop(trim=self.config.mode is VoiceTypingMode.CONTINUOUS)
        except RecordingTooShortError as exc:
            self._set_state(VoiceTypingState.IDLE, "Recording was too short")
            self._play_feedback(start=False)
            log.info("dictation too short", extra={"event": "vt_too_short", "reason": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            self._fail(f"Recording failed: {exc}")
            return
        self._play_feedback(start=False)
        self._set_state(VoiceTypingState.TRANSCRIBING)
        self._thread = threading.Thread(
            target=self._process,
            args=(session, recording),
            name="tixi-dictation",
            daemon=True,
        )
        self._thread.start()

    def cancel(self, *, reason: str = "user request") -> None:
        """Abort the current session immediately (hotkey, tray or overlay)."""
        self._cancel.set()
        with contextlib.suppress(Exception):
            self.recorder.cancel()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if self.config.enabled:
            self._set_state(VoiceTypingState.IDLE, f"Cancelled ({reason})")
        log.info("dictation cancelled", extra={"event": "vt_cancel", "reason": reason})

    def shutdown(self) -> None:
        self._cancel.set()
        with contextlib.suppress(Exception):
            self.recorder.close()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    # -- processing pipeline ------------------------------------------------
    def _process(self, session: int, recording: RecordingResult) -> None:
        started = time.perf_counter()
        result = DictationResult(duration_s=recording.duration_s, language=self.config.language)
        result.warnings.extend(recording.warnings)
        try:
            if self._cancel.is_set():
                raise CancelledError("cancelled before transcription")

            transcription = self._transcribe(recording.samples, recording.sample_rate)
            if self._cancel.is_set():
                raise CancelledError("cancelled after transcription")
            if transcription is None:
                raise EngineError("No speech-recognition engine is available.")
            result.transcription = transcription
            result.raw_text = transcription.text
            result.language = transcription.language or self.config.language
            result.warnings.extend(transcription.warnings)

            text = self._post_process(transcription.text, result)
            if not looks_like_speech(text):
                result.warnings.append("No speech was recognised (the clip is silent or too noisy).")
                text = ""
            if not text.strip():
                result.text = ""
                result.state = VoiceTypingState.IDLE
                self._finish(result, session)
                return

            result.text = text
            if self._cancel.is_set():
                raise CancelledError("cancelled before insertion")

            self._set_state(VoiceTypingState.INSERTING)
            insertion = self._insert(text)
            result.insertion = insertion
            result.inserted = insertion.success
            result.warnings.extend(insertion.warnings)
            if insertion.success:
                result.state = VoiceTypingState.IDLE
                self._stats["inserted"] += 1
                self._stats["words"] += len(text.split())
            else:
                result.state = VoiceTypingState.FALLBACK
                self._stats["failed"] += 1
        except CancelledError:
            result.state = VoiceTypingState.IDLE
            result.error = ""
            result.warnings.append("Cancelled.")
        except EngineError as exc:
            result.state = VoiceTypingState.ERROR
            result.error = str(exc)
            result.warnings.append(str(exc))
            self._stats["failed"] += 1
        except Exception as exc:  # noqa: BLE001 - never lose the reason
            log.exception("dictation failed", extra={"event": "vt_failed"})
            result.state = VoiceTypingState.ERROR
            result.error = f"{type(exc).__name__}: {exc}"
            self._stats["failed"] += 1
        finally:
            result.warnings = list(dict.fromkeys(result.warnings))
            log.info(
                "dictation finished",
                extra={
                    "event": "vt_finished",
                    "state": result.state.value,
                    "inserted": result.inserted,
                    "seconds": round(time.perf_counter() - started, 2),
                    "audio_seconds": round(recording.duration_s, 2),
                    "characters": len(result.text),
                },
            )
            self._finish(result, session)

    def _transcribe(self, samples: np.ndarray, sample_rate: int) -> TranscriptionResult | None:
        if self.engine_provider is None:
            raise EngineError(
                "Speech recognition is not configured. Choose a model on the AI Models page."
            )
        engine = self.engine_provider()
        if engine is None:
            raise EngineError(
                "No speech-recognition model is installed. Download Whisper Small or Turbo from "
                "AI Models ▸ Speech Recognition."
            )
        request = TranscriptionRequest(
            audio=samples,
            sample_rate=sample_rate,
            language=self.config.language,
            model_id=self.config.model_id,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            word_timestamps=False,
            condition_on_previous_text=False,
            initial_prompt="",
            preprocess={"normalise": True},
        )
        return engine.transcribe(request, cancel=self._cancel)

    def _post_process(self, text: str, result: DictationResult) -> str:
        """Persian normalisation, optional diacritization and punctuation."""
        processed = text
        if self.config.normalize_persian and self.pipeline_provider is not None:
            try:
                pipeline = self.pipeline_provider()
                if pipeline is not None:
                    from ..engines.text_normalization.pipeline import PipelineOptions

                    pipeline_result = pipeline.process(
                        processed,
                        PipelineOptions(
                            normalize=True,
                            diacritize=self.config.diacritize,
                            diacritization_mode="smart",
                            chunk=False,
                            language=self.config.language,
                            verbalise_numbers=True,
                            apply_dictionary=True,
                        ),
                    )
                    processed = pipeline_result.final_text or processed
                    result.warnings.extend(pipeline_result.warnings)
            except Exception as exc:  # noqa: BLE001
                log.warning("dictation normalisation failed", extra={"event": "vt_normalise_failed", "error": str(exc)})
                result.warnings.append(f"Text normalisation was skipped ({exc}).")
        options = PostProcessOptions(
            auto_punctuation=self.config.auto_punctuation
            or self.config.mode is VoiceTypingMode.PUSH_TO_TALK_PUNCTUATION,
            auto_capitalization=self.config.auto_capitalization,
            append_space=self.config.append_space,
            language=self.config.language,
            rules=tuple(self.config.post_process_rules),
        )
        return post_process(processed, options)

    def _insert(self, text: str) -> InsertionResult:
        return self.inserter.insert(
            text,
            self._target_hwnd,
            method=self.config.insert_method,
            restore_clipboard=self.config.restore_clipboard,
            paste_delay_ms=self.config.paste_delay_ms,
            submit_key=self.config.submit_key,
            blocked_apps=self.config.blocked_apps,
            allow_focus_change=True,
        )

    def _finish(self, result: DictationResult, session: int) -> None:
        with self._lock:
            if session != self._session_id:
                log.debug("stale dictation result discarded", extra={"event": "vt_stale"})
                return
            self._last_result = result
            self._history.append(result)
            self._history = self._history[-50:]
            self._last_activity = time.time()
        if result.state is VoiceTypingState.IDLE and not result.error:
            self._set_state(VoiceTypingState.IDLE, "Ready")
        elif result.state is VoiceTypingState.FALLBACK:
            self._set_state(VoiceTypingState.FALLBACK, result.insertion.message if result.insertion else "")
        elif result.state is VoiceTypingState.ERROR:
            self._set_state(VoiceTypingState.ERROR, result.error)
        for listener in list(self._result_listeners):
            with contextlib.suppress(Exception):
                listener(result)

    def _fail(self, message: str) -> None:
        result = DictationResult(state=VoiceTypingState.ERROR, error=message)
        result.warnings.append(message)
        self._stats["failed"] += 1
        self._finish(result, self._session_id)

    def _on_level(self, level: float, elapsed: float) -> None:
        for listener in list(self._level_listeners):
            with contextlib.suppress(Exception):
                listener(level, elapsed)

    # -- feedback -----------------------------------------------------------
    def _play_feedback(self, *, start: bool) -> None:
        if not self.config.sound_feedback:
            return
        try:
            import sys

            if sys.platform == "win32":  # pragma: no cover - Windows only
                import winsound

                winsound.Beep(880 if start else 620, 90)
            else:
                print("\a", end="", flush=True)
        except Exception:  # noqa: BLE001
            pass

    # -- diagnostics --------------------------------------------------------
    def diagnostics(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "mode": self.config.mode.value,
            "enabled": self.config.enabled,
            "language": self.config.language,
            "model": self.config.model_id,
            "sessions": self._stats["sessions"],
            "inserted": self._stats["inserted"],
            "failed": self._stats["failed"],
            "words": self._stats["words"],
            "last_seconds_ago": round(time.time() - self._last_activity, 1),
            "inserter": self.inserter.diagnostics(),
        }


def build_diacritizer_provider(service: Any) -> Callable[[], Any]:
    return lambda: service
