"""Floating recording indicator and insertion-fallback window.

The overlay must never steal focus from the application the user is typing in,
so it uses ``Qt.WindowDoesNotAcceptFocus`` together with the ``Tool`` and
``FramelessWindowHint`` window flags, and it is shown with ``show()`` rather
than ``activateWindow()``.  On Windows we additionally set
``WS_EX_NOACTIVATE`` so clicking the cancel button does not move the caret out
of the target application.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..app.logging_config import get_logger
from ..app.paths import IS_WINDOWS

log = get_logger("tixi.voice.overlay")

OVERLAY_POSITIONS = (
    "bottom_center",
    "bottom_right",
    "top_center",
    "top_right",
    "near_cursor",
)


class WaveformBars(QWidget):
    """A small, cheap live level visualiser (no audio data copying)."""

    def __init__(self, bars: int = 28, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._levels: list[float] = [0.0] * bars
        self._bars = bars
        self._accent = QColor(10, 132, 255)
        self.setFixedHeight(34)
        self.setMinimumWidth(120)

    def set_accent(self, colour: str | QColor) -> None:
        self._accent = QColor(colour)

    def set_level(self, level: float) -> None:
        self._levels.append(max(0.0, min(1.0, level)))
        if len(self._levels) > self._bars:
            self._levels = self._levels[-self._bars :]
        self.update()

    def set_levels(self, levels: Sequence[float]) -> None:
        self._levels = list(levels)[-self._bars :]
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        height = self.height()
        count = max(1, len(self._levels))
        spacing = 2
        bar_width = max(2.0, (width - spacing * (count - 1)) / count)
        for index, level in enumerate(self._levels):
            amplitude = max(0.06, level)
            bar_height = max(3.0, amplitude * height)
            x = index * (bar_width + spacing)
            y = (height - bar_height) / 2
            colour = QColor(self._accent)
            colour.setAlphaF(0.35 + 0.65 * min(1.0, amplitude * 1.4))
            painter.setBrush(colour)
            painter.setPen(Qt.PenStyle.NoPen)
            path = QPainterPath()
            path.addRoundedRect(x, y, bar_width, bar_height, bar_width / 2, bar_width / 2)
            painter.drawPath(path)
        painter.end()


class RecordingOverlay(QWidget):
    """Compact always-on-top indicator shown while dictating."""

    cancel_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, _overlay_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("Tixi Voice — recording")
        self.setFixedSize(QSize(286, 96))
        self._position = "bottom_center"
        self._opacity = 0.94
        self._accent = "#0A84FF"
        self._dark = True
        self._seconds = 0.0
        self._state_text = "Listening…"
        self._language = "fa"
        self._mode_label = "Push to talk"
        self._build_ui()
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(160)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._blink = QTimer(self)
        self._blink.setInterval(500)
        self._blink.timeout.connect(self._toggle_dot)

    # -- construction -------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._dot = QLabel("●")
        self._dot.setFixedWidth(12)
        self._title = QLabel(self._state_text)
        font = QFont()
        font.setPointSize(10)
        font.setWeight(QFont.Weight.DemiBold)
        self._title.setFont(font)
        self._timer = QLabel("0:00")
        self._timer.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._timer.setFont(font)
        top.addWidget(self._dot)
        top.addWidget(self._title, 1)
        top.addWidget(self._timer)
        layout.addLayout(top)

        self._waveform = WaveformBars(parent=self)
        layout.addWidget(self._waveform)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self._info = QLabel(f"{self._language.upper()} · {self._mode_label}")
        small = QFont()
        small.setPointSize(8)
        self._info.setFont(small)
        self._cancel = QPushButton("Cancel  Esc")
        self._cancel.setCursor(Qt.CursorShape.ArrowCursor)
        self._cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cancel.setFixedHeight(22)
        self._cancel.clicked.connect(self.cancel_requested.emit)
        bottom.addWidget(self._info, 1)
        bottom.addWidget(self._cancel)
        layout.addLayout(bottom)

    # -- configuration ------------------------------------------------------
    def configure(
        self,
        *,
        position: str = "bottom_center",
        opacity: float = 0.94,
        accent: str = "#0A84FF",
        dark: bool = True,
    ) -> None:
        self._position = position if position in OVERLAY_POSITIONS else "bottom_center"
        self._opacity = max(0.4, min(1.0, opacity))
        self._accent = accent
        self._dark = dark
        self._dot.setStyleSheet(f"color: {accent}; font-size: 14px;")
        self._waveform.set_accent(accent)
        self._timer.setStyleSheet("color: %s;" % ("#ffffff" if dark else "#111111"))
        self._title.setStyleSheet("color: %s;" % ("#ffffff" if dark else "#111111"))
        self._info.setStyleSheet(
            "color: %s;" % ("rgba(255,255,255,0.65)" if dark else "rgba(0,0,0,0.55)")
        )
        self._cancel.setStyleSheet(self._button_style())
        self.reposition()

    def set_language(self, language: str) -> None:
        self._language = (language or "").upper()
        self._refresh_info()

    def set_mode(self, label: str) -> None:
        self._mode_label = label
        self._refresh_info()

    def _refresh_info(self) -> None:
        self._info.setText(f"{self._language} · {self._mode_label}")

    def _button_style(self) -> str:
        if self._dark:
            return (
                "QPushButton { background: rgba(255,255,255,0.12); color: #ffffff;"
                " border: 1px solid rgba(255,255,255,0.18); border-radius: 11px; padding: 0 10px;"
                " font-size: 11px; }"
                "QPushButton:hover { background: rgba(255,80,80,0.35); }"
            )
        return (
            "QPushButton { background: rgba(0,0,0,0.06); color: #111111;"
            " border: 1px solid rgba(0,0,0,0.12); border-radius: 11px; padding: 0 10px;"
            " font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,80,80,0.25); }"
        )

    # -- state --------------------------------------------------------------
    def set_state_text(self, text: str, *, recording: bool = True) -> None:
        self._state_text = text
        self._title.setText(text)
        self._timer.setVisible(recording)
        self._dot.setVisible(recording)
        if not recording:
            self._blink.stop()
            self._dot.setText("●")

    def set_level(self, level: float, elapsed: float) -> None:
        self._waveform.set_level(level)
        self._seconds = elapsed
        minutes, seconds = divmod(int(elapsed), 60)
        self._timer.setText(f"{minutes}:{seconds:02d}")

    def _toggle_dot(self) -> None:
        self._dot.setText("●" if self._dot.text() != "●" else "○")

    # -- geometry -----------------------------------------------------------
    def reposition(self) -> None:
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        if screen is None:  # pragma: no cover - headless
            return
        area: QRect = screen.availableGeometry()
        margin = 24
        if self._position == "bottom_center":
            x = area.left() + (area.width() - self.width()) // 2
            y = area.bottom() - self.height() - margin
        elif self._position == "bottom_right":
            x = area.right() - self.width() - margin
            y = area.bottom() - self.height() - margin
        elif self._position == "top_center":
            x = area.left() + (area.width() - self.width()) // 2
            y = area.top() + margin + 8
        elif self._position == "top_right":
            x = area.right() - self.width() - margin
            y = area.top() + margin + 8
        else:  # near_cursor
            cursor = QGuiApplication.primaryScreen().cursor().pos() if QGuiApplication.primaryScreen() else area.center()
            x = min(max(area.left(), cursor.x() - self.width() // 2), area.right() - self.width())
            y = min(max(area.top(), cursor.y() + 24), area.bottom() - self.height())
        self.move(QPoint(int(x), int(y)))

    # -- show/hide ----------------------------------------------------------
    def show_recording(self, *, state_text: str = "Listening…") -> None:
        self.set_state_text(state_text, recording=True)
        self.reposition()
        self.setWindowOpacity(0.0)
        self.show()
        self._no_activate()
        self._blink.start()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(self._opacity)
        self._fade.start()

    def hide_overlay(self) -> None:
        self._blink.stop()
        if not self.isVisible():
            return
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self._hide_now)
        self._fade.start()

    def _hide_now(self) -> None:
        with_suppress = lambda: None  # noqa: E731 - tiny local helper
        with_suppress()
        try:
            self._fade.finished.disconnect(self._hide_now)
        except (RuntimeError, TypeError):
            pass
        self.hide()

    def _no_activate(self) -> None:
        """Make sure Windows never gives this window the focus."""
        if not IS_WINDOWS:
            return
        try:
            import ctypes

            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not set the no-activate window style", extra={"event": "overlay_style_failed", "error": str(exc)})

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        background = QColor(28, 28, 32, 232) if self._dark else QColor(250, 250, 252, 236)
        painter.setBrush(background)
        border = QColor(self._accent)
        border.setAlpha(140)
        painter.setPen(QPen(border, 1.2))
        painter.drawRoundedRect(rect, 16, 16)
        painter.end()


class InsertionFallbackWindow(QWidget):
    """Shown when text could not be inserted automatically.

    Requirements met here: the recognised text is presented in an *editable*
    window with **Copy** and **Insert** actions, the reason is explained, and no
    text is ever lost.
    """

    insert_again_requested = Signal(str)
    copy_requested = Signal(str)
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tixi Voice — recognised text")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.resize(520, 320)
        self._accent = "#0A84FF"
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("The text could not be inserted automatically")
        font = QFont()
        font.setPointSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        title.setFont(font)
        layout.addWidget(title)

        self._reason = QLabel("")
        self._reason.setWordWrap(True)
        self._reason.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(self._reason)

        self._editor = QTextEdit()
        self._editor.setAcceptRichText(False)
        self._editor.setPlaceholderText("The recognised text appears here and can be edited before inserting.")
        editor_font = QFont()
        editor_font.setPointSize(11)
        self._editor.setFont(editor_font)
        layout.addWidget(self._editor, 1)

        buttons = QHBoxLayout()
        self._copy = QPushButton("Copy")
        self._insert = QPushButton("Insert into the previous window")
        self._close = QPushButton("Close")
        self._copy.clicked.connect(self._on_copy)
        self._insert.clicked.connect(self._on_insert)
        self._close.clicked.connect(self.close)
        buttons.addStretch(1)
        buttons.addWidget(self._copy)
        buttons.addWidget(self._insert)
        buttons.addWidget(self._close)
        layout.addLayout(buttons)

        self._hint = QLabel(
            "Tip: if the target application keeps refusing synthetic input, paste with Ctrl+V "
            "after pressing Copy, or run Tixi Voice as administrator to dictate into elevated apps."
        )
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: palette(mid); font-size: 10px;")
        layout.addWidget(self._hint)

    # -- api ----------------------------------------------------------------
    def present(self, text: str, reason: str, *, accent: str = "#0A84FF") -> None:
        self._accent = accent
        self._editor.setPlainText(text)
        self._reason.setText(reason)
        self._insert.setStyleSheet(
            f"QPushButton {{ background: {accent}; color: white; border-radius: 8px; padding: 6px 14px; }}"
        )
        self.show()
        self.raise_()
        self.activateWindow()
        self._editor.setFocus()

    def text(self) -> str:
        return self._editor.toPlainText()

    def _on_copy(self) -> None:
        payload = self._editor.toPlainText()
        clipboard = QApplication.clipboard()
        clipboard.setText(payload)
        self.copy_requested.emit(payload)
        self._reason.setText("Copied to the clipboard — press Ctrl+V in the target application.")

    def _on_insert(self) -> None:
        payload = self._editor.toPlainText()
        self.insert_again_requested.emit(payload)

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        self.closed.emit()
        super().closeEvent(event)


def _overlay_flags() -> Qt.WindowType:
    return (
        Qt.WindowType.Tool
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.WindowDoesNotAcceptFocus
    )


@dataclass
class OverlayController:
    """Glue between the voice-typing controller and the Qt overlay widgets."""

    overlay: RecordingOverlay | None = None
    fallback: InsertionFallbackWindow | None = None
    on_cancel: Callable[[], None] | None = None

    def ensure(self, parent: QWidget | None = None) -> RecordingOverlay:
        if self.overlay is None:
            self.overlay = RecordingOverlay(parent)
            if self.on_cancel is not None:
                self.overlay.cancel_requested.connect(self.on_cancel)
        return self.overlay

    def show_recording(self, *, state_text: str, language: str, mode_label: str, config: Any) -> None:
        overlay = self.ensure()
        overlay.configure(
            position=getattr(config, "overlay_position", "bottom_center"),
            opacity=getattr(config, "overlay_opacity", 0.94),
            accent=getattr(config, "accent", "#0A84FF"),
            dark=getattr(config, "dark", True),
        ) if config is not None else None
        overlay.set_language(language)
        overlay.set_mode(mode_label)
        overlay.show_recording(state_text=state_text)

    def update_level(self, level: float, elapsed: float) -> None:
        if self.overlay is not None and self.overlay.isVisible():
            self.overlay.set_level(level, elapsed)

    def set_state(self, text: str, *, recording: bool) -> None:
        if self.overlay is not None:
            self.overlay.set_state_text(text, recording=recording)
            if not recording:
                self.overlay.hide_overlay()

    def hide(self) -> None:
        if self.overlay is not None:
            self.overlay.hide_overlay()

    def show_fallback(self, text: str, reason: str, *, accent: str = "#0A84FF") -> None:
        if self.fallback is None:
            self.fallback = InsertionFallbackWindow()
        self.fallback.present(text, reason, accent=accent)
