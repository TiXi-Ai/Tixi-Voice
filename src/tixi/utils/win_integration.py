"""Windows shell integration: tray, notifications, start-up, blur, single instance.

Everything degrades to a no-op (with a log line) on non-Windows systems so the
same code runs in development on Linux/macOS, and every ctypes call is guarded:
a missing API must never crash the application.

The acrylic/mica blur uses ``SetWindowCompositionAttribute`` — the same
undocumented-but-stable API used by Windows Terminal and many Electron apps.
When it is unavailable (Windows 10 1803 and older, remote desktop sessions,
transparency disabled in the OS) the UI falls back to an opaque layered
background, which the theme system provides automatically.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..app.logging_config import get_logger
from ..app.paths import IS_WINDOWS, APP_SLUG, APP_NAME, app_root

log = get_logger("tixi.win")

# --- SetWindowCompositionAttribute constants --------------------------------
ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
ACCENT_ENABLE_HOSTBACKDROP = 5

WCA_ACCENT_POLICY = 19


class ACCENT_POLICY(ctypes.Structure):  # noqa: N801 - Win32 naming
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


def is_windows() -> bool:
    return IS_WINDOWS


def enable_window_blur(hwnd: int, *, colour: tuple[int, int, int] = (24, 24, 28), alpha: int = 150, acrylic: bool = True) -> bool:
    """Turn on acrylic/blur behind a window.  Returns ``True`` on success."""
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        set_attribute = getattr(user32, "SetWindowCompositionAttribute", None)
        if set_attribute is None:
            log.info("acrylic blur is not supported by this Windows version", extra={"event": "blur_unsupported"})
            return False
        # GradientColor is AABBGGRR.
        gradient = (alpha << 24) | (colour[2] << 16) | (colour[1] << 8) | colour[0]
        policy = ACCENT_POLICY(
            AccentState=ACCENT_ENABLE_ACRYLICBLURBEHIND if acrylic else ACCENT_ENABLE_BLURBEHIND,
            AccentFlags=2,
            GradientColor=gradient,
            AnimationId=0,
        )
        data = WINDOWCOMPOSITIONATTRIBDATA(
            Attribute=WCA_ACCENT_POLICY,
            Data=ctypes.pointer(policy),
            SizeOfData=ctypes.sizeof(policy),
        )
        result = set_attribute(int(hwnd), ctypes.byref(data))
        if not result:
            log.info("acrylic blur request was refused by the compositor", extra={"event": "blur_refused"})
        return bool(result)
    except Exception as exc:  # noqa: BLE001 - never fatal
        log.warning("could not enable window blur", extra={"event": "blur_failed", "error": str(exc)})
        return False


def disable_window_blur(hwnd: int) -> bool:
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        policy = ACCENT_POLICY(AccentState=ACCENT_DISABLED, AccentFlags=0, GradientColor=0, AnimationId=0)
        data = WINDOWCOMPOSITIONATTRIBDATA(
            Attribute=WCA_ACCENT_POLICY, Data=ctypes.pointer(policy), SizeOfData=ctypes.sizeof(policy)
        )
        return bool(user32.SetWindowCompositionAttribute(int(hwnd), ctypes.byref(data)))
    except Exception:  # noqa: BLE001
        return False


def set_dark_titlebar(hwnd: int, dark: bool = True) -> bool:
    """Match the native title bar with the app theme (Windows 10 1809+)."""
    if not IS_WINDOWS:
        return False
    try:
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, legacy)
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(hwnd), attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
            if result == 0:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def window_dpi_scale(hwnd: int = 0) -> float:
    """Current DPI scale factor (1.0 = 96 DPI)."""
    if not IS_WINDOWS:
        return 1.0
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        if hwnd:
            return int(user32.GetDpiForWindow(int(hwnd))) / 96.0
        return int(user32.GetDpiForSystem()) / 96.0
    except Exception:  # noqa: BLE001
        return 1.0


def flash_window(hwnd: int) -> None:
    """Bring attention to a window without stealing focus."""
    if not IS_WINDOWS:
        return
    with contextlib.suppress(Exception):
        ctypes.windll.user32.FlashWindow(int(hwnd), True)


# ---------------------------------------------------------------------------
# Start with Windows
# ---------------------------------------------------------------------------
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_command() -> str:
    """The command that should run at logon."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimised'
    entry = app_root() / "main.py"
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    interpreter = windowless if windowless.exists() else python
    return f'"{interpreter}" "{entry}" --minimised'


def is_startup_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_SLUG)
            return bool(value)
    except FileNotFoundError:
        return False
    except Exception:  # noqa: BLE001
        return False


def set_startup_enabled(enabled: bool) -> tuple[bool, str]:
    """Register/unregister the application in ``HKCU\\...\\Run``."""
    if not IS_WINDOWS:
        return False, "Starting with Windows is only available on Windows."
    try:
        import winreg  # noqa: PLC0415

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, APP_SLUG, 0, winreg.REG_SZ, startup_command())
                return True, "Tixi Voice will start with Windows."
            try:
                winreg.DeleteValue(key, APP_SLUG)
            except FileNotFoundError:
                pass
        return True, "Tixi Voice will no longer start with Windows."
    except Exception as exc:  # noqa: BLE001
        log.warning("could not update the start-up entry", extra={"event": "startup_failed", "error": str(exc)})
        return False, f"Windows refused the change to the start-up list: {exc}"


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
@dataclass
class NotificationResult:
    shown: bool
    method: str = ""
    error: str = ""


def notify(title: str, message: str, *, timeout_s: int = 6, tray: Any = None) -> NotificationResult:
    """Show a Windows toast/balloon notification.

    Uses ``QSystemTrayIcon.showMessage`` when a tray icon exists (works on every
    supported Windows version), otherwise falls back to PowerShell's toast API.
    """
    if tray is not None:
        try:
            from PySide6.QtWidgets import QSystemTrayIcon  # noqa: PLC0415

            tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, timeout_s * 1000)
            return NotificationResult(True, "tray")
        except Exception as exc:  # noqa: BLE001
            log.debug("tray notification failed", extra={"event": "tray_notify_failed", "error": str(exc)})

    if not IS_WINDOWS:
        log.info("notification", extra={"event": "notify", "title": title, "message": message})
        return NotificationResult(False, "log", "Notifications require Windows.")

    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        f"$t.GetElementsByTagName('text')[0].AppendChild($t.CreateTextNode('{_escape(title)}')) > $null;"
        f"$t.GetElementsByTagName('text')[1].AppendChild($t.CreateTextNode('{_escape(message)}')) > $null;"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_NAME}').Show("
        "[Windows.UI.Notifications.ToastNotification]::new($t));"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return NotificationResult(True, "toast")
    except Exception as exc:  # noqa: BLE001
        return NotificationResult(False, "none", str(exc))


def _escape(text: str) -> str:
    return (text or "").replace("'", "''")[:200]


def open_in_explorer(path: Path | str, *, select: bool = False) -> bool:
    """Open a folder (or select a file) in Windows Explorer."""
    target = Path(path)
    try:
        if IS_WINDOWS:
            if select and target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                folder = target if target.is_dir() else target.parent
                os.startfile(str(folder))  # type: ignore[attr-defined]  # noqa: S606
            return True
        folder = target if target.is_dir() else target.parent
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(folder)])  # noqa: S603,S607
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("could not open the folder", extra={"event": "open_folder_failed", "error": str(exc)})
        return False


def reveal_path(path: Path | str) -> bool:
    return open_in_explorer(path, select=True)


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------
class SingleInstance:
    """Named mutex so a second launch focuses the existing window."""

    def __init__(self, name: str = APP_SLUG) -> None:
        self.name = f"Global\\{name}-single-instance"
        self._handle: Any = None

    def acquire(self) -> bool:
        if not IS_WINDOWS:
            return True
        try:
            kernel32 = ctypes.windll.kernel32
            self._handle = kernel32.CreateMutexW(None, False, self.name)
            last_error = kernel32.GetLastError()
            if last_error == 183:  # ERROR_ALREADY_EXISTS
                with contextlib.suppress(Exception):
                    kernel32.CloseHandle(self._handle)
                self._handle = None
                return False
            return True
        except Exception:  # noqa: BLE001
            return True

    def release(self) -> None:
        if self._handle:
            with contextlib.suppress(Exception):
                ctypes.windll.kernel32.ReleaseMutex(self._handle)
                ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None


def focus_existing_window(title_part: str = APP_NAME) -> bool:
    """Find a window by title and bring it to the foreground."""
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)  # type: ignore[misc]
        def _callback(hwnd: int, _lparam: int) -> bool:
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if title_part.lower() in buffer.value.lower():
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(_callback, 0)
        if not found:
            return False
        hwnd = found[0]
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:  # noqa: BLE001
        return False


def prevent_sleep(prevent: bool = True) -> bool:
    """Keep the machine awake while a long transcription runs."""
    if not IS_WINDOWS:
        return False
    try:
        flags = 0x80000000 | 0x00000001 | 0x00000002 if prevent else 0x80000000
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
        return True
    except Exception:  # noqa: BLE001
        return False
