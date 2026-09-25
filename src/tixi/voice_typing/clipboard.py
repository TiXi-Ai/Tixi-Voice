"""Clipboard access with faithful preservation.

Dictation inserts text through the clipboard because it is the only mechanism
that reliably delivers **Persian/RTL and mixed-direction** text to arbitrary
Windows applications (Chromium, Electron, Word, Notepad, Telegram, …).
Simulating each character with ``SendInput`` loses text in many of those apps.

The rules this module enforces:

* never leave the user's clipboard replaced, unless they explicitly ask for it,
* never overwrite a clipboard the *user* changed while we were working
  (the Windows clipboard sequence number tells us when that happened),
* restore as many formats as possible, and report honestly when a format could
  not be restored (some private/delayed formats are simply not copyable).
"""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass, field
from typing import Any

from ..app.logging_config import get_logger
from ..app.paths import IS_WINDOWS

log = get_logger("tixi.voice.clipboard")

CF_UNICODETEXT = 13
CF_TEXT = 1
GMEM_MOVEABLE = 0x0002

user32 = ctypes.windll.user32 if IS_WINDOWS else None
kernel32 = ctypes.windll.kernel32 if IS_WINDOWS else None


class ClipboardError(RuntimeError):
    """Raised when the clipboard cannot be accessed."""


@dataclass
class ClipboardSnapshot:
    """A best-effort copy of every clipboard format we could read."""

    formats: dict[int, bytes] = field(default_factory=dict)
    text: str = ""
    sequence: int = 0
    captured_at: float = field(default_factory=time.time)
    unrestorable: list[int] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.formats

    def describe(self) -> str:
        kinds = ", ".join(_format_name(code) for code in self.formats) or "empty"
        return f"{len(self.formats)} format(s): {kinds}"


def _format_name(code: int) -> str:
    known = {
        1: "text", 2: "bitmap", 3: "metafile", 8: "DIB", 13: "unicode-text",
        14: "enhmetafile", 15: "auto-hlink", 16: "drop-files", 17: "palette",
    }
    if code in known:
        return known[code]
    if 0xC000 <= code <= 0xFFFF:
        return f"registered({code:#x})"
    return f"format({code})"


def sequence_number() -> int:
    """Windows clipboard change counter (0 on other platforms)."""
    if not IS_WINDOWS:
        return 0
    try:
        return int(user32.GetClipboardSequenceNumber())
    except Exception:  # noqa: BLE001
        return 0


class Clipboard:
    """Thin wrapper around the Win32 clipboard with Qt/dummy fallbacks."""

    def __init__(self, *, qt_clipboard: Any = None) -> None:
        self._qt = qt_clipboard

    # -- reading ------------------------------------------------------------
    def text(self) -> str:
        if IS_WINDOWS:
            return self._win_text()
        if self._qt is not None:
            try:
                return self._qt.text() or ""
            except Exception:  # noqa: BLE001
                return ""
        return ""

    def has_text(self) -> bool:
        return bool(self.text().strip())

    def snapshot(self) -> ClipboardSnapshot:
        """Capture every format, so the clipboard can be restored afterwards."""
        snap = ClipboardSnapshot(sequence=sequence_number())
        if not IS_WINDOWS:
            snap.text = self.text()
            return snap
        opened = False
        for attempt in range(8):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.03 * (attempt + 1))
        if not opened:
            raise ClipboardError(
                "Another application is holding the clipboard open. Please try again."
            )
        try:
            snap.text = self._win_text_locked()
            if snap.text:
                snap.formats[CF_UNICODETEXT] = snap.text.encode("utf-16-le")
            for code in self._enumerate_formats():
                if code in (CF_UNICODETEXT,):
                    continue
                if code in (CF_TEXT,):
                    payload = _read_bytes(code)
                    if payload:
                        snap.formats[code] = payload
                    continue
                payload = _read_bytes(code)
                if payload:
                    snap.formats[code] = payload
                else:
                    snap.unrestorable.append(code)
        finally:
            user32.CloseClipboard()
        return snap

    def _enumerate_formats(self) -> list[int]:
        formats: list[int] = []
        try:
            current = 0
            while True:
                current = int(user32.EnumClipboardFormats(current))
                if current == 0:
                    break
                formats.append(current)
                if len(formats) > 64:
                    break
        except Exception:  # noqa: BLE001
            pass
        return formats

    def _win_text(self) -> str:
        if not IS_WINDOWS:
            return ""
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            if not user32.OpenClipboard(None):
                return ""
            try:
                return self._win_text_locked()
            finally:
                user32.CloseClipboard()
        except Exception:  # noqa: BLE001
            return ""

    def _win_text_locked(self) -> str:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)

    # -- writing ------------------------------------------------------------
    def set_text(self, text: str) -> bool:
        if IS_WINDOWS:
            return self._win_set_text(text)
        if self._qt is not None:
            try:
                self._qt.setText(text)
                return True
            except Exception:  # noqa: BLE001
                return False
        return False

    def _win_set_text(self, text: str) -> bool:
        if not IS_WINDOWS:
            return False
        for attempt in range(8):
            if user32.OpenClipboard(None):
                break
            time.sleep(0.03 * (attempt + 1))
        else:
            raise ClipboardError("The clipboard is locked by another application.")
        try:
            user32.EmptyClipboard()
            payload = (text or "").encode("utf-16-le") + b"\x00\x00"
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
            if not handle:
                return False
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                kernel32.GlobalFree(handle)
                return False
            try:
                ctypes.memmove(pointer, payload, len(payload))
            finally:
                kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
                return False
            return True
        finally:
            user32.CloseClipboard()

    def restore(self, snapshot: ClipboardSnapshot) -> bool:
        """Put a snapshot back.  Returns ``False`` when it could not be restored."""
        if snapshot.is_empty:
            return True
        if not IS_WINDOWS:
            if snapshot.text:
                return self.set_text(snapshot.text)
            return True
        for attempt in range(8):
            if user32.OpenClipboard(None):
                break
            time.sleep(0.03 * (attempt + 1))
        else:
            log.warning("could not restore the clipboard: it stayed locked", extra={"event": "clipboard_restore_locked"})
            return False
        try:
            user32.EmptyClipboard()
            for code, payload in snapshot.formats.items():
                if code == CF_UNICODETEXT:
                    self._set_unicode_locked(payload.decode("utf-16-le").rstrip("\x00"))
                    continue
                if code in (2, 3, 14):  # bitmap/metafile handles are not restorable
                    continue
                _write_bytes(code, payload)
        finally:
            user32.CloseClipboard()
        return True

    def _set_unicode_locked(self, text: str) -> None:
        payload = (text or "").encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not handle:
            return
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, payload, len(payload))
        kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(CF_UNICODETEXT, handle)


def _read_bytes(code: int) -> bytes:
    handle = user32.GetClipboardData(code)
    if not handle:
        return b""
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        return b""
    try:
        size = kernel32.GlobalSize(handle)
        if size <= 0:
            return b""
        return ctypes.string_at(pointer, size)
    except Exception:  # noqa: BLE001
        return b""
    finally:
        kernel32.GlobalUnlock(handle)


def _write_bytes(code: int, payload: bytes) -> bool:
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not handle:
        return False
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        return False
    try:
        ctypes.memmove(pointer, payload, len(payload))
    finally:
        kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(code, handle):
        kernel32.GlobalFree(handle)
        return False
    return True
