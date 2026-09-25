"""Global keyboard shortcuts.

Push-to-talk needs **key-down and key-up** events for the whole system, which
``RegisterHotKey`` cannot provide (it only reports presses, and it *eats* the
key combination).  Tixi Voice therefore installs a Win32 low-level keyboard hook
(``WH_KEYBOARD_LL``) through ``ctypes`` — no third-party dependency, no elevated
privileges, and it is removed cleanly on exit or when the user disables voice
typing.

Design notes
------------
* The hook callback runs on a system thread and must return in a few
  milliseconds: it only pushes events into a queue; matching and dispatch happen
  on a worker thread.
* ``RegisterHotKey`` is used first to *test* for conflicts with shortcuts other
  applications already own, so the settings page can warn the user honestly.
* ``Ctrl+Alt+Delete`` and other secure-attention keys can never be captured —
  that is a Windows rule, and the UI says so instead of pretending otherwise.
* On Linux/macOS the hook is unavailable; the manager reports that clearly and
  the UI offers the in-app shortcut (which only works when Tixi Voice has focus).
"""

from __future__ import annotations

import contextlib
import ctypes
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..app.config import shortcut_parts
from ..app.logging_config import get_logger
from ..app.paths import IS_WINDOWS

log = get_logger("tixi.voice.hotkeys")

# --- Win32 constants --------------------------------------------------------
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x00000010
LLKHF_LOWER_IL_INJECTED = 0x00000002

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK_NAMES: dict[int, str] = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause", 0x14: "CapsLock",
    0x1B: "Escape", 0x20: "Space", 0x21: "PageUp", 0x22: "PageDown", 0x23: "End",
    0x24: "Home", 0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete",
    0x5B: "Win", 0x5C: "Win", 0x5D: "Menu",
    0x60: "Num0", 0x61: "Num1", 0x62: "Num2", 0x63: "Num3", 0x64: "Num4",
    0x65: "Num5", 0x66: "Num6", 0x67: "Num7", 0x68: "Num8", 0x69: "Num9",
    0x6A: "Multiply", 0x6B: "Add", 0x6D: "Subtract", 0x6E: "Decimal", 0x6F: "Divide",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`",
    0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}
for _index in range(0x70, 0x88):  # F1..F24
    VK_NAMES[_index] = f"F{_index - 0x6F}"
for _index in range(0x41, 0x5B):  # A..Z
    VK_NAMES[_index] = chr(_index)
for _index in range(0x30, 0x3A):  # 0..9
    VK_NAMES[_index] = chr(_index)

NAME_TO_VK: dict[str, int] = {name.upper(): code for code, name in VK_NAMES.items()}
NAME_TO_VK.update(
    {
        "ESC": 0x1B,
        "RETURN": 0x0D,
        "SPACEBAR": 0x20,
        "CONTROL": 0x11,
        "CTRL": 0x11,
        "SHIFT": 0x10,
        "ALT": 0x12,
        "WIN": 0x5B,
        "DEL": 0x2E,
        "GRAVE": 0xC0,
    }
)

#: Keys Windows never lets an application capture.
FORBIDDEN_KEYS = frozenset({"Delete"})  # only in Ctrl+Alt+Del, checked below
SECURE_COMBINATIONS = frozenset({"Ctrl+Alt+Delete", "Ctrl+Alt+Del"})


class HotkeyError(RuntimeError):
    """Raised when a hook cannot be installed or a shortcut is invalid."""


@dataclass
class HotkeyEvent:
    """One keyboard event that matched the configured shortcut."""

    shortcut: str
    pressed: bool
    timestamp: float = field(default_factory=time.time)
    injected: bool = False


@dataclass
class ConflictReport:
    """Result of asking Windows whether a shortcut is already taken."""

    shortcut: str
    conflict: bool
    detail: str = ""

    def describe(self) -> str:
        if not self.conflict:
            return f"{self.shortcut} is free to use."
        return self.detail or f"Another application already uses {self.shortcut}."


class GlobalHotkeyManager:
    """Installs the keyboard hook and reports push-to-talk style events."""

    def __init__(self) -> None:
        self._shortcut = "Ctrl+Shift+Space"
        self._modifiers: set[str] = set()
        self._key = "Space"
        self._hook: Any = None
        self._thread: threading.Thread | None = None
        self._events: queue.Queue[HotkeyEvent] = queue.Queue(maxsize=256)
        self._callbacks: list[Callable[[HotkeyEvent], None]] = []
        self._lock = threading.RLock()
        self._enabled = False
        self._suppress = False
        self._pressed = False
        self._worker: threading.Thread | None = None
        self._running = threading.Event()
        self._last_error = ""
        self._declared_conflict: bool = False

    # -- configuration ------------------------------------------------------
    def set_shortcut(self, shortcut: str) -> None:
        modifiers, key = shortcut_parts(shortcut)
        with self._lock:
            self._shortcut = "+".join([*modifiers, key])
            self._modifiers = {modifier.lower() for modifier in modifiers}
            self._key = key.lower()
        log.info("global shortcut updated", extra={"event": "hotkey_update", "shortcut": self._shortcut})

    @property
    def shortcut(self) -> str:
        return self._shortcut

    @property
    def is_active(self) -> bool:
        return self._enabled

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def supported(self) -> bool:
        return IS_WINDOWS

    def subscribe(self, callback: Callable[[HotkeyEvent], None]) -> None:
        self._callbacks.append(callback)

    def unsubscribe(self, callback: Callable[[HotkeyEvent], None]) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> bool:
        """Install the hook.  Returns ``False`` (with ``last_error`` set) on failure."""
        if self._enabled:
            return True
        if not IS_WINDOWS:
            self._last_error = (
                "System-wide shortcuts need Windows. On this platform voice typing works only "
                "while the Tixi Voice window is focused."
            )
            log.warning("global hotkeys unavailable", extra={"event": "hotkeys_unsupported"})
            return False
        with self._lock:
            self._running.set()
            self._thread = threading.Thread(target=self._hook_thread, name="tixi-hotkey-hook", daemon=True)
            self._thread.start()
            self._worker = threading.Thread(target=self._dispatch_thread, name="tixi-hotkey-dispatch", daemon=True)
            self._worker.start()
        # Give the hook a moment to install so callers get a truthful answer.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if self._enabled or self._last_error:
                break
            time.sleep(0.02)
        if self._enabled:
            log.info("global hotkey hook installed", extra={"event": "hotkeys_started", "shortcut": self._shortcut})
        else:
            log.error("global hotkey hook failed", extra={"event": "hotkeys_failed", "error": self._last_error})
        return self._enabled

    def stop(self) -> None:
        """Remove the hook and stop the worker threads cleanly."""
        self._running.clear()
        if IS_WINDOWS and self._thread is not None:
            try:
                user32 = ctypes.windll.user32
                if self._hook:
                    user32.UnhookWindowsHookEx(self._hook)
                    self._hook = None
                # Break the message loop with a WM_QUIT.
                thread_id = getattr(self, "_thread_id", 0)
                if thread_id:
                    user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not remove the keyboard hook", extra={"event": "hotkey_unhook_failed", "error": str(exc)})
        self._enabled = False
        self._pressed = False
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
        if self._worker is not None:
            self._worker.join(timeout=1.5)
            self._worker = None
        log.info("global hotkeys stopped", extra={"event": "hotkeys_stopped"})

    def set_temporarily_disabled(self, disabled: bool) -> None:
        """The "pause voice typing" switch in the tray menu."""
        self._suppress = bool(disabled)
        if disabled:
            self._pressed = False
        log.info(
            "voice typing shortcut temporarily disabled" if disabled else "voice typing shortcut re-enabled",
            extra={"event": "hotkey_suppressed", "disabled": disabled},
        )

    @property
    def temporarily_disabled(self) -> bool:
        return self._suppress

    # -- conflicts ----------------------------------------------------------
    def test_conflict(self, shortcut: str | None = None) -> ConflictReport:
        """Ask Windows whether another application owns this shortcut."""
        target = shortcut or self._shortcut
        if not IS_WINDOWS:
            return ConflictReport(target, False, "Conflict detection needs Windows.")
        try:
            modifiers, key = shortcut_parts(target)
        except ValueError as exc:
            return ConflictReport(target, True, f"Invalid shortcut: {exc}")
        if target in SECURE_COMBINATIONS:
            return ConflictReport(
                target,
                True,
                f"{target} is reserved by Windows for secure attention and can never be captured.",
            )
        if not modifiers:
            return ConflictReport(
                target,
                True,
                f"{target} has no modifier key. A bare key would interfere with normal typing — "
                "add Ctrl, Alt, Shift or Win.",
            )
        mod_value = 0
        for modifier in modifiers:
            mod_value |= {
                "ctrl": MOD_CONTROL,
                "shift": MOD_SHIFT,
                "alt": MOD_ALT,
                "win": MOD_WIN,
            }.get(modifier.lower(), 0)
        vk = NAME_TO_VK.get(key.upper())
        if vk is None:
            return ConflictReport(target, False, "This key cannot be checked automatically.")
        try:
            user32 = ctypes.windll.user32
            if not user32.RegisterHotKey(None, 0xB701, mod_value | MOD_NOREPEAT, vk):
                error = ctypes.get_last_error()
                detail = (
                    f"{target} is already registered by Windows or another application "
                    f"(error {error}). Choose a different combination."
                )
                self._declared_conflict = True
                return ConflictReport(target, True, detail)
            user32.UnregisterHotKey(None, 0xB701)
            self._declared_conflict = False
            return ConflictReport(target, False, f"{target} is available.")
        except Exception as exc:  # noqa: BLE001
            return ConflictReport(target, False, f"Could not test the shortcut: {exc}")

    # -- hook implementation (Windows) --------------------------------------
    def _hook_thread(self) -> None:
        try:
            from ctypes import wintypes  # noqa: PLC0415

            class KBDLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("vkCode", wintypes.DWORD),
                    ("scanCode", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_ssize_t, ctypes.c_void_p)
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            self._thread_id = kernel32.GetCurrentThreadId()

            def _callback(code: int, wparam: int, lparam: int) -> int:
                if code < 0 or self._suppress:
                    return user32.CallNextHookEx(self._hook, code, wparam, lparam)
                try:
                    info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    injected = bool(info.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED))
                    if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        self._on_key(info.vkCode, True, injected)
                    elif wparam in (WM_KEYUP, WM_SYSKEYUP):
                        self._on_key(info.vkCode, False, injected)
                except Exception:  # noqa: BLE001 - never break the hook chain
                    pass
                return user32.CallNextHookEx(self._hook, code, wparam, lparam)

            self._callback_ref = HOOKPROC(_callback)
            self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._callback_ref, None, 0)
            if not self._hook:
                self._last_error = (
                    "Windows refused the global keyboard hook. If an antivirus or security policy "
                    "blocks input hooks, allow Tixi Voice and try again."
                )
                return
            self._enabled = True
            message = wintypes.MSG()
            while self._running.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"Could not install the keyboard hook: {exc}"
        finally:
            self._enabled = False
            if self._hook:
                with contextlib.suppress(Exception):
                    ctypes.windll.user32.UnhookWindowsHookEx(self._hook)
                self._hook = None

    def _on_key(self, vk: int, pressed: bool, injected: bool) -> None:
        name = VK_NAMES.get(vk, "")
        if not name:
            return
        lowered = name.lower()
        if lowered in ("ctrl", "shift", "alt", "win") or vk in (0x10, 0x11, 0x12, 0x5B, 0x5C):
            return  # modifier only
        if lowered != self._key:
            return
        if not self._modifiers_held():
            if pressed and self._pressed:
                # Modifier released before the main key: treat as release.
                self._pressed = False
                self._emit(False, injected)
            return
        if pressed and not self._pressed:
            self._pressed = True
            self._emit(True, injected)
        elif not pressed and self._pressed:
            self._pressed = False
            self._emit(False, injected)

    def _modifiers_held(self) -> bool:
        try:
            user32 = ctypes.windll.user32
            state = {
                "ctrl": bool(user32.GetAsyncKeyState(0x11) & 0x8000),
                "shift": bool(user32.GetAsyncKeyState(0x10) & 0x8000),
                "alt": bool(user32.GetAsyncKeyState(0x12) & 0x8000),
                "win": bool(user32.GetAsyncKeyState(0x5B) & 0x8000)
                or bool(user32.GetAsyncKeyState(0x5C) & 0x8000),
            }
            return all(state.get(modifier, False) for modifier in self._modifiers)
        except Exception:  # noqa: BLE001
            return True

    def _emit(self, pressed: bool, injected: bool) -> None:
        event = HotkeyEvent(self._shortcut, pressed, injected=injected)
        try:
            self._events.put_nowait(event)
        except queue.Full:  # pragma: no cover - the dispatcher is keeping up
            with contextlib.suppress(Exception):
                self._events.get_nowait()

    def _dispatch_thread(self) -> None:
        while self._running.is_set() or not self._events.empty():
            try:
                event = self._events.get(timeout=0.2)
            except queue.Empty:
                continue
            for callback in list(self._callbacks):
                try:
                    callback(event)
                except Exception:  # noqa: BLE001 - a broken listener must not kill dispatch
                    log.exception("hotkey listener failed")

    # -- testing helpers ----------------------------------------------------
    def simulate(self, pressed: bool, *, injected: bool = True) -> None:
        """Inject an event without a keyboard (used by the tests and diagnostics)."""
        self._emit(pressed, injected)

    def key_names(self) -> Iterable[str]:
        return sorted({name for name in VK_NAMES.values()})


def describe_shortcut(shortcut: str) -> str:
    """Pretty label for the UI ("Ctrl + Shift + Space")."""
    try:
        modifiers, key = shortcut_parts(shortcut)
    except ValueError:
        return shortcut
    return " + ".join([*modifiers, _pretty_key(key)])


def _pretty_key(key: str) -> str:
    mapping = {"Space": "Space", "PageUp": "Page Up", "PageDown": "Page Down"}
    return mapping.get(key, key)
