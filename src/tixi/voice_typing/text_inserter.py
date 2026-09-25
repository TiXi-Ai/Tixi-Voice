"""Unicode-safe text insertion into the focused application.

Three real mechanisms, tried in a deliberate order:

1. **Clipboard paste** (default, ``Ctrl+V``): the only method that reliably
   carries Persian/RTL and mixed-direction text into Chromium, Electron, Office
   and native Win32 controls.  The user's clipboard is snapshotted first and
   restored afterwards, and it is *not* restored if the user changed it while we
   were working.
2. **Unicode ``SendInput``**: sends ``KEYEVENTF_UNICODE`` scan codes, which
   bypasses the keyboard layout.  Works in most native controls and in some
   Electron apps, and is the fallback when the clipboard is unavailable.
3. **Keystroke simulation** for plain ASCII only (some legacy controls reject
   Unicode input entirely).

Honest limitations — surfaced to the user instead of hidden:

* Windows blocks synthesized input into **elevated** applications when Tixi
  Voice itself is not elevated (UIPI).  We detect this before trying and offer
  the floating window with a Copy button.
* Applications that install keyboard hooks, run in a different session (RDP,
  secure desktop, UAC prompt) or use custom input stacks may ignore it
  completely.  The floating fallback window exists for exactly those cases.
* Password fields cannot be detected reliably without UI Automation, so Tixi
  Voice provides a per-application block list and a one-click pause instead of
  pretending it knows.
"""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..app.logging_config import get_logger
from ..app.paths import IS_WINDOWS
from ..utils.text_build import insert_text_at_cursor_safe
from .clipboard import Clipboard, ClipboardError, sequence_number

log = get_logger("tixi.voice.insert")

if IS_WINDOWS:  # pragma: no cover - platform specific
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
else:  # pragma: no cover
    user32 = None
    kernel32 = None

# --- Win32 constants --------------------------------------------------------
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_EXTENDEDKEY = 0x0001
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_RETURN = 0x0D
VK_V = 0x56
VK_INSERT = 0x2D
MAPVK_VK_TO_VSC = 0

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20


class InsertionMethod(str, Enum):
    AUTO = "auto"
    CLIPBOARD = "clipboard"
    UNICODE = "unicode"
    TYPE = "type"


@dataclass
class InsertionResult:
    """Outcome of one insertion attempt."""

    success: bool
    method: str = ""
    message: str = ""
    fallback_required: bool = False
    text: str = ""
    target_title: str = ""
    target_process: str = ""
    clipboard_restored: bool = True
    elapsed_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "method": self.method,
            "message": self.message,
            "fallback_required": self.fallback_required,
            "target": self.target_title,
            "process": self.target_process,
            "clipboard_restored": self.clipboard_restored,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "warnings": list(self.warnings),
        }


class TextInserter:
    """Inserts text into whatever window has the focus."""

    def __init__(self, clipboard: Clipboard | None = None) -> None:
        self.clipboard = clipboard or Clipboard()
        self._last_target = 0
        self._last_insert_at = 0.0

    # -- window helpers -----------------------------------------------------
    def foreground_window(self) -> int:
        if not IS_WINDOWS:
            return 0
        try:
            return int(user32.GetForegroundWindow())
        except Exception:  # noqa: BLE001
            return 0

    def window_title(self, hwnd: int) -> str:
        if not IS_WINDOWS or not hwnd:
            return ""
        try:
            length = int(user32.GetWindowTextLengthW(hwnd))
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            return buffer.value
        except Exception:  # noqa: BLE001
            return ""

    def process_name(self, hwnd: int) -> str:
        """Executable name of the process owning a window."""
        if not IS_WINDOWS or not hwnd:
            return ""
        try:
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if not handle:
                return ""
            try:
                buffer = ctypes.create_unicode_buffer(1024)
                size = ctypes.c_ulong(len(buffer))
                if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    return Path(buffer.value).name
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return ""
        return ""

    def is_elevated(self, hwnd: int) -> bool:
        """Is the target process running with administrator rights?"""
        if not IS_WINDOWS or not hwnd:
            return False
        try:
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if not handle:
                return False
            try:
                token = ctypes.c_void_p()
                if not ctypes.windll.advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
                    return False
                try:
                    elevation = ctypes.c_ulong()
                    returned = ctypes.c_ulong()
                    ok = ctypes.windll.advapi32.GetTokenInformation(
                        token, TokenElevation, ctypes.byref(elevation),
                        ctypes.sizeof(elevation), ctypes.byref(returned),
                    )
                    return bool(ok and elevation.value)
                finally:
                    kernel32.CloseHandle(token)
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return False

    def own_elevation(self) -> bool:
        if not IS_WINDOWS:
            return False
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return False

    def is_secure_desktop(self) -> bool:
        """True while a UAC prompt or the lock screen has the desktop."""
        if not IS_WINDOWS:
            return False
        try:
            return bool(user32.OpenInputDesktop(0, False, 0x0100)) is False
        except Exception:  # noqa: BLE001
            return False

    def describe_target(self, hwnd: int) -> tuple[str, str]:
        return self.window_title(hwnd), self.process_name(hwnd)

    # -- preflight ----------------------------------------------------------
    def preflight(self, target_hwnd: int, *, blocked_apps: list[str] | None = None) -> str:
        """Return an empty string when insertion should work, or a reason."""
        if not IS_WINDOWS:
            return (
                "Automatic insertion is only available on Windows. The recognised text is shown "
                "so you can copy it."
            )
        if not target_hwnd:
            return "Tixi Voice could not remember which window was focused. Use Copy instead."
        title, process = self.describe_target(target_hwnd)
        for blocked in blocked_apps or []:
            needle = blocked.strip().lower()
            if needle and (needle in process.lower() or needle in title.lower()):
                return f"'{process or title}' is in your blocked list, so nothing was inserted."
        if self.is_elevated(target_hwnd) and not self.own_elevation():
            return (
                f"'{title or process}' is running as administrator, and Windows blocks synthetic "
                "input from a non-elevated application into elevated ones. The text is shown below "
                "so you can paste it, or run Tixi Voice as administrator."
            )
        return ""

    # -- insertion ----------------------------------------------------------
    def insert(
        self,
        text: str,
        target_hwnd: int,
        *,
        method: InsertionMethod | str = InsertionMethod.AUTO,
        restore_clipboard: bool = True,
        paste_delay_ms: int = 90,
        submit_key: str = "none",
        blocked_apps: list[str] | None = None,
        allow_focus_change: bool = True,
    ) -> InsertionResult:
        """Insert ``text`` at the caret of ``target_hwnd``."""
        started = time.perf_counter()
        cleaned = insert_text_at_cursor_safe(text)
        method_enum = InsertionMethod(method) if not isinstance(method, InsertionMethod) else method
        title, process = self.describe_target(target_hwnd)
        result = InsertionResult(
            success=False, text=cleaned, target_title=title, target_process=process
        )
        if not cleaned:
            result.message = "There was nothing to insert."
            return result

        reason = self.preflight(target_hwnd, blocked_apps=blocked_apps)
        if reason:
            result.message = reason
            result.fallback_required = True
            return result

        current = self.foreground_window()
        if current != target_hwnd:
            if not allow_focus_change:
                result.message = (
                    "The focused window changed while the text was being recognised, so nothing was "
                    "typed. The text is shown below so you can paste it where you want it."
                )
                result.fallback_required = True
                return result
            # Bring the remembered window back: the user's intent was to type there.
            self._activate(target_hwnd)
            time.sleep(0.12)

        attempts: list[InsertionMethod] = []
        if method_enum is InsertionMethod.AUTO:
            attempts = [InsertionMethod.CLIPBOARD, InsertionMethod.UNICODE]
            if cleaned.isascii():
                attempts.append(InsertionMethod.TYPE)
        else:
            attempts = [method_enum]

        for attempt in attempts:
            try:
                if attempt is InsertionMethod.CLIPBOARD:
                    success, warnings = self._insert_via_clipboard(
                        cleaned, restore_clipboard=restore_clipboard, paste_delay_ms=paste_delay_ms
                    )
                elif attempt is InsertionMethod.UNICODE:
                    success, warnings = self._insert_unicode(cleaned), []
                else:
                    success, warnings = self._insert_keystrokes(cleaned), []
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "insertion attempt failed",
                    extra={"event": "insert_failed", "method": attempt.value, "error": str(exc)},
                )
                result.warnings.append(f"{attempt.value}: {exc}")
                continue
            result.warnings.extend(warnings)
            if success:
                result.success = True
                result.method = attempt.value
                result.message = f"Inserted {len(cleaned)} characters into {title or process}."
                if submit_key and submit_key != "none":
                    self.send_key(submit_key)
                break
            result.warnings.append(f"{attempt.value} was not accepted by the target application")

        if not result.success:
            result.fallback_required = True
            result.message = (
                f"'{title or process}' did not accept simulated input. "
                "This happens with elevated windows, some games and a few custom input stacks. "
                "The recognised text is shown below — use Copy or Insert to place it yourself."
            )
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        self._last_target = target_hwnd
        self._last_insert_at = time.time()
        log.info(
            "text insertion finished",
            extra={
                "event": "insert_finished",
                "success": result.success,
                "method": result.method,
                "process": process,
                "characters": len(cleaned),
                "ms": round(result.elapsed_ms, 1),
            },
        )
        return result

    # -- clipboard ----------------------------------------------------------
    def _insert_via_clipboard(
        self, text: str, *, restore_clipboard: bool, paste_delay_ms: int
    ) -> tuple[bool, list[str]]:
        warnings: list[str] = []
        snapshot = None
        sequence_before = sequence_number()
        if restore_clipboard:
            try:
                snapshot = self.clipboard.snapshot()
            except ClipboardError as exc:
                warnings.append(f"the clipboard could not be preserved: {exc}")
        if not self.clipboard.set_text(text):
            return False, [*warnings, "the clipboard could not be written to"]

        time.sleep(max(0.02, paste_delay_ms / 1000.0))
        self.send_paste()
        time.sleep(max(0.03, paste_delay_ms / 1000.0))

        if snapshot is not None:
            changed_by_user = sequence_number() not in (0, sequence_before + 1)
            if changed_by_user and self.clipboard.text() != text:
                warnings.append(
                    "The clipboard changed while inserting, so it was left untouched to avoid "
                    "overwriting your data."
                )
            else:
                try:
                    self.clipboard.restore(snapshot)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"the clipboard could not be restored: {exc}")
        return True, warnings

    def send_paste(self, *, use_shift_insert: bool = False) -> None:
        """Send Ctrl+V (or Shift+Insert, which some terminals prefer)."""
        if not IS_WINDOWS:
            return
        if use_shift_insert:
            self._send_combo(VK_SHIFT, VK_INSERT)
            return
        self._send_combo(VK_CONTROL, VK_V)

    def _send_combo(self, modifier: int, key: int) -> None:
        self._send_key_event(modifier, False)
        self._send_key_event(key, False)
        time.sleep(0.02)
        self._send_key_event(key, True)
        self._send_key_event(modifier, True)

    # -- unicode ------------------------------------------------------------
    def _insert_unicode(self, text: str) -> bool:
        if not IS_WINDOWS:
            return False
        sent = 0
        for char in text:
            if char == "\n":
                self._send_key_event(VK_RETURN, False)
                self._send_key_event(VK_RETURN, True)
                sent += 1
                continue
            if char == "\t":
                self._send_key_event(0x09, False)
                self._send_key_event(0x09, True)
                sent += 1
                continue
            code_units = char.encode("utf-16-le")
            for index in range(0, len(code_units), 2):
                unit = int.from_bytes(code_units[index : index + 2], "little")
                if not self._send_unicode_unit(unit):
                    return sent > 0
                sent += 1
        return sent > 0

    def _insert_keystrokes(self, text: str) -> bool:
        """Plain-ASCII path using virtual keys (works in legacy controls)."""
        if not IS_WINDOWS:
            return False
        virtual_keys = {
            " ": 0x20, "!": 0x31, '"': 0xDE, "#": 0x33, "$": 0x34, "%": 0x35,
            "&": 0x37, "'": 0xDE, "(": 0x39, ")": 0x30, "*": 0x38, "+": 0xBB,
            ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, ":": 0xBA, ";": 0xBA,
            "<": 0xBC, "=": 0xBB, ">": 0xBE, "?": 0xBF, "@": 0x32, "[": 0xDB,
            "\\": 0xDC, "]": 0xDD, "^": 0x36, "_": 0xBD, "`": 0xC0, "{": 0xDB,
            "|": 0xDC, "}": 0xDD, "~": 0xC0,
        }
        for char in text:
            if char == "\n":
                self._send_key_event(VK_RETURN, False)
                self._send_key_event(VK_RETURN, True)
                continue
            if char.isalpha():
                self._send_key_event(ord(char.upper()), False)
                self._send_key_event(ord(char.upper()), True)
            elif char.isdigit():
                self._send_key_event(ord(char), False)
                self._send_key_event(ord(char), True)
            elif char in virtual_keys:
                key = virtual_keys[char]
                needs_shift = char in '!@#$%^&*()_+{}|:"<>?~' or char.isupper()
                if needs_shift:
                    self._send_key_event(0x10, False)
                self._send_key_event(key, False)
                self._send_key_event(key, True)
                if needs_shift:
                    self._send_key_event(0x10, True)
            else:
                self._send_unicode_unit(ord(char))
            time.sleep(0.004)
        return True

    # -- low level ----------------------------------------------------------
    def _send_key_event(self, vk: int, key_up: bool) -> bool:
        if not IS_WINDOWS:
            return False
        try:
            flags = KEYEVENTF_KEYUP if key_up else 0
            scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
            user32.keybd_event(vk, scan, flags, 0)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _send_unicode_unit(self, unit: int) -> bool:
        """Send one UTF-16 code unit using ``SendInput`` with ``KEYEVENTF_UNICODE``."""
        if not IS_WINDOWS:
            return False

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class _INPUTUNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 24)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("union", _INPUTUNION)]

        try:
            inputs = (INPUT * 2)()
            for index, flags in enumerate((KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
                inputs[index].type = INPUT_KEYBOARD
                inputs[index].union.ki = KEYBDINPUT(0, unit, flags, 0, None)
            sent = user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))
            return sent == 2
        except Exception:  # noqa: BLE001
            return False

    def send_key(self, combination: str) -> bool:
        """Send a submit key such as ``enter`` or ``ctrl+enter``."""
        if not IS_WINDOWS:
            return False
        mapping = {"enter": VK_RETURN, "return": VK_RETURN, "tab": 0x09, "space": 0x20}
        parts = [part.strip().lower() for part in (combination or "").split("+") if part.strip()]
        if not parts:
            return False
        key = parts[-1]
        vk = mapping.get(key)
        if vk is None:
            return False
        modifiers = {"ctrl": VK_CONTROL, "control": VK_CONTROL, "shift": VK_SHIFT, "alt": 0x12}
        pressed = [modifiers[part] for part in parts[:-1] if part in modifiers]
        for modifier in pressed:
            self._send_key_event(modifier, False)
        self._send_key_event(vk, False)
        time.sleep(0.02)
        self._send_key_event(vk, True)
        for modifier in reversed(pressed):
            self._send_key_event(modifier, True)
        return True

    def _activate(self, hwnd: int) -> bool:
        if not IS_WINDOWS or not hwnd:
            return False
        try:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            if user32.SetForegroundWindow(hwnd):
                return True
            # SetForegroundWindow can be refused; a small Alt press usually unlocks it.
            self._send_key_event(0x12, False)
            self._send_key_event(0x12, True)
            time.sleep(0.05)
            return bool(user32.SetForegroundWindow(hwnd))
        except Exception:  # noqa: BLE001
            return False

    # -- diagnostics --------------------------------------------------------
    def diagnostics(self) -> dict[str, Any]:
        target = self.foreground_window()
        title, process = self.describe_target(target)
        return {
            "windows": IS_WINDOWS,
            "foreground_title": title,
            "foreground_process": process,
            "target_elevated": self.is_elevated(target),
            "we_are_elevated": self.own_elevation(),
            "clipboard_sequence": sequence_number(),
            "last_target": self._last_target,
            "seconds_since_last_insert": round(time.time() - self._last_insert_at, 1) if self._last_insert_at else None,
        }


def supported_applications() -> list[str]:
    """Applications verified by the maintainers (see docs/MANUAL_TESTING.md).

    This list is deliberately explicit: the application never claims universal
    compatibility, and the manual test checklist contains the exact steps used
    to verify each entry.
    """
    return [
        "Notepad",
        "WordPad",
        "Microsoft Word",
        "Microsoft Outlook (message body)",
        "Google Chrome (address bar, text fields, Google Docs)",
        "Microsoft Edge",
        "Mozilla Firefox",
        "Visual Studio Code",
        "Notepad++",
        "Discord",
        "Telegram Desktop",
        "WhatsApp Desktop",
        "Slack",
        "Windows Terminal",
        "PowerShell / cmd",
        "Windows search box",
    ]


def limitations() -> list[str]:
    return [
        "Elevated applications (running as administrator) reject input from a non-elevated window.",
        "UAC prompts, the lock screen and Ctrl+Alt+Del are on the secure desktop and cannot be reached.",
        "Games and some custom-rendered controls may ignore synthetic keyboard input.",
        "Remote Desktop sessions and virtual machines can block global hooks depending on policy.",
        "Password fields cannot be detected reliably without per-application automation.",
    ]
