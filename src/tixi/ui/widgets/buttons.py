"""Button widgets with a consistent look and honest disabled/reason states."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QSizePolicy, QWidget

from ..theme.icons import get_icon, icon_pixmap
from ..theme.tokens import ThemeTokens


class BaseButton(QPushButton):
    """QPushButton that remembers *why* it is disabled."""

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        icon_name: str = "",
        role: str = "",
        tooltip: str = "",
        minimum_width: int = 0,
    ) -> None:
        super().__init__(text, parent)
        self._icon_name = icon_name
        self._role = role
        self._tokens: ThemeTokens | None = None
        self._disabled_reason = ""
        if role:
            self.setProperty(role, "true")
        if tooltip:
            self.setToolTip(tooltip)
        if minimum_width:
            self.setMinimumWidth(minimum_width)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # -- theming ------------------------------------------------------------
    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        if not self._icon_name:
            self._refresh_tooltip()
            return
        colour = tokens.accent_text if self._role == "primary" else tokens.text_muted
        if self._role == "danger":
            colour = tokens.danger
        self.setIcon(get_icon(self._icon_name, tokens, colour=colour))
        self.setIconSize(QSize(16, 16))
        self._refresh_tooltip()
        self.update()

    def _refresh_tooltip(self) -> None:
        base = self.toolTip().split("\n")[0]
        if self._disabled_reason and not self.isEnabled():
            self.setToolTip(f"{base}\n{self._disabled_reason}" if base else self._disabled_reason)
        else:
            self.setToolTip(base)

    # -- api ----------------------------------------------------------------
    def disable_with_reason(self, reason: str) -> None:
        """Disable the button and explain why in the tooltip."""
        self._disabled_reason = reason
        self.setEnabled(False)
        self._refresh_tooltip()

    def enable(self) -> None:
        self._disabled_reason = ""
        self.setEnabled(True)
        self._refresh_tooltip()

    def set_icon_name(self, name: str) -> None:
        self._icon_name = name
        if self._tokens is not None:
            self.apply_tokens(self._tokens)

    def set_role(self, role: str) -> None:
        self._role = role
        self.setProperty(role, "true")
        self.style().unpolish(self)
        self.style().polish(self)


class PrimaryButton(BaseButton):
    def __init__(self, text: str, parent: QWidget | None = None, *, icon_name: str = "", tooltip: str = "") -> None:
        super().__init__(text, parent, icon_name=icon_name, role="primary", tooltip=tooltip)
        self.setDefault(True)


class SecondaryButton(BaseButton):
    def __init__(self, text: str, parent: QWidget | None = None, *, icon_name: str = "", tooltip: str = "") -> None:
        super().__init__(text, parent, icon_name=icon_name, tooltip=tooltip)


class DangerButton(BaseButton):
    def __init__(self, text: str, parent: QWidget | None = None, *, icon_name: str = "trash", tooltip: str = "") -> None:
        super().__init__(text, parent, icon_name=icon_name, role="danger", tooltip=tooltip)


class FlatButton(BaseButton):
    def __init__(self, text: str = "", parent: QWidget | None = None, *, icon_name: str = "", tooltip: str = "") -> None:
        super().__init__(text, parent, icon_name=icon_name, role="flat", tooltip=tooltip)


class LinkButton(BaseButton):
    def __init__(self, text: str, parent: QWidget | None = None, *, tooltip: str = "") -> None:
        super().__init__(text, parent, tooltip=tooltip)
        self.setObjectName("LinkButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_name = ""
        self.setFlat(True)


class IconButton(BaseButton):
    """Square icon-only button; ``tooltip`` is mandatory so nothing is a mystery."""

    def __init__(
        self,
        icon_name: str,
        tooltip: str,
        parent: QWidget | None = None,
        *,
        size: int = 30,
        role: str = "flat",
        checkable: bool = False,
    ) -> None:
        super().__init__("", parent, icon_name=icon_name, role=role, tooltip=tooltip)
        self.setProperty("iconOnly", "true")
        self.setCheckable(checkable)
        self.setFixedSize(QSize(size, size))
        self._icon_size = max(14, int(size * 0.56))

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        colour = tokens.accent_primary if self.isChecked() else tokens.text_muted
        self.setIcon(get_icon(self._icon_name, tokens, colour=colour))
        self.setIconSize(QSize(self._icon_size, self._icon_size))
        self.update()

    def nextCheckState(self) -> None:  # noqa: N802 - Qt naming
        super().nextCheckState()
        if self._tokens is not None:
            self.apply_tokens(self._tokens)


class SegmentedControl(QWidget):
    """A row of mutually exclusive buttons (replaces tab bars in cards)."""

    currentChanged = Signal(str)

    def __init__(
        self,
        options: list[tuple[str, str]],
        parent: QWidget | None = None,
        *,
        spacing: int = 2,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SegmentedControl")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(spacing)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[tuple[str, QPushButton]] = []
        for index, (key, label) in enumerate(options):
            button = QPushButton(label)
            button.setProperty("segmented", "true")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._group.addButton(button, index)
            layout.addWidget(button)
            self._buttons.append((key, button))
        self._group.idClicked.connect(self._on_clicked)
        if self._buttons:
            self._buttons[0][1].setChecked(True)

    def _on_clicked(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            self.currentChanged.emit(self._buttons[index][0])

    def current_key(self) -> str:
        for key, button in self._buttons:
            if button.isChecked():
                return key
        return ""

    def set_current(self, key: str, *, emit: bool = False) -> None:
        for option_key, button in self._buttons:
            if option_key == key:
                button.setChecked(True)
                if emit:
                    self.currentChanged.emit(key)
                return

    def set_option_enabled(self, key: str, enabled: bool, reason: str = "") -> None:
        for option_key, button in self._buttons:
            if option_key == key:
                button.setEnabled(enabled)
                if reason:
                    button.setToolTip(reason)

    def set_option_text(self, key: str, text: str) -> None:
        for option_key, button in self._buttons:
            if option_key == key:
                button.setText(text)


class ActionRow(QWidget):
    """A horizontal row of buttons with a stretch, used in card footers."""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 8) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(spacing)

    def add(self, widget: QWidget) -> QWidget:
        self._layout.addWidget(widget)
        return widget

    def add_stretch(self) -> None:
        self._layout.addStretch(1)

    def layout(self) -> QHBoxLayout:  # type: ignore[override]
        return self._layout


class CopyButton(FlatButton):
    """Copy-to-clipboard button with a temporary "Copied" confirmation."""

    def __init__(self, parent: QWidget | None = None, *, tooltip: str = "Copy to clipboard") -> None:
        super().__init__("Copy", parent, icon_name="copy", tooltip=tooltip)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(1400)
        self._timer.timeout.connect(self._restore)

    def flash_copied(self) -> None:
        self.setText("Copied")
        self.set_icon_name("check")
        self._timer.start()

    def _restore(self) -> None:
        self.setText("Copy")
        self.set_icon_name("copy")


class ToggleButton(BaseButton):
    """Flat button that reports its checked state through a callback."""

    def __init__(
        self,
        text: str,
        parent: QWidget | None = None,
        *,
        icon_name: str = "",
        tooltip: str = "",
        on_toggle: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(text, parent, icon_name=icon_name, role="flat", tooltip=tooltip)
        self.setCheckable(True)
        if on_toggle is not None:
            self.toggled.connect(on_toggle)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        if self._icon_name:
            colour = tokens.accent_primary if self.isChecked() else tokens.text_muted
            self.setIcon(get_icon(self._icon_name, tokens, colour=colour))
            self.setIconSize(QSize(16, 16))

    def nextCheckState(self) -> None:  # noqa: N802 - Qt naming
        super().nextCheckState()
        if self._tokens is not None:
            self.apply_tokens(self._tokens)


def record_button(tokens: ThemeTokens, size: int = 56) -> QPushButton:
    """The big circular record button used on the Speech to Text page."""
    button = QPushButton()
    button.setFixedSize(QSize(size, size))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip("Start recording (Ctrl+R)")
    return button


def set_record_state(button: QPushButton, tokens: ThemeTokens, *, recording: bool) -> None:
    colour = tokens.danger if recording else tokens.accent_primary
    button.setStyleSheet(
        f"QPushButton {{ background: {colour}; border: none; border-radius: {button.width() // 2}px; }}"
        f"QPushButton:hover {{ background: {tokens.accent_hover if not recording else '#FF6B60'}; }}"
    )
    button.setIcon(get_icon("stop" if recording else "mic", tokens, colour=tokens.accent_text))
    button.setIconSize(QSize(int(button.width() * 0.42), int(button.width() * 0.42)))
