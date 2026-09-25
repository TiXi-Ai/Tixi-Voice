"""Voice typing / dictation service.

Owns the pieces of the global push-to-talk feature and keeps them in sync with
the settings: the keyboard hook, the recorder, the transcription controller and
the floating overlay.  The Qt signals emitted here are what the Voice Typing
page, the tray menu and the overlay listen to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from ..app.logging_config import get_logger
from ..audio.devices import cached_devices, describe_device, resolve_input_device
from ..voice_typing.controller import (
    DictationResult,
    VoiceTypingConfig,
    VoiceTypingController,
    VoiceTypingState,
)
from ..voice_typing.floating_overlay import OverlayController
from ..voice_typing.hotkey_manager import GlobalHotkeyManager, HotkeyError, describe_shortcut
from ..voice_typing.text_inserter import TextInserter, limitations, supported_applications

log = get_logger("tixi.services.dictation")


class DictationService(QObject):
    """Glue between settings, the hotkey hook, the controller and the overlay."""

    state_changed = Signal(str, str)      # state value, human message
    level_changed = Signal(float, float)  # 0..1 level, elapsed seconds
    result_ready = Signal(object)         # DictationResult
    transcript_ready = Signal(str, str)   # inserted text, target application
    error = Signal(str)
    hotkey_changed = Signal(bool, str)    # active, detail

    def __init__(
        self,
        *,
        engine_provider: Callable[[], Any] | None = None,
        pipeline_provider: Callable[[], Any] | None = None,
        diacritizer_provider: Callable[[], Any] | None = None,
        settings_provider: Callable[[], Any] | None = None,
        parent: QObject | None = None,
        overlay: OverlayController | None = None,
        insanter: TextInserter | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings_provider = settings_provider or (lambda: None)
        self.overlay = overlay if overlay is not None else OverlayController()
        self.inserter = insanter or TextInserter()
        self.controller = VoiceTypingController(
            inserter=self.inserter,
            engine_provider=engine_provider,
            pipeline_provider=pipeline_provider,
            diacritizer_provider=diacritizer_provider,
        )
        self.hotkeys = GlobalHotkeyManager()
        self._manual_session = False
        self._last_error = ""
        self._overlay_options = _OverlayOptions()
        self.available = True

        self.controller.on_state_change(self._on_state)
        self.controller.on_level(self._on_level)
        self.controller.on_result(self._on_result)
        self.hotkeys.subscribe(self._on_hotkey)

    # -- configuration ------------------------------------------------------
    def apply_settings(self, settings: Any | None = None, *, start_hotkey: bool = True) -> None:
        """Re-read the settings and push them into every component."""
        settings = settings if settings is not None else self.settings_provider()
        if settings is None:
            return
        config = VoiceTypingConfig.from_settings(settings)
        self.controller.configure(config)
        self._overlay_options = _overlay_options(settings)
        shortcut = str(getattr(getattr(settings, "voice_typing", settings), "shortcut", "") or "")
        if shortcut and start_hotkey:
            self.set_shortcut(shortcut, start=bool(config.enabled))

    def set_shortcut(self, shortcut: str, *, start: bool = True) -> bool:
        self.hotkeys.set_shortcut(shortcut)
        if not start:
            self.hotkeys.stop()
            self.hotkey_changed.emit(False, "Global shortcut is turned off in Settings.")
            return False
        ok = self.start()
        return ok

    def start(self) -> bool:
        """Install the keyboard hook (no-op on non-Windows platforms)."""
        if not self.hotkeys.supported():
            self.hotkey_changed.emit(False, "Global shortcuts are only available on Windows.")
            return False
        try:
            active = self.hotkeys.start()
        except HotkeyError as exc:
            self._last_error = str(exc)
            self.hotkey_changed.emit(False, str(exc))
            self.error.emit(str(exc))
            return False
        detail = self.hotkeys.shortcut() if active else (self.hotkeys.last_error() or "The hook did not start.")
        self.hotkey_changed.emit(active, describe_shortcut(self.hotkeys.shortcut()) if active else detail)
        return active

    def stop(self) -> None:
        self.hotkeys.stop()
        if self.controller.state.is_busy:
            self.controller.cancel(reason="dictation service stopped")
        self.overlay.hide()
        self.hotkey_changed.emit(False, "Global shortcut stopped.")

    def shutdown(self) -> None:
        try:
            self.hotkeys.stop()
        finally:
            self.controller.shutdown()
            self.overlay.hide()

    @property
    def state(self) -> VoiceTypingState:
        return self.controller.state

    @property
    def last_result(self) -> DictationResult | None:
        return self.controller.last_result

    @property
    def results(self) -> list[DictationResult]:
        return self.controller.history

    def statistics(self) -> dict[str, int]:
        return self.controller.statistics

    def is_active(self) -> bool:
        return self.hotkeys.is_active()

    def last_error(self) -> str:
        return self._last_error or self.hotkeys.last_error()

    # -- manual sessions (from the UI) --------------------------------------
    def toggle_manual(self) -> bool:
        """Start or stop a dictation session from a button click."""
        if self.controller.state.is_busy:
            self.stop_manual()
            return False
        return self.start_manual()

    def start_manual(self) -> bool:
        target = 0
        try:
            target = int(self.inserter.foreground_window() or 0)
        except Exception:  # noqa: BLE001 - non-Windows
            target = 0
        self._manual_session = True
        return self.controller.start_session(target_hwnd=target, reason="manual")

    def stop_manual(self) -> None:
        self._manual_session = False
        self.controller.stop_session()

    def cancel(self, *, reason: str = "cancelled by the user") -> None:
        self._manual_session = False
        self.controller.cancel(reason=reason)

    # -- diagnostics --------------------------------------------------------
    def health_check(self, settings: Any | None = None) -> list[dict[str, Any]]:
        """Structured checks for the Voice Typing page."""
        settings = settings if settings is not None else self.settings_provider()
        checks: list[dict[str, Any]] = []

        platform_ok = self.hotkeys.supported()
        checks.append(
            _check(
                "Global shortcut",
                platform_ok,
                describe_shortcut(self.hotkeys.shortcut() if platform_ok else "unavailable")
                if platform_ok
                else "Global push-to-talk requires Windows (the rest of the app still works).",
                warn_only=not platform_ok,
            )
        )

        try:
            requested = getattr(getattr(settings, "audio", settings), "input_device", "")
            index = resolve_input_device(requested)
            devices = cached_devices() if index is None else None
            detail = describe_device(index)
            if devices is not None and devices.error:
                detail = devices.error
            checks.append(_check("Microphone", index is not None, detail))
            if devices is not None and len(devices.inputs) > 1:
                checks.append(
                    _check("Input devices", True, f"{len(devices.inputs)} inputs available.", warn_only=True)
                )
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("Microphone", False, str(exc)))

        engine_ok = False
        detail = "The speech-recognition engine is not installed yet."
        try:
            engine = self.controller.engine_provider() if self.controller.engine_provider else None
            if engine is not None:
                engine_ok = True
                name = getattr(engine, "display_name", "") or getattr(engine, "name", "The engine")
                detail = f"{name} is ready"
                try:
                    models = list(engine.capabilities.models) if getattr(engine, "capabilities", None) else []
                except Exception:  # noqa: BLE001
                    models = []
                if models:
                    detail += f" ({len(models)} model(s) installed)"
                if config.model_id:
                    detail += f" · using {config.model_id}"
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)
        checks.append(_check("Speech recognition", engine_ok, detail))

        config = VoiceTypingConfig.from_settings(settings) if settings is not None else VoiceTypingConfig()
        checks.append(
            _check(
                "Insertion method",
                True,
                {
                    "auto": "Automatic — Unicode typing with a clipboard fallback.",
                    "unicode": "Unicode keystrokes only.",
                    "clipboard": "Clipboard paste only.",
                }.get(str(getattr(config.insert_method, "value", config.insert_method)), "Automatic"),
                warn_only=True,
            )
        )

        try:
            diagnostics = self.inserter.diagnostics()
        except Exception as exc:  # noqa: BLE001
            diagnostics = {"error": str(exc)}
        if "error" not in diagnostics:
            checks.append(
                _check(
                    "Keyboard injection",
                    bool(diagnostics.get("windows", True)),
                    "Unicode typing with a clipboard fallback is available."
                    if diagnostics.get("windows")
                    else "Typing into other applications needs Windows; copy-to-clipboard still works.",
                    warn_only=not diagnostics.get("windows"),
                )
            )
            elevated = bool(diagnostics.get("we_are_elevated"))
            checks.append(
                _check(
                    "Privileges",
                    not elevated,
                    "Tixi Voice is running elevated — text cannot be typed into non-elevated windows."
                    if elevated
                    else "Running as a standard user (recommended).",
                    warn_only=elevated,
                )
            )
            self_target = str(diagnostics.get("foreground_process", "") or "")
            blocked = {name.lower() for name in (config.blocked_apps or [])}
            if self_target and self_target.lower() in blocked:
                checks.append(
                    _check(
                        "Active window",
                        False,
                        f"{self_target} is in the blocked list — dictation will not insert here.",
                    )
                )
            if diagnostics.get("target_elevated"):
                checks.append(
                    _check(
                        "Target window",
                        False,
                        "The focused window is elevated; Windows blocks input from a standard user process.",
                        warn_only=True,
                    )
                )
        checks.append(
            _check(
                "Verified applications",
                True,
                "Tested with: " + ", ".join(supported_applications()[:6]) + " and more.",
                warn_only=True,
            )
        )
        return checks

    # -- internal wiring ----------------------------------------------------
    def _on_hotkey(self, event: Any) -> None:
        self.controller.handle_hotkey(event)

    def _on_state(self, state: VoiceTypingState, message: str) -> None:
        self.state_changed.emit(str(getattr(state, "value", state)), message)
        if state.is_busy:
            config = self.controller.config
            self.overlay.show_recording(
                state_text=message or state.label,
                language=config.language,
                mode_label=getattr(config.mode, "label", str(config.mode)),
                config=self._overlay_options,
            )
        else:
            self.overlay.hide()

    def _on_level(self, level: float, elapsed: float) -> None:
        self.overlay.update_level(level, elapsed)
        self.level_changed.emit(level, elapsed)

    def _on_result(self, result: DictationResult) -> None:
        self.result_ready.emit(result)
        insertion = result.insertion
        target = insertion.target_title if insertion is not None else ""
        if result.text:
            self.transcript_ready.emit(result.text, target)
        if result.error:
            self._last_error = result.error
            self.error.emit(result.error)
        if result.text and not result.inserted:
            reason = ""
            if insertion is not None:
                reason = insertion.message or ""
            self.overlay.show_fallback(
                result.text,
                reason or result.error or "The text could not be typed automatically.",
            )


@dataclass
class _OverlayOptions:
    """The handful of theme values the overlay reads from its config object."""

    overlay_position: str = "bottom_center"
    overlay_opacity: float = 0.94
    accent: str = "#0A84FF"
    dark: bool = True


def _overlay_options(settings: Any) -> _OverlayOptions:
    appearance = getattr(settings, "appearance", None) if settings is not None else None
    voice = getattr(settings, "voice_typing", None) if settings is not None else None
    options = _OverlayOptions()
    if voice is not None:
        options.overlay_position = str(getattr(voice, "overlay_position", options.overlay_position))
        try:
            options.overlay_opacity = float(getattr(voice, "overlay_opacity", options.overlay_opacity))
        except (TypeError, ValueError):
            pass
    if appearance is not None:
        mode = str(getattr(appearance, "theme_mode", "system"))
        options.dark = mode != "light"
        accent = str(getattr(appearance, "accent", "") or "")
        if accent.startswith("#"):
            options.accent = accent
    return options


def _check(label: str, ok: bool, detail: str, *, warn_only: bool = False) -> dict[str, Any]:
    return {
        "label": label,
        "ok": bool(ok),
        "warn_only": bool(warn_only),
        "detail": detail,
        "status": "ok" if ok else ("warning" if warn_only else "error"),
    }
