"""Small reusable building blocks used by every page.

All of these read colours from the active :class:`ThemeTokens` and repaint
themselves when the theme changes, so they never need per-page styling.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..theme.icons import get_icon, icon_pixmap
from ..theme.tokens import ThemeTokens


class ThemedWidget(QWidget):
    """Base class that keeps a reference to the current tokens and repaints."""

    def __init__(self, parent: QWidget | None = None, *, tokens: ThemeTokens | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        if tokens is not None:
            self._apply_tokens(tokens)

    @property
    def tokens(self) -> ThemeTokens | None:
        return self._tokens

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        """Called by the main window whenever the theme changes."""
        self._tokens = tokens
        self._apply_tokens(tokens)
        self.update()

    def _apply_tokens(self, tokens: ThemeTokens) -> None:  # pragma: no cover - overridden
        return

    def _fp(self, points: float, *, weight: int = 400) -> QFont:
        font = self.font()
        font.setPointSizeF(points)
        font.setWeight(QFont.Weight(weight))
        return font


def h_line(tokens: ThemeTokens | None = None, *, parent: QWidget | None = None) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("Separator")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line


def v_line(parent: QWidget | None = None) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("VSeparator")
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFixedWidth(1)
    line.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
    return line


def elide_middle(text: str, limit: int = 48) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    keep = max(4, (limit - 1) // 2)
    return f"{text[:keep]}…{text[-keep:]}"


def card(parent: QWidget | None = None, *, object_name: str = "Card", glass: bool = False) -> QFrame:
    frame = QFrame(parent)
    frame.setObjectName("GlassCard" if glass else object_name)
    return frame


class Card(QFrame):
    """A standard surface with padding and an optional click signal."""

    clicked = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        padding: int = 16,
        spacing: int = 10,
        interactive: bool = False,
        glass: bool = False,
        object_name: str = "Card",
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setProperty("card", "true")
        self.setProperty("interactive", "true" if interactive else "false")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(padding, padding, padding, padding)
        self._layout.setSpacing(spacing)
        if interactive:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout: Any, stretch: int = 0) -> Any:
        self._layout.addLayout(layout, stretch)
        return layout

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if self.property("interactive") == "true" and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def set_shadow(self, tokens: ThemeTokens, *, blur: int = 28, offset: int = 4, alpha: float = 0.35) -> None:
        effect = QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(blur)
        colour = QColor(tokens.shadow)
        colour.setAlphaF(min(1.0, alpha if tokens.is_dark else alpha * 0.5))
        effect.setColor(colour)
        effect.setOffset(0, offset)
        self.setGraphicsEffect(effect)


class SectionHeader(QWidget):
    """Title + optional subtitle, with an optional trailing widget."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        icon_name: str = "",
        upper: bool = False,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._icon = QLabel()
        self._icon.setVisible(bool(icon_name))
        self._icon_name = icon_name
        layout.addWidget(self._icon)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        self._title = QLabel(title)
        self._title.setObjectName("SectionTitle" if upper else "H3")
        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("Caption")
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))
        text_column.addWidget(self._title)
        text_column.addWidget(self._subtitle)
        layout.addLayout(text_column, 1)
        self._trailing = QHBoxLayout()
        self._trailing.setContentsMargins(0, 0, 0, 0)
        self._trailing.setSpacing(6)
        layout.addLayout(self._trailing)

    def title_widget(self) -> QLabel:
        return self._title

    def subtitle_widget(self) -> QLabel:
        return self._subtitle

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def add_trailing(self, widget: QWidget) -> QWidget:
        self._trailing.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return widget

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        if self._icon_name:
            self._icon.setPixmap(icon_pixmap(self._icon_name, tokens, 16, colour=tokens.accent_primary))


class StatTile(Card):
    """A compact metric tile for the dashboard."""

    def __init__(
        self,
        title: str,
        value: str = "—",
        parent: QWidget | None = None,
        *,
        icon_name: str = "",
        caption: str = "",
    ) -> None:
        super().__init__(parent, padding=14, spacing=4, object_name="StatTile")
        self._icon_name = icon_name
        header = QHBoxLayout()
        header.setSpacing(6)
        self._icon = QLabel()
        self._icon.setVisible(bool(icon_name))
        self._title = QLabel(title)
        self._title.setObjectName("MetricLabel")
        header.addWidget(self._icon)
        header.addWidget(self._title, 1)
        self.body().addLayout(header)
        self._value = QLabel(value)
        self._value.setObjectName("MetricValue")
        self.body().addWidget(self._value)
        self._caption = QLabel(caption)
        self._caption.setObjectName("CaptionFaint")
        self._caption.setVisible(bool(caption))
        self.body().addWidget(self._caption)

    def set_value(self, value: str, caption: str = "") -> None:
        self._value.setText(value)
        if caption:
            self._caption.setText(caption)
            self._caption.setVisible(True)

    def set_accented(self, tokens: ThemeTokens, colour: str) -> None:
        self._value.setStyleSheet(f"color: {colour};")

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        if self._icon_name:
            self._icon.setPixmap(icon_pixmap(self._icon_name, tokens, 15, colour=tokens.accent_primary))


class InfoBanner(QFrame):
    """Inline message with a severity, used instead of modal popups."""

    action_clicked = Signal()

    def __init__(
        self,
        message: str = "",
        *,
        severity: str = "info",
        parent: QWidget | None = None,
        action_text: str = "",
        dismissible: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setProperty("severity", severity)
        self._severity = severity
        self._icon_name = {
            "info": "info",
            "success": "check",
            "warning": "warning",
            "error": "warning",
        }.get(severity, "info")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(9)
        self._icon = QLabel()
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self._label = QLabel(message)
        self._label.setWordWrap(True)
        layout.addWidget(self._label, 1)
        self._action = QLabel(action_text)
        self._action.setVisible(bool(action_text))
        self._action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action.setStyleSheet("text-decoration: underline; font-weight: 600;")
        self._action.mouseReleaseEvent = self._on_action  # type: ignore[method-assign]
        layout.addWidget(self._action, 0, Qt.AlignmentFlag.AlignTop)
        self._close = QLabel("✕")
        self._close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close.setVisible(dismissible)
        self._close.mouseReleaseEvent = self._on_close  # type: ignore[method-assign]
        layout.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)

    def set_message(self, message: str, *, severity: str | None = None) -> None:
        self._label.setText(message)
        if severity is not None and severity != self._severity:
            self._severity = severity
            self._icon_name = {
                "info": "info",
                "success": "check",
                "warning": "warning",
                "error": "warning",
            }.get(severity, "info")
            self.setProperty("severity", severity)
            self.style().unpolish(self)
            self.style().polish(self)

    def set_action(self, text: str) -> None:
        self._action.setText(text)
        self._action.setVisible(bool(text))

    def _on_action(self, event: Any) -> None:
        self.action_clicked.emit()

    def _on_close(self, event: Any) -> None:
        self.hide()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        colour = {
            "info": tokens.info,
            "success": tokens.success,
            "warning": tokens.warning,
            "error": tokens.danger,
        }.get(self._severity, tokens.info)
        self._icon.setPixmap(icon_pixmap(self._icon_name, tokens, 15, colour=colour))
        self._action.setStyleSheet(f"color: {colour}; text-decoration: underline; font-weight: 600;")
        self._close.setStyleSheet(f"color: {tokens.text_muted};")


class Badge(QLabel):
    """Small status chip ("installed", "beta", "FA+EN"…)."""

    def __init__(self, text: str = "", severity: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._severity = severity
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_severity(self, severity: str) -> None:
        self._severity = severity

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        from ..theme.styles import build_badge_style

        self.setStyleSheet(build_badge_style(tokens, self._severity))


class EmptyState(QWidget):
    """Centred placeholder with an icon, message and optional action."""

    action_clicked = Signal()

    def __init__(
        self,
        title: str,
        message: str = "",
        parent: QWidget | None = None,
        *,
        icon_name: str = "info",
        action_text: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("EmptyState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_name = icon_name
        layout.addWidget(self._icon)
        self._title = QLabel(title)
        self._title.setObjectName("EmptyStateTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title)
        self._message = QLabel(message)
        self._message.setObjectName("Caption")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._message.setVisible(bool(message))
        layout.addWidget(self._message)
        self._button: QWidget | None = None
        if action_text:
            from .buttons import PrimaryButton

            self._button = PrimaryButton(action_text, parent=self)
            self._button.clicked.connect(self.action_clicked.emit)  # type: ignore[attr-defined]
            layout.addWidget(self._button, 0, Qt.AlignmentFlag.AlignCenter)

    def set_message(self, title: str, message: str = "") -> None:
        self._title.setText(title)
        self._message.setText(message)
        self._message.setVisible(bool(message))

    def set_icon(self, name: str) -> None:
        self._icon_name = name

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._icon.setPixmap(icon_pixmap(self._icon_name, tokens, 42, colour=tokens.text_faint))


class HeroPanel(QFrame):
    """Gradient header panel used at the top of the dashboard and About page."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        eyebrow: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("HeroPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(6)
        self._eyebrow = QLabel(eyebrow)
        self._eyebrow.setObjectName("SectionTitle")
        self._eyebrow.setVisible(bool(eyebrow))
        layout.addWidget(self._eyebrow)
        self._title = QLabel(title)
        self._title.setObjectName("H1")
        self._title.setWordWrap(True)
        layout.addWidget(self._title)
        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("Muted")
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))
        layout.addWidget(self._subtitle)
        self._actions = QHBoxLayout()
        self._actions.setContentsMargins(0, 8, 0, 0)
        self._actions.setSpacing(8)
        layout.addLayout(self._actions)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def set_eyebrow(self, text: str) -> None:
        self._eyebrow.setText(text)
        self._eyebrow.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> QWidget:
        self._actions.addWidget(widget)
        return widget

    def add_stretch(self) -> None:
        self._actions.addStretch(1)


class KeyValueRow(QWidget):
    """Label / value row used across the About, Settings and diagnostics views."""

    def __init__(self, label: str, value: str = "", parent: QWidget | None = None, *, mono: bool = False) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(12)
        self._label = QLabel(label)
        self._label.setObjectName("Muted")
        self._label.setMinimumWidth(150)
        self._value = QLabel(value)
        self._value.setObjectName("Monospace" if mono else "")
        self._value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._value.setWordWrap(True)
        layout.addWidget(self._label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._value, 1)

    def set_value(self, value: str, *, severity: str = "") -> None:
        self._value.setText(value)
        if severity and self._tokens_holder is not None:
            pass

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens_holder = tokens

    _tokens_holder: ThemeTokens | None = None


class FlowLayout(QWidget):
    """Simple wrap layout for chips and tags (Qt layouts cannot wrap)."""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 6) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QGridLayout

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(spacing)
        self._column = 0
        self._row = 0
        self._max_columns = 24

    def add(self, widget: QWidget) -> None:
        self._grid.addWidget(widget, self._row, self._column)
        self._column += 1
        if self._column >= self._max_columns:
            self._column = 0
            self._row += 1

    def clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._column = 0
        self._row = 0


def set_widget_role(widget: QWidget, role: str, value: str = "true") -> None:
    """Set a dynamic QSS property and force a restyle."""
    widget.setProperty(role, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def tooltip_with_shortcut(text: str, shortcut: str) -> str:
    if not shortcut:
        return text
    return f"{text}  ({shortcut})"


def make_chip(text: str, tokens: ThemeTokens | None = None, *, severity: str = "neutral") -> QLabel:
    badge = Badge(text, severity)
    if tokens is not None:
        badge.apply_tokens(tokens)
    return badge


def dot_separator(parent: QWidget | None = None) -> QLabel:
    label = QLabel("·", parent)
    label.setObjectName("Faint")
    return label


def labelled_icon(name: str, tokens: ThemeTokens, size: int = 16, colour: str = "") -> QLabel:
    label = QLabel()
    label.setPixmap(icon_pixmap(name, tokens, size, colour=colour or tokens.text_muted))
    return label


def icon_size(size: int = 18) -> QSize:
    return QSize(size, size)


class RoundedImagePreview(QWidget):
    """Rounded clip for previews (used by the About page and the theme picker)."""

    def __init__(self, parent: QWidget | None = None, *, radius: int = 10, fill: str = "") -> None:
        super().__init__(parent)
        self._radius = radius
        self._fill = fill
        self._pixmap = None

    def set_pixmap(self, pixmap: Any) -> None:
        self._pixmap = pixmap
        self.update()

    def set_colour(self, colour: str) -> None:
        self._fill = colour
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect(), self._radius, self._radius)
        painter.setClipPath(path)
        if self._pixmap is not None:
            painter.drawPixmap(self.rect(), self._pixmap)
        elif self._fill:
            painter.fillPath(path, QColor(self._fill))
        painter.end()


def labelled_value_row(label: str, value_widget: QWidget) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    text = QLabel(label)
    text.setObjectName("Muted")
    layout.addWidget(text, 0)
    layout.addWidget(value_widget, 1)
    return row


def iter_widgets(layout: Any) -> Iterable[QWidget]:
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        if widget is not None:
            yield widget
        else:
            child = item.layout()
            if child is not None:
                yield from iter_widgets(child)


def repaint_tree(widget: QWidget, tokens: ThemeTokens) -> None:
    """Recursively push new tokens into themed widgets after a theme switch."""
    for child in widget.findChildren(QWidget):
        apply = getattr(child, "apply_tokens", None)
        if callable(apply):
            try:
                apply(tokens)
            except TypeError:
                continue


def connect_theme(widget: QWidget, theme_manager: Any) -> None:
    """Wire ``tokens_changed`` to a widget's ``apply_tokens`` slot."""
    apply = getattr(widget, "apply_tokens", None)
    if callable(apply):
        theme_manager.tokens_changed.connect(apply)


def toast_host(widget: QWidget) -> QWidget:
    """Return the widget new toasts should be parented to."""
    window = widget.window()
    return window if window is not None else widget
