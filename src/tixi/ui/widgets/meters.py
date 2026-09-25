"""Visualisers: live level meters, waveform views, progress rings, sparklines."""

from __future__ import annotations

import math
from typing import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme.tokens import ThemeTokens


class LevelMeter(QWidget):
    """Live input level with peak hold — shows *real* microphone amplitude."""

    def __init__(self, parent: QWidget | None = None, *, bars: int = 32, orientation: str = "horizontal") -> None:
        super().__init__(parent)
        self._levels: list[float] = [0.0] * bars
        self._bars = bars
        self._orientation = orientation
        self._peak = 0.0
        self._peak_hold = 0
        self._tokens: ThemeTokens | None = None
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def push(self, level: float) -> None:
        value = max(0.0, min(1.0, float(level)))
        self._levels.append(value)
        if len(self._levels) > self._bars:
            self._levels = self._levels[-self._bars :]
        if value >= self._peak:
            self._peak = value
            self._peak_hold = 18
        elif self._peak_hold > 0:
            self._peak_hold -= 1
        else:
            self._peak = max(0.0, self._peak - 0.03)
        self.update()

    def reset(self) -> None:
        self._levels = [0.0] * self._bars
        self._peak = 0.0
        self.update()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt naming
        tokens = self._tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        height = self.height()
        background = QColor(tokens.surface_alt if tokens else "#22262F")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(0, 0, width, height), 6, 6)

        count = max(1, len(self._levels))
        spacing = 2.0
        bar_width = max(1.5, (width - spacing * (count - 1)) / count)
        accent = QColor(tokens.accent_primary if tokens else "#0A84FF")
        secondary = QColor(tokens.accent_secondary if tokens else "#4FC3F7")
        warn = QColor(tokens.warning if tokens else "#FFB020")
        for index, level in enumerate(self._levels):
            bar_height = max(2.0, level * (height - 6))
            x = index * (bar_width + spacing)
            y = height - 3 - bar_height
            gradient = QLinearGradient(x, y, x, height - 3)
            if level > 0.92:
                gradient.setColorAt(0.0, warn)
                gradient.setColorAt(1.0, accent)
            else:
                gradient.setColorAt(0.0, secondary)
                gradient.setColorAt(1.0, accent)
            painter.setBrush(gradient)
            path = QPainterPath()
            path.addRoundedRect(QRectF(x, y, bar_width, bar_height), bar_width / 2, bar_width / 2)
            painter.drawPath(path)
        if self._peak > 0.01:
            peak_x = (self._peak) * (width - 3) + 1.5
            painter.setPen(QPen(QColor(tokens.danger if self._peak > 0.97 else (tokens.text if tokens else "#FFFFFF")), 1.4))
            painter.drawLine(QPointF(peak_x, 3), QPointF(peak_x, height - 3))
        painter.end()


class WaveformView(QWidget):
    """Waveform preview with a playhead; clicking seeks (used by playback and STT)."""

    seek_requested = Signal(float)   # seconds
    playhead_moved = Signal(float)

    def __init__(self, parent: QWidget | None = None, *, colour_role: str = "accent") -> None:
        super().__init__(parent)
        self.setObjectName("WaveformPreview")
        self.setMinimumHeight(74)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tokens: ThemeTokens | None = None
        self._peaks: list[float] = []
        self._raw_peaks: list[float] = []
        self._duration = 0.0
        self._position = 0.0
        self._selection: tuple[float, float] | None = None
        self._segments: list[tuple[float, float]] = []
        self._colour_role = colour_role
        self._empty_text = "No audio loaded"

    # -- data ---------------------------------------------------------------
    def set_peaks(self, peaks: Sequence[float], duration: float) -> None:
        self._raw_peaks = [max(0.0, min(1.0, float(p))) for p in peaks]
        self._duration = max(0.0, float(duration))
        self._rebin()
        self.update()

    def clear(self) -> None:
        self._raw_peaks = []
        self._peaks = []
        self._duration = 0.0
        self._position = 0.0
        self._segments = []
        self.update()

    def set_position(self, seconds: float) -> None:
        self._position = max(0.0, min(self._duration if self._duration else 0.0, float(seconds)))
        self.update()

    def set_segments(self, segments: Sequence[tuple[float, float]]) -> None:
        """Highlight recognised speech regions (start, end) in seconds."""
        self._segments = [(float(a), float(b)) for a, b in segments]
        self.update()

    def set_selection(self, start: float | None, end: float | None = None) -> None:
        if start is None:
            self._selection = None
        else:
            self._selection = (start, end if end is not None else self._duration)
        self.update()

    def set_empty_text(self, text: str) -> None:
        self._empty_text = text
        self.update()

    def _rebin(self) -> None:
        """Reduce the peak array to roughly one peak per 3 device pixels."""
        target = max(40, min(1200, int(self.width() / 3))) if self.width() > 30 else 200
        peaks = self._raw_peaks
        if not peaks:
            self._peaks = []
            return
        if len(peaks) <= target:
            self._peaks = peaks
            return
        step = len(peaks) / target
        binned: list[float] = []
        for index in range(target):
            start = int(index * step)
            end = max(start + 1, int((index + 1) * step))
            window = peaks[start:end]
            binned.append(max(window) if window else 0.0)
        self._peaks = binned

    def resizeEvent(self, event: object) -> None:  # noqa: N802 - Qt naming
        self._rebin()
        super().resizeEvent(event)  # type: ignore[arg-type]

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    # -- interaction --------------------------------------------------------
    def mouseReleaseEvent(self, event: object) -> None:  # noqa: N802 - Qt naming
        if self._duration <= 0 or self._peaks == []:
            return
        position = getattr(event, "position", None)
        if position is None:
            return
        x = position().x()
        ratio = max(0.0, min(1.0, x / max(1, self.width())))
        seconds = ratio * self._duration
        self._position = seconds
        self.update()
        self.seek_requested.emit(seconds)

    # -- painting -----------------------------------------------------------
    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt naming
        tokens = self._tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        height = self.height()
        background = QColor(tokens.background_alt if tokens else "#171A21")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(0, 0, width, height), 8, 8)

        if not self._peaks or self._duration <= 0:
            painter.setPen(QColor(tokens.text_faint if tokens else "#6C7486"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            painter.end()
            return

        accent = QColor(tokens.accent_primary if tokens else "#0A84FF")
        muted = QColor(tokens.text_muted if tokens else "#9AA3B2")
        muted.setAlpha(150)
        playhead_x = (self._position / self._duration) * width if self._duration else 0.0

        # speech regions behind the waveform
        if self._segments:
            highlight = QColor(tokens.accent_primary)
            highlight.setAlpha(38)
            painter.setBrush(highlight)
            for start, end in self._segments:
                x1 = (start / self._duration) * width
                x2 = (end / self._duration) * width
                painter.drawRect(QRectF(x1, 0, max(1.0, x2 - x1), height))

        count = len(self._peaks)
        spacing = 1.0
        bar_width = max(1.0, (width - spacing * (count - 1)) / count)
        for index, level in enumerate(self._peaks):
            amplitude = max(2.0, level * (height - 16))
            x = index * (bar_width + spacing)
            y = (height - amplitude) / 2
            painter.setBrush(accent if x <= playhead_x else muted)
            painter.drawRoundedRect(QRectF(x, y, bar_width, amplitude), bar_width / 2, bar_width / 2)

        # centre line
        line = QColor(tokens.border if tokens else "#2A3040")
        painter.setPen(QPen(line, 1))
        centre = height / 2
        painter.drawLine(QPointF(0, centre), QPointF(width, centre))

        # playhead
        if self._duration:
            painter.setPen(QPen(QColor(tokens.text if tokens else "#FFFFFF"), 1.6))
            painter.drawLine(QPointF(playhead_x, 0), QPointF(playhead_x, height))
        painter.end()


class ProgressRing(QWidget):
    """Circular progress indicator (model downloads, busy states)."""

    def __init__(self, parent: QWidget | None = None, *, size: int = 64, thickness: int = 6) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._indeterminate = False
        self._tokens: ThemeTokens | None = None
        self._thickness = thickness
        self._caption = ""
        self.setFixedSize(size, size)
        from PySide6.QtCore import QTimer

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self._angle = 0.0

    def set_progress(self, value: float, *, caption: str = "") -> None:
        self._indeterminate = False
        self._timer.stop()
        self._value = max(0.0, min(1.0, float(value)))
        if caption:
            self._caption = caption
        self.update()

    def set_indeterminate(self, active: bool = True) -> None:
        self._indeterminate = active
        if active:
            self._timer.start()
        else:
            self._timer.stop()
            self.update()

    def set_caption(self, text: str) -> None:
        self._caption = text
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle + 6.0) % 360.0
        self.update()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt naming
        tokens = self._tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(
            self._thickness / 2 + 1,
            self._thickness / 2 + 1,
            self.width() - self._thickness - 2,
            self.height() - self._thickness - 2,
        )
        track = QColor(tokens.surface_alt if tokens else "#252B3B")
        painter.setPen(QPen(track, self._thickness))
        painter.drawArc(rect, 0, 360 * 16)
        accent = QColor(tokens.accent_primary if tokens else "#0A84FF")
        painter.setPen(QPen(accent, self._thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self._indeterminate:
            start = int(-self._angle * 16)
            painter.drawArc(rect, start, int(100 * 16))
        else:
            painter.drawArc(rect, 90 * 16, int(-self._value * 360 * 16))
        if self._caption:
            painter.setPen(QColor(tokens.text if tokens else "#FFFFFF"))
            font = painter.font()
            font.setPointSizeF(max(7.0, self.width() * 0.16))
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._caption)
        painter.end()


class Sparkline(QWidget):
    """Minimal activity chart used by the dashboard."""

    def __init__(self, parent: QWidget | None = None, *, height: int = 46) -> None:
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._values: list[float] = []
        self._labels: list[str] = []
        self._tokens: ThemeTokens | None = None

    def set_values(self, values: Sequence[float], labels: Sequence[str] = ()) -> None:
        self._values = [float(v) for v in values]
        self._labels = list(labels)
        self.update()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt naming
        tokens = self._tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._values:
            painter.setPen(QColor(tokens.text_faint if tokens else "#6C7486"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No activity yet")
            painter.end()
            return
        width = self.width()
        height = self.height() - 4
        maximum = max(self._values) or 1.0
        minimum = min(min(self._values), 0.0)
        span = max(1e-6, maximum - minimum)
        points = [
            QPointF(
                (index / max(1, len(self._values) - 1)) * width,
                2 + height - ((value - minimum) / span) * (height - 6),
            )
            for index, value in enumerate(self._values)
        ]
        if len(points) < 2:
            points = [points[0], QPointF(width, points[0].y())]
        accent = QColor(tokens.accent_primary if tokens else "#0A84FF")
        area = QPainterPath()
        area.moveTo(points[0].x(), height)
        for point in points:
            area.lineTo(point)
        area.lineTo(points[-1].x(), height)
        area.closeSubpath()
        gradient = QLinearGradient(0, 0, 0, height)
        top = QColor(accent)
        top.setAlpha(90)
        bottom = QColor(accent)
        bottom.setAlpha(0)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawPath(area)
        pen = QPen(accent, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF(points))
        painter.end()


class VolumeBar(QWidget):
    """Compact horizontal volume readout with a dB label."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._level = 0.0
        self._tokens: ThemeTokens | None = None

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, float(level)))
        self.update()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt naming
        tokens = self._tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 2, self.width(), self.height() - 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.surface_alt if tokens else "#252B3B"))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        filled = QRectF(rect)
        filled.setWidth(rect.width() * self._level)
        colour = QColor(tokens.success if tokens else "#34C759")
        if self._level > 0.88:
            colour = QColor(tokens.danger if tokens else "#FF453A")
        elif self._level > 0.7:
            colour = QColor(tokens.warning if tokens else "#FFB020")
        painter.setBrush(colour)
        painter.drawRoundedRect(filled, rect.height() / 2, rect.height() / 2)
        painter.end()


def db_to_level(db: float, *, floor: float = -60.0) -> float:
    """Convert a dBFS value to 0..1 for the meters."""
    if db <= floor:
        return 0.0
    return max(0.0, min(1.0, (db - floor) / -floor))


def level_to_db(level: float, *, floor: float = -60.0) -> float:
    return floor + max(0.0, min(1.0, level)) * -floor


def format_db(db: float) -> str:
    if db <= -60:
        return "-∞ dB"
    return f"{db:+.1f} dB"
