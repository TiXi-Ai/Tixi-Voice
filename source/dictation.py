# SPDX-License-Identifier: GPL-3.0-or-later
"""Global dictation (system-wide hold-to-talk).

A registered hotkey (Ctrl+Shift+Space) works in every application. While the key
is held the microphone is recorded; on release the audio is transcribed locally
with whisper.cpp (auto language) and the resulting text is typed into whatever
window has focus, character by character (Unicode, Persian and English safe).

Windows only. Uses ctypes (RegisterHotKey, GetAsyncKeyState, SendInput) so no
extra dependency is introduced.
"""
from __future__ import annotations
import ctypes
import ctypes.wintypes
import logging
import os
import threading
import time
import uuid

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

from core import UserError

LOG = logging.getLogger('tixi.voice')

# --- Win32 constants -------------------------------------------------------
WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002
VK_SPACE = 0x20

user32 = ctypes.windll.user32

HOTKEY_ID = 1
DEFAULT_MOD = MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT
DEFAULT_VK = VK_SPACE
HOTKEY_LABEL = 'Ctrl + Shift + Space'


def cue(kind: str) -> None:
    """Short system sound so the user hears the outcome without leaving the focused app."""
    try:
        import winsound
        winsound.MessageBeep({'ok': 0x40, 'warn': 0x30, 'error': 0x10}.get(kind, 0x40))
    except Exception:
        pass


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', ctypes.c_ulong),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
                ('dwExtraInfo', ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort), ('dwFlags', ctypes.c_ulong),
                ('time', ctypes.c_ulong), ('dwExtraInfo', ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', ctypes.c_ulong), ('wParamL', ctypes.c_ushort), ('wParamH', ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', ctypes.c_ulong), ('u', _INPUTUNION)]


def hotkey_down() -> bool:
    """True while the space key is physically held."""
    return bool(user32.GetAsyncKeyState(VK_SPACE) & 0x8000)


def send_text(text: str) -> bool:
    """Type *text* into the focused window (Unicode, layout independent).

    Returns True when every character was delivered by SendInput; False tells the
    caller to fall back to the clipboard (UIPI can block injected input when the
    focused window runs at a higher integrity level).
    """
    if not text:
        return True
    try:
        raw = text.encode('utf-16-le')
        codes = [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]
        count = len(codes)
        Array = INPUT * (count * 2)
        arr = Array()
        for i, code in enumerate(codes):
            arr[i * 2].type = INPUT_KEYBOARD
            arr[i * 2].ki.wScan = code
            arr[i * 2].ki.dwFlags = KEYEVENTF_UNICODE
            arr[i * 2 + 1].type = INPUT_KEYBOARD
            arr[i * 2 + 1].ki.wScan = code
            arr[i * 2 + 1].ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
        sent = user32.SendInput(count * 2, ctypes.byref(arr), ctypes.sizeof(INPUT))
        return int(sent) == count * 2
    except Exception:
        LOG.exception('SendInput failed')
        return False


def paste_text(text: str) -> None:
    """Fallback insertion: clipboard + Ctrl+V (used only if SendInput is blocked)."""
    try:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
    except Exception:
        return
    for vk in (0x11, 0x56):
        user32.keybd_event(vk, 0, 0, 0)
    for vk in (0x56, 0x11):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


class NativeHotkeyFilter(QAbstractNativeEventFilter):
    """Receives WM_HOTKEY from the Qt event loop and forwards it to *callback*."""

    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, event_type, message):
        try:
            if event_type == b'windows_generic_MSG':
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY:
                    self._callback(int(msg.wParam))
        except Exception:
            LOG.exception('Native hotkey filter failed')
        return False


class DictationController(QObject):
    """Owns the global hotkey and the record -> transcribe -> type pipeline."""

    status = Signal(str)     # status bar message
    recording = Signal(bool) # hold-to-talk started / stopped
    working = Signal(bool)   # transcription in progress
    failed = Signal(str)     # non fatal problem; shown as status text only

    def __init__(self, store, providers=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.providers = providers or (lambda: {})
        self._app = None
        self._filter = None
        self._enabled = False
        self._busy = False
        self._options = {}
        self._recorder = None
        self._cancel = threading.Event()
        self._thread = None

    # ---- lifecycle ---------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def recorder(self):
        """Live Recorder while dictation is capturing, else None (read-only for UI)."""
        return self._recorder

    def install(self, app) -> bool:
        if os.name != 'nt' or self._enabled:
            return self._enabled
        if not user32.RegisterHotKey(None, HOTKEY_ID, DEFAULT_MOD, DEFAULT_VK):
            self.failed.emit('کلید میانبر «%s» را برنامهٔ دیگری گرفته است؛ آن برنامه را ببند یا دیکته را غیرفعال کن.' % HOTKEY_LABEL)
            return False
        self._filter = NativeHotkeyFilter(self._on_hotkey)
        app.installNativeEventFilter(self._filter)
        self._app = app
        self._enabled = True
        return True

    def uninstall(self):
        if not self._enabled:
            return
        self._cancel.set()
        if self._app is not None and self._filter is not None:
            self._app.removeNativeEventFilter(self._filter)
        self._filter = None
        user32.UnregisterHotKey(None, HOTKEY_ID)
        self._enabled = False

    # ---- hotkey ------------------------------------------------------------
    def _on_hotkey(self, wparam):
        if wparam != HOTKEY_ID or self._busy or not self._enabled:
            return
        # Runs on the Qt event loop, so it is safe to read widget state here;
        # the worker thread only consumes the captured snapshot.
        try:
            self._options = dict(self.providers() or {})
        except Exception:
            LOG.exception('Dictation options failed')
            self._options = {}
        self._cancel.clear()
        self._busy = True
        self._thread = threading.Thread(target=self._run, daemon=True, name='dictation')
        self._thread.start()

    def cancel(self):
        self._cancel.set()

    def wait(self, timeout: float = 2.0) -> bool:
        """Join the worker thread; returns True when it is no longer running."""
        t = self._thread
        if t and t.is_alive():
            t.join(timeout)
            return not t.is_alive()
        return True

    # ---- pipeline ----------------------------------------------------------
    def _run(self):
        try:
            path = self._record()
            if path is None:
                return
            try:
                self._transcribe_and_type(path)
            finally:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        finally:
            self._recorder = None
            self.recording.emit(False)
            self.working.emit(False)
            self._busy = False

    def _record(self):
        from audio import Recorder
        rec_dir = self.store.root / 'temp' / 'dictation'
        try:
            rec_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.failed.emit('پوشهٔ موقت دیکته ساخته نشد؛ فضای دیسک را بررسی کن.')
            return None
        path = rec_dir / ('dict-' + uuid.uuid4().hex + '.wav')
        rec = Recorder()
        self._recorder = rec
        device = self._options.get('device')
        self.recording.emit(True)
        self.status.emit('در حال ضبط… کلید را رها کن تا متن تایپ شود.')
        try:
            rec.start(path, device)
        except UserError as e:
            cue('error')
            self.failed.emit(str(e))
            return None
        except Exception:
            LOG.exception('Dictation recording failed')
            cue('error')
            self.failed.emit('ضبط دیکته انجام نشد؛ میکروفون و مجوز Microphone را بررسی کن.')
            return None
        # Hold-to-talk: keep capturing until the key is physically released.
        while not self._cancel.is_set() and hotkey_down() and time.monotonic() < rec.started + 120:
            time.sleep(0.04)
        try:
            out = rec.stop()
        except UserError as e:
            cue('warn')
            self.status.emit(str(e))
            return None
        except Exception:
            LOG.exception('Dictation recording stop failed')
            cue('error')
            self.failed.emit('پایان ضبط دیکته ناموفق بود؛ دوباره تلاش کن.')
            return None
        self.recording.emit(False)
        self.working.emit(True)
        self.status.emit('در حال تبدیل گفتار به متن…')
        return out

    def _transcribe_and_type(self, path):
        from engines import transcribe
        from core import Cancelled
        options = self._options
        key = options.get('model') or 'stt-small'
        if key not in ('stt-small', 'stt-base', 'stt-medium'):
            key = 'stt-small'
        try:
            result = transcribe(self.store.root, key, path, 'auto', self._cancel, self._progress)
        except Cancelled:
            self.status.emit('دیکته لغو شد.')
            return
        except UserError as e:
            cue('error')
            self.failed.emit(str(e))
            return
        except Exception:
            LOG.exception('Dictation transcription failed')
            cue('error')
            self.failed.emit('تبدیل گفتار به متن انجام نشد؛ مدل را از بخش مدل‌ها نصب کن و دوباره تلاش کن.')
            return
        if self._cancel.is_set():
            self.status.emit('دیکته لغو شد.')
            return
        text = (result.get('text') or '').strip()
        if not text:
            cue('warn')
            self.status.emit('گفتاری تشخیص داده نشد؛ دوباره تلاش کن.')
            return
        # Give the user time to release the modifiers before the text is injected.
        time.sleep(0.12)
        if not send_text(text):
            paste_text(text)
        preview = text.replace('\n', ' ')
        if len(preview) > 46:
            preview = preview[:46] + '…'
        cue('ok')
        self.status.emit('تایپ شد · ' + preview)

    def _progress(self, percent, text):
        self.status.emit(text if text else 'در حال تبدیل گفتار به متن…')
