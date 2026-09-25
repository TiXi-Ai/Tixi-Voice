"""Shared building blocks for the application pages.

Every page is a :class:`Page`: a scrollable column with a consistent header,
optional header actions and a ``toast`` shortcut that routes notifications to
the main window's toast stack.  Pages never talk to each other directly — when
one page needs the user to go somewhere else it calls ``self.window.goto(page_id)``.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..theme.icons import icon_pixmap
from ..theme.tokens import ThemeTokens
from ..widgets.base import Card, InfoBanner, SectionHeader
from ..widgets.toast import ScrollingColumn
from ..widgets.buttons import PrimaryButton, SecondaryButton


class Page(QWidget):
    """Base class for every section in the sidebar."""

    #: shown in the sidebar
    title: str = "Page"
    #: one-line description under the title
    subtitle: str = ""
    #: icon name from ``tixi.ui.theme.icons``
    icon_name: str = "info"
    #: the page is created lazily on first visit
    lazy: bool = True

    def __init__(self, context: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self._tokens: ThemeTokens | None = None
        self._built = False
        self._header_actions: list[QWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        self.header = _PageHeader(self.title, self.subtitle, icon_name=self.icon_name)
        root.addWidget(self.header)

        self.scroll = ScrollingColumn(self, margins=(0, 0, 8, 0))
        root.addWidget(self.scroll, 1)
        self.body = self.scroll.body()
        self.body.setSpacing(14)

        self.banner = InfoBanner("")
        self.banner.setVisible(False)
        self.body.addWidget(self.banner)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(14)
        self.body.addWidget(self.content)

    # -- lifecycle ----------------------------------------------------------
    def ensure_built(self) -> None:
        if not self._built:
            self._built = True
            self.build()

    def build(self) -> None:
        """Create the widgets.  Called once, on first display."""

    def refresh(self) -> None:
        """Re-read data from the services.  Called on every display and on demand."""
        self.ensure_built()

    def on_enter(self) -> None:
        """Called when the page becomes the visible one."""
        self.ensure_built()

    def on_leave(self) -> None:
        """Called when the user navigates away (stop playback, timers…)."""

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.header.apply_tokens(tokens)
        for widget in _walk(self):
            if widget is self or widget is self.header or isinstance(widget, Page):
                continue
            hook = getattr(widget, "apply_tokens", None)
            if not callable(hook) or isinstance(widget, Page):
                continue
            try:
                hook(tokens)
            except Exception:  # noqa: BLE001 - a paint helper must not break the page
                pass
        if self.banner.isVisible():
            self.banner.apply_tokens(tokens)

    @property
    def tokens(self) -> ThemeTokens | None:
        return self._tokens

    # -- helpers ------------------------------------------------------------
    def add(self, widget: QWidget) -> QWidget:
        self.ensure_built()
        self.content_layout.addWidget(widget)
        return widget

    def add_layout(self, layout: Any) -> Any:
        self.ensure_built()
        self.content_layout.addLayout(layout)
        return layout

    def add_header_action(self, widget: QWidget) -> QWidget:
        self.header.add_action(widget)
        return widget

    def section(self, title: str, subtitle: str = "", *, icon_name: str = "") -> Card:
        """A titled card with an empty body to fill."""
        self.ensure_built()
        card = Card()
        card.body().addWidget(SectionHeader(title, subtitle, icon_name=icon_name))
        self.content_layout.addWidget(card)
        return card

    def row(self, *widgets: QWidget, spacing: int = 8) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing)
        for widget in widgets:
            layout.addWidget(widget)
        self.add_layout(layout)
        return layout

    def show_banner(self, message: str, *, severity: str = "info", action: str = "") -> InfoBanner:
        self.banner.set_message(message, severity=severity)
        self.banner.set_action(action)
        self.banner.setVisible(bool(message))
        if self._tokens is not None:
            self.banner.apply_tokens(self._tokens)
        return self.banner

    def hide_banner(self) -> None:
        self.banner.setVisible(False)

    def toast(self, title: str, message: str = "", *, severity: str = "info", detail: str = "") -> None:
        window = self.window()
        hook = getattr(window, "notify", None)
        if callable(hook):
            hook(title, message, severity=severity, detail=detail)

    def busy(self, message: str, *, cancellable: bool = True) -> None:
        window = self.window()
        hook = getattr(window, "set_busy", None)
        if callable(hook):
            hook(message, cancellable=cancellable)

    def idle(self) -> None:
        window = self.window()
        hook = getattr(window, "clear_busy", None)
        if callable(hook):
            hook()

    def goto(self, page_id: str) -> None:
        window = self.window()
        hook = getattr(window, "goto", None)
        if callable(hook):
            hook(page_id)


class _PageHeader(QWidget):
    """Title, subtitle and the action area on the right of a page."""

    def __init__(self, title: str, subtitle: str = "", *, icon_name: str = "") -> None:
        super().__init__(parent=None)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._icon = QLabel()
        self._icon.setVisible(bool(icon_name))
        self._icon_name = icon_name
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        self._title = QLabel(title)
        self._title.setObjectName("H1")
        column.addWidget(self._title)
        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("Muted")
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))
        column.addWidget(self._subtitle)
        layout.addLayout(column, 1)

        self._actions = QHBoxLayout()
        self._actions.setContentsMargins(0, 0, 0, 0)
        self._actions.setSpacing(8)
        layout.addLayout(self._actions)

    def add_action(self, widget: QWidget) -> QWidget:
        self._actions.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return widget

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        if self._icon_name:
            self._icon.setPixmap(icon_pixmap(self._icon_name, tokens, 28, colour=tokens.accent_primary))


def _walk(widget: QWidget) -> list[QWidget]:
    found: list[QWidget] = [widget]
    for child in widget.findChildren(QWidget):
        found.append(child)
    return found


# ---------------------------------------------------------------------------
# small factories shared by several pages
# ---------------------------------------------------------------------------
def caption(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("Caption")
    label.setWordWrap(True)
    return label


def muted(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


def title_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("H3")
    label.setWordWrap(True)
    return label


def value_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("MonoValue")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line


def button_row(*buttons: QWidget, stretch: bool = True) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    for widget in buttons:
        layout.addWidget(widget)
    if stretch:
        layout.addStretch(1)
    return layout


def labelled_row(label: str, widget: QWidget, *, hint: str = "", width: int = 0) -> QWidget:
    """``[label + hint] [widget]`` — a standard settings line."""
    from ..widgets.inputs import SettingRow

    if width:
        widget.setMinimumWidth(width)
    return SettingRow(label, widget, description=hint)


def action_button(text: str, *, icon_name: str = "", primary: bool = False) -> Any:
    factory: Callable[..., Any] = PrimaryButton if primary else SecondaryButton
    return factory(text, icon_name=icon_name)


def two_column(left: QWidget, right: QWidget, *, ratio: tuple[int, int] = (3, 2)) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(14)
    layout.addWidget(left, ratio[0])
    layout.addWidget(right, ratio[1])
    return layout
