"""Input widgets: search boxes, switches, shortcut recorders, path pickers, drop zones."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Iterable

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...app.paths import paths
from ..theme.icons import get_icon, icon_pixmap
from ..theme.tokens import ThemeTokens
from .base import h_line
from .buttons import FlatButton, IconButton


class SearchBox(QLineEdit):
    """Line edit with a leading magnifier and delayed search signal."""

    search_changed = Signal(str)

    def __init__(self, placeholder: str = "Search…", parent: QWidget | None = None, *, delay_ms: int = 220) -> None:
        super().__init__(parent)
        self.setObjectName("SearchBox")
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self._tokens: ThemeTokens | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(lambda: self.search_changed.emit(self.text()))
        self.textChanged.connect(lambda _: self._timer.start())

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        action = self.findChild(QWidget, "searchIcon")
        if action is None:
            from PySide6.QtGui import QAction

            icon_action = QAction(get_icon("search", tokens, colour=tokens.text_faint), "search", self)
            self.addAction(icon_action, QLineEdit.ActionPosition.LeadingPosition)
            icon_action.setObjectName("searchIcon")


class Switch(QWidget):
    """An animated iOS-style toggle switch (nicer than a checkbox in settings)."""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None, *, label: str = "") -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._label = label
        self._tokens: ThemeTokens | None = None
        self._offset = 1.0 if checked else 0.0
        self.setFixedSize(QSize(46, 26))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if label:
            self.setAccessibleName(label)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(lambda value: self._set_offset_from_animation(float(value)))

    def _set_offset_from_animation(self, value: float) -> None:
        self._offset = max(0.0, min(1.0, value))
        self.update()

    def isChecked(self) -> bool:  # noqa: N802 - Qt naming
        return self._checked

    def setChecked(self, checked: bool, *, emit: bool = False) -> None:  # noqa: N802 - Qt naming
        checked = bool(checked)
        if checked == self._checked:
            self._offset = 1.0 if checked else 0.0
            self.update()
            return
        self._checked = checked
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()
        self._offset = 1.0 if checked else 0.0
        if emit:
            self.toggled.emit(checked)

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.setChecked(not self._checked, emit=True)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.setChecked(not self._checked, emit=True)
        else:
            super().keyPressEvent(event)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        tokens = self._tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        height = self.height()
        radius = height / 2
        if not self.isEnabled():
            track = QColor(tokens.surface_alt if tokens else "#2A2A2A")
        elif self._checked:
            track = QColor(tokens.accent_primary if tokens else "#0A84FF")
        else:
            track = QColor(tokens.border_strong if tokens else "#3A3A3A")
        if not self._checked and tokens is not None:
            track = QColor(tokens.border if tokens.is_dark else tokens.border_strong)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, self.width(), height), radius, radius)

        knob_size = height - 6
        travel = self.width() - knob_size - 6
        x = 3 + travel * self._offset
        knob = QColor("#FFFFFF") if self.isEnabled() else QColor("#B9BEC7")
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(x, 3, knob_size, knob_size))
        painter.end()


class SettingRow(QWidget):
    """A settings line: title + description on the left, control on the right."""

    def __init__(
        self,
        title: str,
        control: QWidget,
        parent: QWidget | None = None,
        *,
        description: str = "",
        badge: str = "",
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(16)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        header = QHBoxLayout()
        header.setSpacing(6)
        self._title = QLabel(title)
        self._title.setObjectName("TitleLabel")
        header.addWidget(self._title)
        self._badge = QLabel(badge)
        self._badge.setObjectName("Pill")
        self._badge.setVisible(bool(badge))
        header.addWidget(self._badge)
        header.addStretch(1)
        column.addLayout(header)
        self._description = QLabel(description)
        self._description.setObjectName("Caption")
        self._description.setWordWrap(True)
        self._description.setVisible(bool(description))
        column.addWidget(self._description)
        layout.addLayout(column, 1)
        layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        self.control = control
        self.setObjectName("SettingRow")

    def set_description(self, text: str) -> None:
        self._description.setText(text)
        self._description.setVisible(bool(text))

    def set_warning(self, text: str) -> None:
        """Show a caveat under the description (never hide limitations)."""
        if not text:
            self._description.setStyleSheet("")
            return
        self.set_description(f"{self._description.text()}\n{text}" if self._description.text() else text)
        self._description.setObjectName("WarningText")
        self._description.style().unpolish(self._description)
        self._description.style().polish(self._description)


class ShortcutEdit(QWidget):
    """Captures a keyboard shortcut the user presses.

    Esc cancels capture, Backspace clears the shortcut.  The Qt key sequence is
    stored as text (``Ctrl+Shift+Space``) and validated by the settings layer.
    """

    changed = Signal(str)

    def __init__(self, value: str = "", parent: QWidget | None = None, *, allow_empty: bool = False) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._allow_empty = allow_empty
        self._capturing = False
        self._value = value
        self._tokens: ThemeTokens | None = None
        self._edit = QLineEdit(value)
        self._edit.setObjectName("ShortcutBox")
        self._edit.setReadOnly(True)
        self._edit.setPlaceholderText("Press a shortcut…")
        self._edit.setMaximumWidth(230)
        self._capture = FlatButton("Change", self, icon_name="hotkey", tooltip="Click, then press the key combination")
        self._capture.clicked.connect(self.start_capture)
        self._clear = IconButton("close", "Clear the shortcut", self, size=28)
        self._clear.clicked.connect(lambda: self.set_value("", emit=True))
        layout.addWidget(self._edit)
        layout.addWidget(self._capture)
        layout.addWidget(self._clear)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, *, emit: bool = False) -> None:
        self._value = value or ""
        self._edit.setText(self._value)
        self._capturing = False
        self._capture.setText("Change")
        if emit:
            self.changed.emit(self._value)

    def start_capture(self) -> None:
        self._capturing = True
        self._edit.setText("")
        self._edit.setPlaceholderText("Press the combination now…")
        self._capture.setText("Listening…")
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if not self._capturing:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.set_value(self._value)
            return
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.set_value("", emit=True)
            return
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
            Qt.Key.Key_Super_L,
            Qt.Key.Key_Super_R,
            Qt.Key.Key_unknown,
        ):
            return
        sequence = QKeySequence(event.keyCombination())
        text = sequence.toString(QKeySequence.SequenceFormat.PortableText)
        if not text:
            return
        self.set_value(text, emit=True)
        event.accept()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self._capture.apply_tokens(tokens)
        self._clear.apply_tokens(tokens)


class PathPicker(QWidget):
    """Line edit + Browse + optional "Open folder" button."""

    changed = Signal(str)

    def __init__(
        self,
        value: str = "",
        parent: QWidget | None = None,
        *,
        mode: str = "directory",
        placeholder: str = "",
        file_filter: str = "All files (*)",
        open_button: bool = True,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._file_filter = file_filter
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._edit = QLineEdit(value)
        self._edit.setPlaceholderText(placeholder)
        self._edit.editingFinished.connect(lambda: self.changed.emit(self.path()))
        browse = FlatButton("Browse…", self, icon_name="folder")
        browse.clicked.connect(self._browse)
        layout.addWidget(self._edit, 1)
        layout.addWidget(browse)
        if open_button:
            self._open = IconButton("external", "Open in File Explorer", self, size=28)
            self._open.clicked.connect(self._open_path)
            layout.addWidget(self._open)

    def path(self) -> str:
        return self._edit.text().strip()

    def set_path(self, value: str) -> None:
        self._edit.setText(value or "")

    def _browse(self) -> None:
        start = self.path() or str(paths().documents)
        if self._mode == "directory":
            chosen = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        elif self._mode == "save":
            chosen, _ = QFileDialog.getSaveFileName(self, "Choose a file", start, self._file_filter)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, "Choose a file", start, self._file_filter)
        if chosen:
            self.set_path(chosen)
            self.changed.emit(chosen)

    def _open_path(self) -> None:
        target = self.path()
        if not target:
            return
        try:
            from ...utils.win_integration import open_in_explorer
            from ...app.paths import IS_WINDOWS

            if IS_WINDOWS:
                open_in_explorer(target)
            else:  # pragma: no cover - developer convenience
                import subprocess

                subprocess.Popen(["xdg-open", target])
        except Exception:
            pass

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        for child in self.findChildren(QWidget):
            apply = getattr(child, "apply_tokens", None)
            if callable(apply):
                apply(tokens)


class SliderRow(QWidget):
    """Slider with a value label and optional reset button."""

    value_changed = Signal(float)

    def __init__(
        self,
        value: float = 1.0,
        parent: QWidget | None = None,
        *,
        minimum: float = 0.0,
        maximum: float = 2.0,
        step: float = 0.05,
        suffix: str = "×",
        default: float | None = None,
        formatter: Callable[[float], str] | None = None,
        width: int = 200,
    ) -> None:
        super().__init__(parent)
        self._min = minimum
        self._step = step
        self._suffix = suffix
        self._default = default
        self._formatter = formatter
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(int(round((maximum - minimum) / step)))
        self._slider.setFixedWidth(width)
        self._slider.setValue(self._to_slider(value))
        self._label = QLabel(self._format(value))
        self._label.setObjectName("Monospace")
        self._label.setMinimumWidth(52)
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._slider)
        layout.addWidget(self._label)
        if default is not None:
            reset = IconButton("refresh", f"Reset to {self._format(default)}", self, size=26)
            reset.clicked.connect(lambda: self.set_value(default, emit=True))
            layout.addWidget(reset)
        self._slider.valueChanged.connect(self._on_slider)

    def _to_slider(self, value: float) -> int:
        return int(round((value - self._min) / self._step))

    def _format(self, value: float) -> str:
        if self._formatter is not None:
            return self._formatter(value)
        return f"{value:.2f}{self._suffix}"

    def _on_slider(self, position: int) -> None:
        value = self._min + position * self._step
        self._label.setText(self._format(value))
        self.value_changed.emit(value)

    def value(self) -> float:
        return self._min + self._slider.value() * self._step

    def set_value(self, value: float, *, emit: bool = False) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(self._to_slider(value))
        self._slider.blockSignals(False)
        self._label.setText(self._format(value))
        if emit:
            self.value_changed.emit(value)


class SpinRow(QWidget):
    """Spin box with a unit suffix."""

    value_changed = Signal(int)

    def __init__(
        self,
        value: int = 0,
        parent: QWidget | None = None,
        *,
        minimum: int = 0,
        maximum: int = 100,
        suffix: str = "",
        width: int = 130,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._spin = QSpinBox()
        self._spin.setRange(minimum, maximum)
        self._spin.setValue(value)
        self._spin.setSuffix(suffix)
        self._spin.setFixedWidth(width)
        self._spin.valueChanged.connect(self.value_changed.emit)
        layout.addWidget(self._spin)

    def value(self) -> int:
        return self._spin.value()

    def set_value(self, value: int, *, emit: bool = False) -> None:
        self._spin.blockSignals(True)
        self._spin.setValue(int(value))
        self._spin.blockSignals(False)
        if emit:
            self.value_changed.emit(int(value))

    def set_range(self, minimum: int, maximum: int) -> None:
        self._spin.setRange(minimum, maximum)


class PercentRow(SliderRow):
    def __init__(self, value: float = 1.0, parent: QWidget | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("minimum", 0.0)
        kwargs.setdefault("maximum", 2.0)
        kwargs.setdefault("step", 0.05)
        kwargs.setdefault("formatter", lambda v: f"{v * 100:.0f}%")
        super().__init__(value, parent, **kwargs)


class ComboRow(QWidget):
    """Combo box exposing ``(key, label)`` options."""

    changed = Signal(str)

    def __init__(
        self,
        options: Iterable[tuple[str, str]] = (),
        parent: QWidget | None = None,
        *,
        width: int = 220,
        placeholder: str = "",
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._combo = QComboBox()
        self._combo.setMinimumWidth(width)
        if placeholder:
            self._combo.setPlaceholderText(placeholder)
        self._combo.currentIndexChanged.connect(self._on_changed)
        layout.addWidget(self._combo)
        self.set_options(options)

    def _on_changed(self, index: int) -> None:
        if index >= 0:
            self.changed.emit(self.current_key())

    def set_options(self, options: Iterable[tuple[str, str]], *, current: str = "") -> None:
        self._combo.blockSignals(True)
        self._combo.clear()
        for key, label in options:
            self._combo.addItem(label, key)
        if current:
            self.set_current(current)
        self._combo.blockSignals(False)

    def current_key(self) -> str:
        data = self._combo.currentData()
        return str(data) if data is not None else ""

    def set_current(self, key: str, *, emit: bool = False) -> None:
        index = self._combo.findData(key)
        if index < 0:
            return
        self._combo.blockSignals(True)
        self._combo.setCurrentIndex(index)
        self._combo.blockSignals(False)
        if emit:
            self.changed.emit(key)

    def combo(self) -> QComboBox:
        return self._combo

    def set_option_enabled(self, key: str, enabled: bool, reason: str = "") -> None:
        index = self._combo.findData(key)
        if index < 0:
            return
        model = self._combo.model()
        item = model.item(index) if hasattr(model, "item") else None
        if item is not None:
            item.setEnabled(enabled)
            if reason:
                item.setToolTip(reason)


class DropZone(QFrame):
    """Drag-and-drop target for audio files and folders."""

    files_dropped = Signal(list)
    clicked = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "Drop audio files here",
        hint: str = "WAV, MP3, M4A, FLAC, OGG, OPUS, AAC — or click to browse",
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setProperty("dragActive", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tokens: ThemeTokens | None = None
        self._icon_name = "upload"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18 if compact else 30, 18, 18 if compact else 30)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._icon)
        self._title = QLabel(title)
        self._title.setObjectName("TitleLabel" if compact else "EmptyStateTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title)
        self._hint = QLabel(hint)
        self._hint.setObjectName("Caption")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)
        self.setMinimumHeight(110 if compact else 170)

    def set_hint(self, text: str) -> None:
        self._hint.setText(text)

    def dragEnterEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drag_active(True)

    def dragLeaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        self._set_drag_active(False)

    def dropEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        self._set_drag_active(False)
        paths: list[str] = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if not local:
                continue
            candidate = Path(local)
            if candidate.is_dir():
                paths.extend(str(child) for child in sorted(candidate.iterdir()) if child.is_file())
            else:
                paths.append(local)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def _set_drag_active(self, active: bool) -> None:
        self.setProperty("dragActive", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self._icon.setPixmap(icon_pixmap(self._icon_name, tokens, 34, colour=tokens.accent_primary))


class ColourSwatch(QPushButton):
    """Base colour swatch; opens a colour dialog when clicked."""

    colour_changed = Signal(str)

    def __init__(self, colour: str = "#0A84FF", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour = colour
        self.setFixedSize(QSize(46, 30))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)

    def colour(self) -> str:
        return self._colour

    def set_colour(self, colour: str, *, emit: bool = False) -> None:
        self._colour = colour
        self.setStyleSheet(
            f"QPushButton {{ background: {colour}; border: 1px solid rgba(0,0,0,0.35); border-radius: 7px; }}"
        )
        if emit:
            self.colour_changed.emit(colour)

    def _pick(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        chosen = QColorDialog.getColor(QColor(self._colour), self, "Choose an accent colour")
        if chosen.isValid():
            self.set_colour(chosen.name().upper(), emit=True)


class TagInput(QWidget):
    """Comma/no — proper list editor for blocked applications and dictionaries."""

    changed = Signal(list)

    def __init__(self, values: Iterable[str] = (), parent: QWidget | None = None, *, placeholder: str = "Add an entry") -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._edit = QLineEdit()
        self._edit.setPlaceholderText(placeholder)
        self._edit.returnPressed.connect(self._add_current)
        add = FlatButton("Add", self, icon_name="plus")
        add.clicked.connect(self._add_current)
        row.addWidget(self._edit, 1)
        row.addWidget(add)
        layout.addLayout(row)
        self._tags = QVBoxLayout()
        self._tags.setContentsMargins(0, 0, 0, 0)
        self._tags.setSpacing(3)
        layout.addLayout(self._tags)
        self._values: list[str] = []
        for value in values:
            self._append_row(value)

    def values(self) -> list[str]:
        return list(self._values)

    def set_values(self, values: Iterable[str]) -> None:
        for _ in range(self._tags.count()):
            item = self._tags.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._values = []
        for value in values:
            self._append_row(value)

    def _append_row(self, value: str) -> None:
        if value in self._values:
            return
        self._values.append(value)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(value)
        label.setObjectName("Monospace")
        remove = IconButton("close", f"Remove {value}", row, size=22)
        remove.clicked.connect(lambda: self._remove(value, row))
        layout.addWidget(label, 1)
        layout.addWidget(remove)
        self._tags.addWidget(row)
        self.changed.emit(self.values())

    def _remove(self, value: str, row: QWidget) -> None:
        if value in self._values:
            self._values.remove(value)
        row.setParent(None)
        row.deleteLater()
        self.changed.emit(self.values())

    def _add_current(self) -> None:
        text = self._edit.text().strip()
        if not text:
            return
        self._edit.clear()
        self._append_row(text)


class NumberedListEditor(QWidget):
    """Ordered list editor used by pronunciation dictionaries and text rules."""

    changed = Signal(list)

    def __init__(self, parent: QWidget | None = None, *, labels: tuple[str, str] = ("From", "To")) -> None:
        super().__init__(parent)
        self._labels = labels
        self._rows: list[tuple[QLineEdit, QLineEdit]] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(5)
        self._entries = QVBoxLayout()
        self._entries.setContentsMargins(0, 0, 0, 0)
        self._entries.setSpacing(5)
        self._layout.addLayout(self._entries)
        add = FlatButton(f"Add {labels[0].lower()} → {labels[1].lower()} rule", self, icon_name="plus")
        add.clicked.connect(lambda: self.add_row("", ""))
        self._layout.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)

    def add_row(self, left: str = "", right: str = "") -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        left_edit = QLineEdit(left)
        left_edit.setPlaceholderText(self._labels[0])
        right_edit = QLineEdit(right)
        right_edit.setPlaceholderText(self._labels[1])
        remove = IconButton("trash", "Remove this rule", row, size=26)
        layout.addWidget(left_edit, 1)
        layout.addWidget(QLabel("→"))
        layout.addWidget(right_edit, 1)
        layout.addWidget(remove)
        self._entries.addWidget(row)
        pair = (left_edit, right_edit)
        self._rows.append(pair)
        left_edit.editingFinished.connect(self._emit)
        right_edit.editingFinished.connect(self._emit)
        remove.clicked.connect(lambda: self._remove_row(row, pair))
        self._emit()

    def _remove_row(self, row: QWidget, pair: tuple[QLineEdit, QLineEdit]) -> None:
        if pair in self._rows:
            self._rows.remove(pair)
        row.setParent(None)
        row.deleteLater()
        self._emit()

    def _emit(self) -> None:
        self.changed.emit(self.values())

    def values(self) -> list[list[str]]:
        return [
            [left.text().strip(), right.text().strip()]
            for left, right in self._rows
            if left.text().strip()
        ]

    def set_values(self, values: Iterable[Any]) -> None:
        for _ in range(self._entries.count()):
            item = self._entries.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []
        for entry in values:
            if isinstance(entry, dict):
                self.add_row(str(entry.get("from", "")), str(entry.get("to", "")))
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                self.add_row(str(entry[0]), str(entry[1]))
        if not values:
            self._emit()


def separator_line(tokens: ThemeTokens | None = None) -> QFrame:
    return h_line(tokens)


def reveal_in_explorer_button(path: str, parent: QWidget | None = None) -> QPushButton:
    button = FlatButton("Show in folder", parent, icon_name="folder", tooltip=path or "Nothing to show yet")

    def _reveal() -> None:
        if not path:
            return
        try:
            from ...app.paths import IS_WINDOWS
            from ...utils.win_integration import open_in_explorer

            if IS_WINDOWS:
                open_in_explorer(path)
            else:  # pragma: no cover
                import subprocess

                target = path if os.path.isdir(path) else os.path.dirname(path)
                subprocess.Popen(["xdg-open", target])
        except Exception:
            pass

    button.clicked.connect(_reveal)
    return button


def path_join_display(*parts: str) -> str:
    return os.path.join(*parts) if parts else ""
