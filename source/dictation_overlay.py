# SPDX-License-Identifier: GPL-3.0-or-later
"""Floating dictation overlay.

A click-through, non-activating pill at the bottom of the screen that shows a
live microphone waveform while global dictation is recording, so the user gets
visual feedback without leaving the application they are typing into.
"""
from __future__ import annotations

import ctypes
from collections import deque

from PySide6.QtCore import Qt, QTimer, QRectF, QEvent
from PySide6.QtGui import QColor, QPainter, QCursor, QGuiApplication
from PySide6.QtWidgets import QWidget, QLabel, QFrame, QVBoxLayout, QHBoxLayout

import theme


class MiniWave(QWidget):
    """Rolling bar waveform driven by the recorder's instantaneous peak level."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self.setMinimumWidth(280)
        self.levels = deque(maxlen=64)
        self.colors = theme.DARK

    def set_colors(self, colors):
        self.colors = colors
        self.update()

    def push(self, level):
        try:
            v = float(level or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        self.levels.append(min(1.0, max(0.0, v)))
        self.update()

    def clear(self):
        self.levels.clear()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        vals = list(self.levels) or [0.05] * 56
        step = w / 64.0
        bar_w = max(2.0, step * 0.55)
        accent = QColor(self.colors.get('accent', '#4dd8ff'))
        idle = QColor(self.colors.get('line', 'rgba(120,140,190,70)'))
        n = len(vals)
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(vals):
            x = w - (n - i) * step + (step - bar_w) / 2.0
            if x < -bar_w or x > w:
                continue
            bh = max(3.0, v * (h - 8))
            p.setBrush(accent if v > 0.12 else idle)
            p.drawRoundedRect(QRectF(x, (h - bh) / 2.0, bar_w, bh), bar_w / 2.0, bar_w / 2.0)
        p.end()


def _opaque(color, alpha=246):
    """Force a palette colour to be almost solid so the pill stays readable."""
    color = str(color)
    if color.startswith('rgba('):
        parts = [p.strip() for p in color[5:-1].split(',')]
        return 'rgba(%s,%s,%s,%d)' % (parts[0], parts[1], parts[2], alpha)
    return color


_PTR = ctypes.c_void_p
_TOPMOST = _PTR((1 << (ctypes.sizeof(_PTR) * 8)) - 1)  # HWND_TOPMOST == (HWND)-1
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
HWND_FLAGS = 0x0001 | 0x0002 | 0x0004  # NOMOVE | NOSIZE | NOACTIVATE


def _prepare_native(hwnd):
    """Never allow the overlay to steal focus from the application being typed into."""
    try:
        user32 = ctypes.windll.user32
        ex = int(user32.GetWindowLongW(_PTR(hwnd), GWL_EXSTYLE))
        if not (ex & WS_EX_NOACTIVATE):
            user32.SetWindowLongW(_PTR(hwnd), GWL_EXSTYLE, ex | WS_EX_NOACTIVATE)
        user32.SetWindowPos(_PTR(hwnd), _TOPMOST, 0, 0, 0, 0, HWND_FLAGS)
    except Exception:
        pass


class DictationOverlay(QWidget):
    """Bottom-of-screen notification with a live waveform; never takes focus."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._mode = None
        self._dots = 0
        self._peak_provider = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        pill = QFrame()
        pill.setObjectName('pill')
        body = QVBoxLayout(pill)
        body.setContentsMargins(20, 14, 20, 14)
        body.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.dot = QLabel('●')
        self.dot.setObjectName('dot')
        self.title = QLabel('در حال ضبط…')
        self.title.setObjectName('title')
        self.hint = QLabel('کلید را رها کن تا متن تایپ شود')
        self.hint.setObjectName('hint')
        head.addWidget(self.title)
        head.addWidget(self.hint, 1)
        head.addWidget(self.dot)
        body.addLayout(head)

        self.wave = MiniWave()
        body.addWidget(self.wave)
        outer.addWidget(pill)

        self._timer = QTimer(self)
        self._timer.setInterval(38)
        self._timer.timeout.connect(self._tick)
        self.set_colors(theme.DARK)

    # ---- appearance --------------------------------------------------------
    def set_colors(self, c):
        self._colors = c
        self.wave.set_colors(c)
        panel = _opaque(c.get('panel', 'rgba(28,36,62,185)'))
        self.setStyleSheet('''
            QFrame#pill {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 %(glassTop)s,stop:1 %(glassBottom)s),
            qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 %(panel)s,stop:1 %(panel)s);
            border:1px solid %(accent)s;border-radius:20px;}
            QLabel#title {color:%(text)s;font-size:13px;font-weight:bold;background:transparent;}
            QLabel#hint {color:%(muted)s;font-size:12px;background:transparent;}
            QLabel#dot {color:%(accent)s;font-size:14px;background:transparent;}
        ''' % dict(c, panel=panel))

    def _move_to_bottom(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.adjustSize()
        x = int(geo.center().x() - self.width() / 2)
        y = int(geo.bottom() - self.height() - 14)
        self.move(max(geo.left() + 8, x), y)

    # ---- state -------------------------------------------------------------
    def show_recording(self, peak_provider=None):
        self._peak_provider = peak_provider
        self._mode = 'record'
        self._dots = 0
        self.title.setText('در حال ضبط…')
        self.hint.setText('کلید را رها کن تا متن تایپ شود')
        self.wave.clear()
        self._move_to_bottom()
        _prepare_native(int(self.winId()))
        if not self.isVisible():
            self.show()
        self.raise_()
        _prepare_native(int(self.winId()))
        self._timer.start()
        self._tick()

    def show_working(self):
        self._mode = 'work'
        self._dots = 0
        self.title.setText('در حال تبدیل…')
        self._move_to_bottom()
        _prepare_native(int(self.winId()))
        if not self.isVisible():
            self.show()
        self.raise_()
        _prepare_native(int(self.winId()))
        self._timer.start()
        self._tick()

    def hide_overlay(self):
        self._mode = None
        self._timer.stop()
        self.wave.clear()
        self.hide()

    def _tick(self):
        # Some applications re-assert their own topmost state; keep ours on top.
        self._ticks = getattr(self, '_ticks', 0) + 1
        if self._ticks % 14 == 0:
            _prepare_native(int(self.winId()))
        if self._mode == 'record':
            provider = self._peak_provider
            peak = 0.0
            if provider is not None:
                try:
                    peak = provider() or 0.0
                except Exception:
                    peak = 0.0
            self.wave.push(peak)
        elif self._mode == 'work':
            last = self.wave.levels[-1] if self.wave.levels else 0.10
            self.wave.push(last * 0.72)
            self._dots = (self._dots + 1) % 4
            self.hint.setText('صبر کن' + ' ' * 0 + '.' * self._dots)

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)
