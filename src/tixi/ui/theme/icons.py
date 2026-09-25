"""Vector icon library.

Icons are drawn with QPainter from path data instead of shipping PNG/SVG assets:
it keeps the installer small, they stay crisp at every DPI, and they pick up the
current accent colour and theme automatically.

Usage::

    icon = get_icon("mic", tokens)             # QIcon, sizes 16..256
    pixmap = icon_pixmap("play", tokens, 20)   # QPixmap
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Iterable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

from .tokens import ThemeTokens

# All paths are authored on a 24x24 grid with a stroke width of 2.
STROKE_ICONS: dict[str, list[list[tuple[float, float]]]] = {
    "home": [[(3, 11), (12, 3.5), (21, 11)], [(6, 10), (6, 20), (18, 20), (18, 10)]],
    "mic": [[(12, 3), (12, 3)], [(12, 3), (12, 14)], [(9, 15), (15, 15)]],
    "mic_rounded": [],
    "speaker": [[(4, 9), (4, 15), (8, 15), (13, 20), (13, 4), (8, 9), (4, 9)]],
    "speaker_wave": [[(5, 9), (5, 15), (9, 15), (14, 20), (14, 4), (9, 9), (5, 9)], [(17, 9), (20, 12), (17, 15)]],
    "waveform": [[(4, 10), (4, 14)], [(8, 6), (8, 18)], [(12, 3), (12, 21)], [(16, 7), (16, 17)], [(20, 10), (20, 14)]],
    "keyboard": [[(3, 7), (21, 7), (21, 17), (3, 17), (3, 7)], [(7, 11), (7, 11)], [(11, 11), (11, 11)], [(15, 11), (15, 11)], [(8, 14), (16, 14)]],
    "chat": [[(5, 5), (19, 5), (19, 15), (9, 15), (5, 19), (5, 5)]],
    "book": [[(4, 4), (11, 6), (11, 20), (4, 18), (4, 4)], [(20, 4), (13, 6), (13, 20), (20, 18), (20, 4)]],
    "folder": [[(3, 7), (10, 7), (12, 9), (21, 9), (21, 19), (3, 19), (3, 7)]],
    "history": [[(3.5, 12), (3.5, 12)], [(12, 7), (12, 12), (16, 14)], [(4, 5), (4, 10), (9, 10)]],
    "settings": [[(12, 9), (12, 9)], [(12, 3), (12, 5)], [(12, 19), (12, 21)], [(3, 12), (5, 12)], [(19, 12), (21, 12)], [(5.6, 5.6), (7, 7)], [(17, 17), (18.4, 18.4)], [(5.6, 18.4), (7, 17)], [(17, 7), (18.4, 5.6)]],
    "update": [[(12, 20), (12, 4)], [(6, 10), (12, 4), (18, 10)], [(4, 17), (4, 20), (20, 20), (20, 17)]],
    "info": [[(12, 4), (12, 4)], [(12, 11), (12, 17)]],
    "download": [[(12, 4), (12, 15)], [(7, 10), (12, 15), (17, 10)], [(4, 19), (20, 19)]],
    "upload": [[(12, 17), (12, 6)], [(7, 11), (12, 6), (17, 11)], [(4, 19), (20, 19)]],
    "play": [[(8, 5), (9.5, 5), (9.5, 19), (8, 19), (8, 5)]],
    "play_filled": [[(7, 4), (19, 12), (7, 20), (7, 4)]],
    "pause": [[(9, 5), (9, 19)], [(15, 5), (15, 19)]],
    "stop": [[(6.5, 6.5), (17.5, 6.5), (17.5, 17.5), (6.5, 17.5), (6.5, 6.5)]],
    "record": [],
    "trash": [[(4, 7), (20, 7)], [(9, 7), (9, 4), (15, 4), (15, 7)], [(6, 7), (7, 20), (17, 20), (18, 7)]],
    "copy": [[(9, 9), (20, 9), (20, 20), (9, 20), (9, 9)], [(5, 15), (4, 15), (4, 4), (15, 4), (15, 5)]],
    "close": [[(6, 6), (18, 18)], [(18, 6), (6, 18)]],
    "check": [[(5, 13), (10, 18), (19, 6)]],
    "search": [[(11, 4), (17, 10), (11, 16), (5, 10), (11, 4)], [(16, 16), (20, 20)]],
    "plus": [[(12, 5), (12, 19)], [(5, 12), (19, 12)]],
    "minus": [[(5, 12), (19, 12)]],
    "chevron_right": [[(9, 5), (16, 12), (9, 19)]],
    "chevron_left": [[(15, 5), (8, 12), (15, 19)]],
    "chevron_down": [[(5, 9), (12, 16), (19, 9)]],
    "arrow_right": [[(4, 12), (19, 12)], [(13, 6), (19, 12), (13, 18)]],
    "arrow_left": [[(20, 12), (5, 12)], [(11, 6), (5, 12), (11, 18)]],
    "external": [[(14, 5), (19, 5), (19, 10)], [(19, 5), (11, 13)], [(18, 14), (18, 19), (5, 19), (5, 6), (10, 6)]],
    "sun": [[(12, 7), (12, 17)], [(12, 12), (12, 12)], [(12, 2), (12, 4)], [(12, 20), (12, 22)], [(2, 12), (4, 12)], [(20, 12), (22, 12)], [(5, 5), (6.5, 6.5)], [(17.5, 17.5), (19, 19)], [(5, 19), (6.5, 17.5)], [(17.5, 6.5), (19, 5)]],
    "moon": [[(20, 14), (17, 19), (11, 20), (6, 16), (5, 10), (10, 4), (16, 5), (20, 14), (14, 9), (10, 11), (11, 16), (16, 17), (20, 14)]],
    "monitor": [[(3, 5), (21, 5), (21, 16), (3, 16), (3, 5)], [(9, 20), (15, 20)]],
    "palette": [[(12, 4), (18, 7), (20, 13), (16, 19), (9, 20), (4, 16), (4, 9), (12, 4)]],
    "model": [[(5, 8), (12, 5), (19, 8), (19, 16), (12, 19), (5, 16), (5, 8)], [(5, 8), (12, 11), (19, 8)], [(12, 11), (12, 19)]],
    "chip": [[(8, 8), (16, 8), (16, 16), (8, 16), (8, 8)], [(12, 3), (12, 6)], [(12, 18), (12, 21)], [(3, 12), (6, 12)], [(18, 12), (21, 12)]],
    "shield": [[(12, 3), (20, 6), (20, 12), (12, 21), (4, 12), (4, 6), (12, 3)], [(9, 12), (11.5, 14.5), (15.5, 9.5)]],
    "warning": [[(12, 3), (21, 20), (3, 20), (12, 3)], [(12, 9), (12, 14)], [(12, 17), (12, 17)]],
    "clock": [[(12, 6), (12, 12), (16, 15)], [(12, 21), (12, 21)]],
    "text": [[(5, 6), (19, 6)], [(12, 6), (12, 19)], [(9, 19), (15, 19)]],
    "edit": [[(4, 20), (4, 16), (16, 4), (20, 8), (8, 20), (4, 20)]],
    "save": [[(5, 5), (16, 5), (19, 8), (19, 19), (5, 19), (5, 5)], [(9, 5), (9, 10), (15, 10), (15, 5)]],
    "refresh": [[(19, 9), (17, 5), (12, 3), (7, 5), (5, 9), (5, 13)], [(5, 15), (7, 19), (12, 21), (17, 19), (19, 15), (19, 11)], [(17, 5), (19, 5), (19, 9)], [(5, 15), (5, 19), (7, 19)]],
    "power": [[(12, 4), (12, 12)], [(7, 7), (5, 11), (6, 16), (10, 19), (15, 18), (18, 14), (18, 9), (15, 6)]],
    "volume": [[(4, 9), (4, 15), (8, 15), (13, 20), (13, 4), (8, 9), (4, 9)], [(17, 9), (20, 12), (17, 15)]],
    "pause_mini": [[(10, 5), (10, 19)], [(14, 5), (14, 19)]],
    "statistics": [[(4, 20), (20, 20)], [(7, 20), (7, 12)], [(12, 20), (12, 7)], [(17, 20), (17, 15)]],
    "language": [[(12, 3), (12, 3)], [(4, 6), (20, 6)], [(9, 6), (9, 6)], [(6, 20), (9, 7), (12, 13), (15, 7), (18, 20)]],
    "sort": [[(7, 5), (7, 19)], [(4, 16), (7, 19), (10, 16)], [(17, 19), (17, 5)], [(14, 8), (17, 5), (20, 8)]],
    "grid": [[(4, 4), (10, 4), (10, 10), (4, 10), (4, 4)], [(14, 4), (20, 4), (20, 10), (14, 10), (14, 4)], [(4, 14), (10, 14), (10, 20), (4, 20), (4, 14)], [(14, 14), (20, 14), (20, 20), (14, 20), (14, 14)]],
    "list": [[(4, 6), (20, 6)], [(4, 12), (20, 12)], [(4, 18), (20, 18)]],
    "eye": [[(2, 12), (7, 6), (12, 5), (17, 6), (22, 12), (17, 18), (12, 19), (7, 18), (2, 12)], [(12, 9), (12, 9)]],
    "eye_off": [[(3, 4), (21, 20)], [(2, 12), (7, 6), (12, 5), (17, 6), (22, 12), (17, 18), (12, 19), (7, 18), (2, 12)]],
    "pin": [[(12, 3), (16, 7), (13, 13), (16, 20), (8, 20), (11, 13), (8, 7), (12, 3)]],
    "cut": [[(7, 4), (17, 20)], [(17, 4), (12, 13)], [(6, 18), (7.5, 16.5)], [(16.5, 16.5), (18, 18)]],
    "wand": [[(15, 4), (19, 8)], [(4, 20), (14, 10)], [(18, 13), (20, 15)], [(13, 4), (15, 6)], [(5, 12), (7, 14)]],
    "translate": [[(3, 5), (13, 5)], [(8, 5), (8, 5)], [(5, 19), (7, 7), (9, 13)], [(14, 19), (17, 10), (20, 19)], [(15.5, 16), (18.5, 16)]],
    "hotkey": [[(3, 7), (10, 7), (10, 14), (3, 14), (3, 7)], [(14, 10), (21, 10), (21, 17), (14, 17), (14, 10)]],
    "equalizer": [[(4, 8), (20, 8)], [(4, 16), (20, 16)], [(9, 5), (9, 11)], [(15, 13), (15, 19)]],
    "star": [[(12, 3.5), (14.6, 9), (20.5, 9.8), (16.2, 14), (17.3, 20), (12, 17), (6.7, 20), (7.8, 14), (3.5, 9.8), (9.4, 9), (12, 3.5)]],
    "shield_check": [[(12, 3), (20, 6), (20, 12), (12, 21), (4, 12), (4, 6), (12, 3)], [(9, 12), (11.5, 14.5), (15.5, 9.5)]],
    "cpu": [[(7, 7), (17, 7), (17, 17), (7, 17), (7, 7)], [(4, 10), (7, 10)], [(4, 14), (7, 14)], [(17, 10), (20, 10)], [(17, 14), (20, 14)], [(10, 4), (10, 7)], [(14, 4), (14, 7)], [(10, 17), (10, 20)], [(14, 17), (14, 20)]],
    "server": [[(4, 5), (20, 5), (20, 10), (4, 10), (4, 5)], [(4, 14), (20, 14), (20, 19), (4, 19), (4, 14)], [(7, 7.5), (7, 7.5)], [(7, 16.5), (7, 16.5)]],
}

# Even-sized icons for macOS/Windows menu roles.
FILLED_ICONS = {
    "play_filled",
    "record_filled",
    "dot",
    "heart",
}

_CIRCLE_ICONS = {"record", "record_filled", "dot"}
_DOT_ICONS = {"mic", "record", "record_filled", "dot", "heart"}


def available_icons() -> tuple[str, ...]:
    return tuple(sorted(STROKE_ICONS))


def _stroke_paths(name: str, size: float) -> list[QPainterPath]:
    """Build the painter paths for ``name`` scaled to ``size``."""
    scale = size / 24.0
    paths: list[QPainterPath] = []
    if name in _CIRCLE_ICONS or name in {"record", "record_filled"}:
        path = QPainterPath()
        radius = size * 0.30
        path.addEllipse(QPointF(size / 2, size / 2), radius, radius)
        paths.append(path)
    if name == "mic" or name == "mic_rounded":
        # microphone: capsule body + stand + base
        body = QPainterPath()
        body.addRoundedRect(QRectF(9 * scale, 3 * scale, 6 * scale, 11 * scale), 3 * scale, 3 * scale)
        stand = QPainterPath()
        stand.moveTo(6.2 * scale, 11.2 * scale)
        stand.arcTo(QRectF(6.2 * scale, 7.2 * scale, 11.6 * scale, 8.0 * scale), 180, -180)
        stand.moveTo(12 * scale, 15.2 * scale)
        stand.lineTo(12 * scale, 19.5 * scale)
        stand.moveTo(8.5 * scale, 19.8 * scale)
        stand.lineTo(15.5 * scale, 19.8 * scale)
        paths.extend([body, stand])
        return paths
    if name == "heart":
        heart = QPainterPath()
        heart.moveTo(size * 0.5, size * 0.82)
        heart.cubicTo(size * 0.05, size * 0.52, size * 0.12, size * 0.16, size * 0.32, size * 0.2)
        heart.cubicTo(size * 0.43, size * 0.22, size * 0.5, size * 0.33, size * 0.5, size * 0.36)
        heart.cubicTo(size * 0.5, size * 0.33, size * 0.57, size * 0.22, size * 0.68, size * 0.2)
        heart.cubicTo(size * 0.88, size * 0.16, size * 0.95, size * 0.52, size * 0.5, size * 0.82)
        paths.append(heart)
        return paths
    for polyline in STROKE_ICONS.get(name, []):
        path = QPainterPath()
        if len(polyline) == 1:
            point = QPointF(polyline[0][0] * scale, polyline[0][1] * scale)
            path.moveTo(point)
            path.lineTo(point)
        else:
            path.moveTo(QPointF(polyline[0][0] * scale, polyline[0][1] * scale))
            for x, y in polyline[1:]:
                path.lineTo(QPointF(x * scale, y * scale))
        paths.append(path)
    return paths


def _extra_shapes(name: str, size: float) -> list[QPainterPath]:
    """Filled extras that give a few icons their character."""
    scale = size / 24.0
    shapes: list[QPainterPath] = []
    if name == "settings":
        path = QPainterPath()
        path.addEllipse(QPointF(size / 2, size / 2), size * 0.16, size * 0.16)
        shapes.append(path)
    elif name == "sun":
        path = QPainterPath()
        path.addEllipse(QPointF(size / 2, size / 2), size * 0.19, size * 0.19)
        shapes.append(path)
        shapes[0] = path
    elif name == "eye":
        path = QPainterPath()
        path.addEllipse(QPointF(size / 2, size / 2), size * 0.15, size * 0.15)
        shapes.append(path)
    elif name == "clock":
        hand = QPainterPath()
        hand.moveTo(size / 2, size / 2)
        hand.lineTo(size / 2 + size * 0.16, size / 2 - size * 0.1)
        shapes.append(hand)
    elif name == "info":
        dot = QPainterPath()
        dot.addEllipse(QPointF(size / 2, size * 0.30), size * 0.055, size * 0.055)
        shapes.append(dot)
    elif name == "warning":
        dot = QPainterPath()
        dot.addEllipse(QPointF(size / 2, size * 0.79), size * 0.05, size * 0.05)
        shapes.append(dot)
    elif name == "language":
        dot = QPainterPath()
        dot.addEllipse(QPointF(11.6 * scale, 19.4 * scale), size * 0.075, size * 0.075)
        shapes.append(dot)
    return shapes


def paint_icon(
    painter: QPainter,
    name: str,
    colour: QColor,
    size: float,
    *,
    stroke_width: float = 0.0,
) -> None:
    """Draw ``name`` at ``size`` using the painter's current transform origin."""
    width = stroke_width or max(1.3, size * 0.085)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(colour)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    selected = None
    try:
        selected = painter.save()
    except Exception:  # pragma: no cover - defensive
        selected = None
    try:
        for path in _stroke_paths(name, size):
            if path.isEmpty():
                continue
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        for shape in _extra_shapes(name, size):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawPath(shape)
        if name in FILLED_ICONS:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            for path in _stroke_paths(name, size):
                painter.drawPath(path)
    finally:
        try:
            painter.restore()
        except Exception:  # pragma: no cover - defensive
            pass


def icon_pixmap(
    name: str,
    tokens: ThemeTokens | None = None,
    size: int = 20,
    *,
    colour: str | QColor | None = None,
    device_pixel_ratio: float = 2.0,
) -> QPixmap:
    """Render an icon to a high-DPI pixmap."""
    if colour is None:
        colour = tokens.text if tokens else "#F3F5F9"
    qcolour = QColor(colour) if not isinstance(colour, QColor) else colour
    ratio = max(1.0, device_pixel_ratio)
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    paint_icon(painter, name, qcolour, size * ratio)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


@lru_cache(maxsize=1024)
def _icon_cached(name: str, colour_name: str, dark: bool) -> QIcon:
    colour = QColor(colour_name)
    icon = QIcon()
    for size in (16, 18, 20, 22, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        paint_icon(painter, name, colour, size)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def get_icon(
    name: str,
    tokens: ThemeTokens | None = None,
    *,
    colour: str | QColor | None = None,
) -> QIcon:
    """Return a (cached) QIcon for ``name``.

    The icon is built for every common size so menus, toolbars and high-DPI
    screens all get a sharp result without interpolation.
    """
    if colour is None:
        colour = tokens.text if tokens else "#F3F5F9"
    if isinstance(colour, QColor):
        colour_name = colour.name()
    else:
        colour_name = QColor(colour).name()
    return _icon_cached(name, colour_name, bool(tokens.is_dark if tokens else True))


def accent_icon(name: str, tokens: ThemeTokens) -> QIcon:
    return get_icon(name, tokens, colour=tokens.accent_primary)


def icon_pixmap_pair(name: str, tokens: ThemeTokens, size: int = 18) -> tuple[QPixmap, QPixmap]:
    """Normal + accent-coloured pixmaps (used by list rows and tabs)."""
    return (
        icon_pixmap(name, tokens, size, colour=tokens.text_muted),
        icon_pixmap(name, tokens, size, colour=tokens.accent_primary),
    )


def spinner_pixmap(name: str, tokens: ThemeTokens, size: int, angle: float) -> QPixmap:
    """Rotate an icon — used by the loading indicator."""
    from PySide6.QtGui import QTransform

    base = icon_pixmap(name, tokens, size, colour=tokens.accent_primary)
    transform = QTransform().rotate(angle)
    return base.transformed(transform, Qt.TransformationMode.SmoothTransformation)


def window_icon(tokens: ThemeTokens | None = None, size: int = 256) -> QIcon:
    """Application icon: the Tixi mark — a waveform inside a rounded speech bubble."""
    colour = QColor(tokens.accent_primary if tokens else "#0A84FF")
    icon = QIcon()
    for dimension in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(dimension, dimension)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = dimension * 0.24
        background = QPainterPath()
        background.addRoundedRect(QRectF(0, 0, dimension, dimension), radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawPath(background)

        painter.setBrush(Qt.GlobalColor.white)
        pen = QPen(QColor(255, 255, 255))
        pen.setWidthF(max(1.2, dimension * 0.075))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        bars = [(0.30, 0.22), (0.42, 0.42), (0.54, 0.62), (0.66, 0.36), (0.78, 0.18)]
        for index, (x, height) in enumerate(bars):
            if index == 2:
                continue
            painter.drawLine(
                QPointF(dimension * x, dimension * (0.5 - height / 2)),
                QPointF(dimension * x, dimension * (0.5 + height / 2)),
            )
        pen.setWidthF(max(1.6, dimension * 0.095))
        painter.setPen(pen)
        painter.drawLine(
            QPointF(dimension * 0.54, dimension * (0.5 - 0.66 / 2)),
            QPointF(dimension * 0.54, dimension * (0.5 + 0.66 / 2)),
        )
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def tray_icon(tokens: ThemeTokens | None = None, recording: bool = False) -> QIcon:
    """Tray icon; turns red while dictating so the state is visible at a glance."""
    from PySide6.QtGui import QBrush

    colour = QColor("#FF453A" if recording else (tokens.accent_primary if tokens else "#0A84FF"))
    icon = QIcon()
    for dimension in (16, 22, 24, 32, 48, 64, 128):
        pixmap = QPixmap(dimension, dimension)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(colour))
        painter.drawEllipse(QRectF(0.5, 0.5, dimension - 1, dimension - 1))
        if recording:
            painter.setBrush(QBrush(QColor(255, 255, 255, 235)))
            dot = dimension * 0.28
            painter.drawEllipse(QRectF((dimension - dot) / 2, (dimension - dot) / 2, dot, dot))
        else:
            painter.setPen(QPen(QColor(255, 255, 255, 240), max(1.1, dimension * 0.1)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for index, factor in enumerate((0.22, 0.42, 0.62)):
                height = dimension * factor
                x = dimension * (0.32 + index * 0.18)
                painter.drawLine(
                    QPointF(x, (dimension - height) / 2),
                    QPointF(x, (dimension + height) / 2),
                )
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def flag_pixmap(language: str, size: int = 18) -> QPixmap:
    """Very small language chips (not flags — letters, to avoid false claims)."""
    letters = {"fa": "فا", "en": "EN", "ar": "ع", "tr": "TR", "fr": "FR", "de": "DE", "ru": "RU", "es": "ES"}
    text = letters.get((language or "").lower()[:2], (language or "??").upper()[:2])
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#FFFFFF"))
    font = painter.font()
    font.setPointSizeF(size * 0.42)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return pixmap


def logo_pixmap(tokens: ThemeTokens, height: int = 34) -> QPixmap:
    """Wordmark used in the sidebar: mark + "Tixi Voice"."""
    from PySide6.QtGui import QFont

    width = int(height * 5.2)
    pixmap = QPixmap(width * 2, height * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2.0, 2.0)

    mark_size = height * 0.86
    mark = QPainterPath()
    mark.addRoundedRect(QRectF(0, height * 0.07, mark_size, mark_size), mark_size * 0.26, mark_size * 0.26)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(tokens.accent_primary))
    painter.drawPath(mark)
    painter.setPen(QPen(QColor("#FFFFFF"), max(1.0, mark_size * 0.08)))
    for index, factor in enumerate((0.26, 0.48, 0.68, 0.4)):
        bar_height = mark_size * factor
        x = mark_size * (0.26 + index * 0.19)
        painter.drawLine(
            QPointF(x, height * 0.07 + mark_size / 2 - bar_height / 2),
            QPointF(x, height * 0.07 + mark_size / 2 + bar_height / 2),
        )

    font = QFont()
    font.setPointSizeF(height * 0.44)
    font.setWeight(QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor(tokens.text))
    painter.drawText(
        QRectF(mark_size + height * 0.28, 0, width - mark_size - height * 0.28, height),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        "Tixi Voice",
    )
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return pixmap


def status_pixmap(status: str, tokens: ThemeTokens, size: int = 12) -> QPixmap:
    """Coloured status dot for model/list rows."""
    colour = {
        "ok": tokens.success,
        "ready": tokens.success,
        "installed": tokens.success,
        "warning": tokens.warning,
        "partial": tokens.warning,
        "downloading": tokens.accent_primary,
        "update": tokens.info,
        "error": tokens.danger,
        "missing": tokens.text_faint,
        "not_installed": tokens.text_faint,
    }.get(status, tokens.text_faint)
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawEllipse(QRectF(1, 1, size * 2 - 2, size * 2 - 2))
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return pixmap


def waveform_pixmap(tokens: ThemeTokens, width: int, height: int, levels: Iterable[float]) -> QPixmap:
    """Static waveform preview used by the history and transcription lists."""
    levels = list(levels)
    pixmap = QPixmap(width * 2, height * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2.0, 2.0)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(tokens.accent_primary))
    count = max(1, len(levels))
    bar_width = max(1.0, (width - (count - 1)) / count)
    for index, level in enumerate(levels):
        amplitude = max(0.05, min(1.0, float(level))) * height
        painter.drawRoundedRect(
            QRectF(index * (bar_width + 1), (height - amplitude) / 2, bar_width, amplitude),
            bar_width / 2,
            bar_width / 2,
        )
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return pixmap


def polyline_pixmap(
    tokens: ThemeTokens,
    values: list[float],
    width: int,
    height: int,
    *,
    colour: str | None = None,
    fill: bool = True,
) -> QPixmap:
    """Sparkline used by the Home dashboard statistics."""
    pixmap = QPixmap(width * 2, height * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2.0, 2.0)
    stroke = QColor(colour or tokens.accent_primary)
    if not values or len(values) < 2:
        painter.end()
        return pixmap
    maximum = max(values) or 1.0
    minimum = min(min(values), 0.0)
    span = max(1e-6, maximum - minimum)
    points = [
        QPointF(
            index * (width / (len(values) - 1)),
            height - ((value - minimum) / span) * (height - 4) - 2,
        )
        for index, value in enumerate(values)
    ]
    if fill:
        area = QPainterPath()
        area.moveTo(points[0].x(), height)
        for point in points:
            area.lineTo(point)
        area.lineTo(points[-1].x(), height)
        area.closeSubpath()
        gradient_colour = QColor(stroke)
        gradient_colour.setAlpha(70)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient_colour)
        painter.drawPath(area)
    pen = QPen(stroke)
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPolyline(QPolygonF(points))
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return pixmap
