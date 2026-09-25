"""Toasts, busy overlay and standard dialogs.

The app prefers non-blocking feedback: a toast in the corner states what
happened, and only genuinely destructive or ambiguous actions open a modal.
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..theme.icons import get_icon, icon_pixmap
from ..theme.tokens import ThemeTokens
from .buttons import FlatButton, PrimaryButton, SecondaryButton

SEVERITY_ICONS = {
    "info": "info",
    "success": "check",
    "warning": "warning",
    "error": "warning",
    "download": "download",
}


class Toast(QFrame):
    """A single notification card."""

    dismissed = Signal()

    def __init__(
        self,
        title: str,
        message: str = "",
        parent: QWidget | None = None,
        *,
        severity: str = "info",
        timeout_ms: int = 6000,
        action_text: str = "",
        action: Callable[[], None] | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToastFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._tokens: ThemeTokens | None = None
        self._severity = severity
        self._detail = detail
        self._action = action
        self.setFixedWidth(348)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 12, 12)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setSpacing(9)
        self._icon = QLabel()
        header.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(2)
        self._title = QLabel(title)
        self._title.setObjectName("TitleLabel")
        self._title.setWordWrap(True)
        titles.addWidget(self._title)
        self._message = QLabel(message)
        self._message.setObjectName("Caption")
        self._message.setWordWrap(True)
        self._message.setVisible(bool(message))
        titles.addWidget(self._message)
        header.addLayout(titles, 1)
        close = QPushButton("✕")
        close.setObjectName("WindowControl")
        close.setFixedSize(22, 22)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.dismiss)
        header.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        footer = QHBoxLayout()
        footer.setSpacing(6)
        self._detail_button = FlatButton("Details", self, icon_name="list")
        self._detail_button.setVisible(bool(detail))
        self._detail_button.clicked.connect(self._show_detail)
        footer.addWidget(self._detail_button)
        footer.addStretch(1)
        self._action_button = PrimaryButton(action_text, self) if action_text else None
        if self._action_button is not None:
            self._action_button.clicked.connect(self._run_action)
            footer.addWidget(self._action_button)
        layout.addLayout(footer)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        if timeout_ms > 0:
            self._timer.setInterval(timeout_ms)
            self._timer.timeout.connect(self.dismiss)
        self._animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def start(self) -> None:
        if self._timer.interval():
            self._timer.start()
        self.setWindowOpacity(0.0)
        self.show()
        self._animation.stop()
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.start()

    def dismiss(self) -> None:
        self._timer.stop()
        self._animation.stop()
        self._animation.setStartValue(self.windowOpacity())
        self._animation.setEndValue(0.0)
        with contextlib.suppress(RuntimeError):
            self._animation.finished.connect(self._finish)
        self._animation.start()

    def _finish(self) -> None:
        with contextlib.suppress(RuntimeError, TypeError):
            self._animation.finished.disconnect(self._finish)
        self.dismissed.emit()
        self.hide()
        self.deleteLater()

    def _run_action(self) -> None:
        if self._action is not None:
            with contextlib.suppress(Exception):
                self._action()
        self.dismiss()

    def _show_detail(self) -> None:
        if not self._detail:
            return
        dialog = DetailDialog(self._title.text(), self._detail, self, tokens=self._tokens)
        dialog.exec()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        colour = {
            "info": tokens.info,
            "success": tokens.success,
            "warning": tokens.warning,
            "error": tokens.danger,
            "download": tokens.accent_primary,
        }.get(self._severity, tokens.info)
        self._icon.setPixmap(icon_pixmap(SEVERITY_ICONS.get(self._severity, "info"), tokens, 17, colour=colour))


class ToastManager:
    """Stacks toasts in the bottom-right corner of a window."""

    def __init__(self, host: QWidget, *, max_visible: int = 4) -> None:
        self._host = host
        self._max_visible = max_visible
        self._toasts: list[Toast] = []
        self._margin = 18

    def show(
        self,
        title: str,
        message: str = "",
        *,
        severity: str = "info",
        timeout_ms: int = 6000,
        action_text: str = "",
        action: Callable[[], None] | None = None,
        detail: str = "",
        tokens: ThemeTokens | None = None,
    ) -> Toast:
        toast = Toast(
            title,
            message,
            self._host,
            severity=severity,
            timeout_ms=timeout_ms,
            action_text=action_text,
            action=action,
            detail=detail,
        )
        if tokens is not None:
            toast.apply_tokens(tokens)
        toast.dismissed.connect(lambda: self._forget(toast))
        self._toasts.append(toast)
        while len(self._toasts) > self._max_visible:
            oldest = self._toasts.pop(0)
            with contextlib.suppress(RuntimeError):
                oldest.dismiss()
        toast.start()
        self._reposition()
        return toast

    def _forget(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._reposition()

    def clear(self) -> None:
        for toast in list(self._toasts):
            with contextlib.suppress(RuntimeError):
                toast.dismiss()
        self._toasts.clear()

    def _reposition(self) -> None:
        host_rect: QRect = self._host.rect()
        y = host_rect.bottom() - self._margin
        for toast in reversed(self._toasts):
            if not toast.isVisible():
                continue
            height = toast.sizeHint().height()
            y -= height
            x = host_rect.right() - toast.width() - self._margin
            toast.move(QPoint(max(0, x), max(0, y)))
            y -= 10


class BusyOverlay(QWidget):
    """Semi-transparent overlay that blocks interaction while a task runs."""

    cancelled = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("BusyOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._tokens: ThemeTokens | None = None
        self._text = "Working…"
        self._hint = ""
        self._cancellable = False
        self._ticks = 0
        self.setVisible(False)
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._tick)

    def start(self, text: str, *, hint: str = "", cancellable: bool = False) -> None:
        self._text = text
        self._hint = hint
        self._cancellable = cancellable
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.raise_()
        self.show()
        self._ticks = 0
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def set_text(self, text: str, *, hint: str = "") -> None:
        self._text = text
        if hint:
            self._hint = hint
        self.update()

    def _tick(self) -> None:
        self._ticks = (self._ticks + 1) % 12
        self.update()

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if self._cancellable:
            self.cancelled.emit()

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if event.key() == Qt.Key.Key_Escape and self._cancellable:
            self.cancelled.emit()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        tokens = self._tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = QColor(tokens.background if tokens else "#0E1015")
        background.setAlpha(190)
        painter.fillRect(self.rect(), background)

        box_width, box_height = 320, 120
        rect = QRect(
            (self.width() - box_width) // 2,
            (self.height() - box_height) // 2,
            box_width,
            box_height,
        )
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 16, 16)
        painter.fillPath(path, QColor(tokens.background_elevated if tokens else "#1A1F2B"))

        # spinner
        centre = QRect(rect.x() + 28, rect.y() + 40, 40, 40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.surface_alt if tokens else "#252B3B"))
        painter.drawEllipse(centre)
        painter.setPen(QColor(tokens.accent_primary if tokens else "#0A84FF"))
        pen = painter.pen()
        pen.setWidthF(3.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(centre, (self._ticks * 30) * 16, 100 * 16)

        font = QFont(painter.font())
        font.setPointSizeF(font.pointSizeF() + 0.5)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(tokens.text if tokens else "#F3F5F9"))
        painter.drawText(
            QRect(rect.x() + 84, rect.y() + 34, rect.width() - 100, 26),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._text,
        )
        font.setWeight(QFont.Weight.Normal)
        font.setPointSizeF(font.pointSizeF() - 1.0)
        painter.setFont(font)
        painter.setPen(QColor(tokens.text_muted if tokens else "#9BA4B5"))
        hint = self._hint or ("Click anywhere to cancel" if self._cancellable else "")
        painter.drawText(
            QRect(rect.x() + 84, rect.y() + 60, rect.width() - 104, 44),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            hint,
        )
        painter.end()


class DetailDialog(QDialog):
    """Scrollable details viewer (error reports, logs, diagnostics)."""

    def __init__(self, title: str, body: str, parent: QWidget | None = None, *, tokens: ThemeTokens | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(680, 460)
        self._tokens = tokens
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        header = QLabel(title)
        header.setObjectName("H3")
        layout.addWidget(header)
        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(body)
        if tokens is not None:
            editor.setStyleSheet(
                "font-family: Consolas, 'Cascadia Mono', 'Courier New', monospace; font-size: 12px;"
            )
        layout.addWidget(editor, 1)
        buttons = QHBoxLayout()
        copy = SecondaryButton("Copy to clipboard", self, icon_name="copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(body))
        close = PrimaryButton("Close", self)
        close.clicked.connect(self.accept)
        buttons.addWidget(copy)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens


def confirm(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    confirm_text: str = "Confirm",
    cancel_text: str = "Cancel",
    destructive: bool = False,
    detail: str = "",
    checkbox_text: str = "",
    tokens: ThemeTokens | None = None,
) -> tuple[bool, bool]:
    """Modal confirmation. Returns ``(accepted, checkbox_checked)``."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(message)
    if detail:
        box.setDetailedText(detail)
    box.setIcon(QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Question)
    accept = box.addButton(confirm_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(cancel_text, QMessageBox.ButtonRole.RejectRole)
    check: QCheckBox | None = None
    if checkbox_text:
        check = QCheckBox(checkbox_text)
        box.setCheckBox(check)
    box.setDefaultButton(accept)  # type: ignore[arg-type]
    box.exec()
    return box.clickedButton() is accept, bool(check is not None and check.isChecked())


def info(parent: QWidget | None, title: str, message: str, *, detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(message)
    box.setIcon(QMessageBox.Icon.Information)
    if detail:
        box.setDetailedText(detail)
    box.exec()


def warn(parent: QWidget | None, title: str, message: str, *, detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(message)
    box.setIcon(QMessageBox.Icon.Warning)
    if detail:
        box.setDetailedText(detail)
    box.exec()


def error(parent: QWidget | None, title: str, message: str, *, detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(message)
    box.setIcon(QMessageBox.Icon.Critical)
    if detail:
        box.setDetailedText(detail)
    box.exec()


class ScrollingColumn(QWidget):
    """Vertical scroll area whose content widget is exposed."""

    def __init__(self, parent: QWidget | None = None, *, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        super().__init__(parent)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._scroll)
        content = QWidget()
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(14)
        self._scroll.setWidget(content)
        self._content = content

    @property
    def content(self) -> QWidget:
        return self._content

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout: Any, stretch: int = 0) -> Any:
        self._layout.addLayout(layout, stretch)
        return layout

    def add_stretch(self) -> None:
        self._layout.addStretch(1)

    def scroll_to_top(self) -> None:
        self._scroll.verticalScrollBar().setValue(0)


class SectionCard(QFrame):
    """Card with a header row and a body layout — the workhorse of the Settings page."""

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        subtitle: str = "",
        icon_name: str = "",
        padding: int = 16,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setProperty("card", "true")
        self._tokens: ThemeTokens | None = None
        self._icon_name = icon_name
        layout = QVBoxLayout(self)
        layout.setContentsMargins(padding, padding, padding, padding)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(9)
        self._icon = QLabel()
        self._icon.setVisible(bool(icon_name))
        header.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        self._title = QLabel(title)
        self._title.setObjectName("H3")
        column.addWidget(self._title)
        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("Caption")
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))
        column.addWidget(self._subtitle)
        header.addLayout(column, 1)
        self._trailing = QHBoxLayout()
        self._trailing.setSpacing(6)
        header.addLayout(self._trailing)
        layout.addLayout(header)
        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(8)
        layout.addLayout(self._body)

    def body(self) -> QVBoxLayout:
        return self._body

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def add_trailing(self, widget: QWidget) -> QWidget:
        self._trailing.addWidget(widget)
        return widget

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        if self._icon_name:
            self._icon.setPixmap(icon_pixmap(self._icon_name, tokens, 18, colour=tokens.accent_primary))


def ask_text(
    parent: QWidget | None,
    title: str,
    label: str,
    *,
    initial: str = "",
    placeholder: str = "",
) -> str | None:
    """Single-field input dialog; returns None when cancelled."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(420, 150)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)
    heading = QLabel(label)
    heading.setWordWrap(True)
    layout.addWidget(heading)
    from PySide6.QtWidgets import QLineEdit

    edit = QLineEdit(initial)
    edit.setPlaceholderText(placeholder)
    edit.selectAll()
    layout.addWidget(edit)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    edit.setFocus()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return edit.text().strip()
    return None


def licence_viewer(title: str, text: str, parent: QWidget | None = None) -> None:
    dialog = DetailDialog(title, text, parent)
    dialog.exec()


def list_dialog(title: str, items: Sequence[str], parent: QWidget | None = None) -> None:
    body = "\n".join(items)
    DetailDialog(title, body, parent).exec()


def build_button_row(buttons: Sequence[tuple[str, Callable[[], None]]], parent: QWidget | None = None) -> QWidget:
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    for label, callback in buttons:
        button = SecondaryButton(label, row)
        button.clicked.connect(callback)
        layout.addWidget(button)
    layout.addStretch(1)
    return row


def icon_for_severity(severity: str) -> str:
    return SEVERITY_ICONS.get(severity, "info")


def themed_icon(name: str, tokens: ThemeTokens, colour: str = "") -> Any:
    return get_icon(name, tokens, colour=colour or tokens.text_muted)
