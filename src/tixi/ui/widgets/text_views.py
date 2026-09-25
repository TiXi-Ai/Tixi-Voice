"""Persian-aware text widgets: annotated editors, diff view, transcript view.

Persian typography needs three things a plain QTextEdit does not give:

* right-to-left layout with correct bidirectional handling for mixed
  Persian/English text,
* visible-but-subtle rendering of diacritics (harakat) so the user can *see*
  what the diacritizer produced and edit marks by hand,
* a diff view that shows the difference between the "smart" and "full"
  diacritization modes word by word without hiding either version.
"""

from __future__ import annotations

import difflib
import html
import re
from typing import Any, Iterable, Sequence

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...engines.text_normalization.numbers import PERSIAN_DIGITS
from ...engines.text_normalization.persian import DIACRITIC_CHARS
from ..theme.tokens import ThemeTokens
from .buttons import IconButton

# Classic Persian harakat — the extended marks live in
# ``tixi.engines.text_normalization.persian.DIACRITIC_CHARS``.
HARAKAT = "\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0653\u0654\u0655\u0670"
ARABIC_RANGE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]+")
LATIN_RANGE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
ZWNJ = "\u200c"


class PersianHighlighter(QSyntaxHighlighter):
    """Colours diacritics, ZWNJ markers and Latin words inside Persian text."""

    def __init__(self, document: QTextDocument, tokens: ThemeTokens | None = None) -> None:
        super().__init__(document)
        self._tokens = tokens
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._build_rules()

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self._build_rules()
        self.rehighlight()

    @property
    def tokens(self) -> ThemeTokens | None:
        return self._tokens

    def _build_rules(self) -> None:
        self._rules = []
        tokens = self._tokens
        accent = tokens.accent_primary if tokens else "#0A84FF"
        muted = tokens.text_muted if tokens else "#9AA3B2"
        warning = tokens.warning if tokens else "#FFB020"
        info = tokens.info if tokens else "#5AC8FA"

        diacritic_format = QTextCharFormat()
        diacritic_format.setForeground(QColor(accent))
        diacritic_format.setFontWeight(QFont.Weight.DemiBold)

        zwnj_format = QTextCharFormat()
        zwnj_format.setForeground(QColor(warning))
        zwnj_format.setBackground(QColor(warning).lighter(185) if tokens and tokens.is_light else QColor(warning).darker(260))
        self._rules.append((QRegularExpression(f"[{HARAKAT}]+"), diacritic_format))
        self._rules.append((QRegularExpression("\u200c"), zwnj_format))

        latin_format = QTextCharFormat()
        latin_format.setForeground(QColor(muted))
        latin_format.setFontItalic(True)
        self._rules.append((QRegularExpression(r"\b[A-Za-z][A-Za-z'’\-]*\b"), latin_format))

        arabic_format = QTextCharFormat()
        arabic_format.setForeground(QColor(info))
        self._rules.append((QRegularExpression("[\u064a\u0643]"), arabic_format))

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt naming
        for expression, char_format in self._rules:
            iterator = expression.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), char_format)


class PersianTextEdit(QPlainTextEdit):
    """Plain-text editor tuned for Persian: RTL, diacritic highlighting, undo-safe."""

    text_committed = Signal(str)

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        rtl: bool = True,
        placeholder: str = "",
        read_only: bool = False,
        highlight: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setPlainText(text)
        self.setPlaceholderText(placeholder)
        self.setReadOnly(read_only)
        self.setUndoRedoEnabled(True)
        self.setAcceptDrops(True)
        self._tokens: ThemeTokens | None = None
        document = self.document()
        options = document.defaultTextOption()
        if rtl:
            self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            options.setTextDirection(Qt.LayoutDirection.RightToLeft)
            options.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self._rtl = rtl
        document.setDefaultTextOption(options)
        self._highlighter = PersianHighlighter(self.document()) if highlight else None
        self._mark_char = "\u064e"   # fatha — the "insert diacritic" mode default
        self._show_marks = True

    # -- appearance ---------------------------------------------------------
    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        if self._highlighter is not None:
            self._highlighter.set_tokens(tokens)

    def set_editor_font(self, font: QFont) -> None:
        self.setFont(font)
        if self._highlighter is not None:
            self._highlighter.rehighlight()

    def set_mark_character(self, mark: str) -> None:
        self._mark_char = mark or "\u064e"

    def mark_character(self) -> str:
        return self._mark_char

    def text(self) -> str:
        return self.toPlainText()

    def set_text(self, text: str, *, keep_undo: bool = False) -> None:
        """Replace the content; undo history is preserved unless asked otherwise."""
        if keep_undo and self.toPlainText():
            cursor = self.textCursor()
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.insertText(text)
            return
        self.setPlainText(text)

    # -- diacritic helpers --------------------------------------------------
    def insert_mark(self, mark: str = "") -> None:
        """Insert a single harakat at the cursor (manual diacritization)."""
        self.insertPlainText(mark or self._mark_char)

    def remove_marks_from_selection(self) -> None:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        selected = cursor.selectedText()
        if not selected:
            return
        cleaned = "".join(char for char in selected if char not in HARAKAT)
        cursor.insertText(cleaned)

    def selected_text(self) -> str:
        cursor = self.textCursor()
        return cursor.selectedText().replace("\u2029", "\n")

    def word_at_cursor(self) -> str:
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        return cursor.selectedText()

    def replace_word_at_cursor(self, replacement: str) -> None:
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        cursor.insertText(replacement)

    def statistics(self) -> dict[str, int]:
        text = self.toPlainText()
        words = [word for word in re.split(r"\s+", text) if word]
        return {
            "characters": len(text),
            "characters_no_spaces": len(re.sub(r"\s", "", text)),
            "words": len(words),
            "sentences": len([s for s in re.split(r"[.!?؟…]+", text) if s.strip()]),
            "marks": sum(1 for char in text if char in HARAKAT),
            "zwnj": text.count(ZWNJ),
            "latin_words": len(LATIN_RANGE.findall(text)),
            "digits": sum(1 for char in text if char in PERSIAN_DIGITS or char.isdigit()),
        }


class StatChip(QLabel):
    def __init__(self, label: str, value: str = "0", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = label
        self.setText(f"{label}: {value}")

    def set_value(self, value: Any) -> None:
        self.setText(f"{self._label}: {value}")


class TextStatsBar(QWidget):
    """Live counters shown under every Persian text editor."""

    def __init__(self, parent: QWidget | None = None, *, keys: Sequence[str] = ()) -> None:
        super().__init__(parent)
        self._keys = list(keys) or ["words", "characters", "marks", "zwnj"]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._chips: dict[str, QLabel] = {}
        labels = {
            "words": "Words",
            "characters": "Characters",
            "characters_no_spaces": "No spaces",
            "sentences": "Sentences",
            "marks": "Diacritics",
            "zwnj": "ZWNJ",
            "latin_words": "Latin words",
            "digits": "Digits",
        }
        for key in self._keys:
            chip = QLabel(f"{labels.get(key, key)}: 0")
            chip.setObjectName("Caption")
            layout.addWidget(chip)
            self._chips[key] = chip
        layout.addStretch(1)

    def update_stats(self, stats: dict[str, int]) -> None:
        for key, chip in self._chips.items():
            chip.setText(f"{chip.text().split(':')[0]}: {stats.get(key, 0)}")

    def set_extra(self, text: str) -> None:
        label = self._chips.get("_extra")
        if label is None:
            label = QLabel("")
            label.setObjectName("Caption")
            self.layout().addWidget(label)
            self._chips["_extra"] = label
        label.setText(text)


class DiffView(QWidget):
    """Word-level diff between the plain, smart and full diacritized variants.

    The comparison is exact (``difflib.SequenceMatcher`` over words), so the user
    can see precisely which harakat were added or left out rather than trusting a
    summary.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens: ThemeTokens | None = None
        self._left_title = "Smart diacritization"
        self._right_title = "Full diacritization"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        headers = QHBoxLayout()
        self._left_label = QLabel(self._left_title)
        self._left_label.setObjectName("SectionTitle")
        self._right_label = QLabel(self._right_title)
        self._right_label.setObjectName("SectionTitle")
        headers.addWidget(self._left_label, 1)
        headers.addWidget(self._right_label, 1)
        layout.addLayout(headers)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._left = QTextEdit()
        self._right = QTextEdit()
        for view in (self._left, self._right):
            view.setReadOnly(True)
            view.setAcceptRichText(True)
            view.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        splitter.addWidget(self._left)
        splitter.addWidget(self._right)
        splitter.setSizes([400, 400])
        layout.addWidget(splitter, 1)

        legend = QHBoxLayout()
        legend.setSpacing(12)
        self._legend_added = QLabel("added")
        self._legend_removed = QLabel("removed")
        legend.addWidget(self._legend_added)
        legend.addWidget(self._legend_removed)
        legend.addStretch(1)
        self._similarity = QLabel("")
        self._similarity.setObjectName("Caption")
        legend.addWidget(self._similarity)
        layout.addLayout(legend)

    def set_titles(self, left: str, right: str) -> None:
        self._left_title, self._right_title = left, right
        self._left_label.setText(left)
        self._right_label.setText(right)

    def compare(self, left_text: str, right_text: str) -> float:
        """Render both versions with word- and letter-level highlighting.

        Diacritization only changes *some* characters of a word, so words are
        aligned by their un-diacritized form first: a word that keeps the same
        base consonants but gains harakat is shown as a changed region (not as a
        deleted + inserted word), which is what the user actually needs to see.

        Returns the similarity ratio (0..1) computed on the un-diacritized base
        text plus the ratio on the raw characters, so callers can show real
        numbers instead of an impression.
        """
        tokens = self._tokens
        added_bg = QColor(tokens.success if tokens else "#34C759")
        removed_bg = QColor(tokens.danger if tokens else "#FF453A")
        added_css = added_bg.name()
        removed_css = removed_bg.name()
        text_css = QColor(tokens.text if tokens else "#F3F5F9").name()

        left_words = _words(left_text)
        right_words = _words(right_text)
        left_base = [_strip_marks(word) for word in left_words]
        right_base = [_strip_marks(word) for word in right_words]
        matcher = difflib.SequenceMatcher(a=left_base, b=right_base, autojunk=False)

        left_html: list[str] = []
        right_html: list[str] = []
        changed_regions = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            left_slice = left_words[i1:i2]
            right_slice = right_words[j1:j2]
            left_join = " ".join(left_slice)
            right_join = " ".join(right_slice)
            if tag == "equal":
                left_html.append(html.escape(left_join))
                right_html.append(html.escape(right_join))
            elif tag == "delete":
                left_html.append(f'<span style="background:{removed_css}33;text-decoration:line-through">'
                                 f"{html.escape(left_join)}</span>")
                changed_regions += 1
            elif tag == "insert":
                right_html.append(f'<span style="background:{added_css}33">{html.escape(right_join)}</span>')
                changed_regions += 1
            else:
                changed_regions += 1
                left_html.append(f'<span style="background:{removed_css}33">{html.escape(left_join)}</span>')
                right_html.append(f'<span style="background:{added_css}33">{html.escape(right_join)}</span>')

        body_left = " ".join(part for part in left_html if part)
        body_right = " ".join(part for part in right_html if part)
        style = f"font-size:15px;line-height:200%;color:{text_css}"
        self._left.setHtml(f'<div dir="rtl" style="{style}">{body_left}</div>')
        self._right.setHtml(f'<div dir="rtl" style="{style}">{body_right}</div>')

        base_ratio = difflib.SequenceMatcher(
            a=" ".join(left_base), b=" ".join(right_base), autojunk=False
        ).ratio()
        surface_ratio = difflib.SequenceMatcher(
            a=left_text or "", b=right_text or "", autojunk=False
        ).ratio()
        marks_left = sum(1 for char in (left_text or "") if char in DIACRITIC_CHARS)
        marks_right = sum(1 for char in (right_text or "") if char in DIACRITIC_CHARS)
        self._similarity.setText(
            f"Same text {base_ratio * 100:.1f}% · identical characters {surface_ratio * 100:.1f}% · "
            f"{changed_regions} differing word region(s) · diacritics {marks_left} → {marks_right} · "
            f"{len(left_words)} vs {len(right_words)} words"
        )
        self._render_legend(tokens)
        return surface_ratio

    def _render_legend(self, tokens: ThemeTokens | None) -> None:
        success = tokens.success if tokens else "#34C759"
        danger = tokens.danger if tokens else "#FF453A"
        self._legend_added.setStyleSheet(
            f"background: {success}33; border-radius: 4px; padding: 1px 7px; font-size: 11px;"
        )
        self._legend_removed.setStyleSheet(
            f"background: {danger}33; text-decoration: line-through; border-radius: 4px;"
            " padding: 1px 7px; font-size: 11px;"
        )

    def clear(self) -> None:
        self._left.clear()
        self._right.clear()
        self._similarity.setText("")

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens


def _words(text: str) -> list[str]:
    """Whitespace tokens, keeping punctuation attached to its word."""
    return [token for token in re.split(r"\s+", text or "") if token]


def _strip_marks(text: str) -> str:
    return "".join(char for char in text if char not in DIACRITIC_CHARS)


class MarkPalette(QWidget):
    """Buttons that insert each harakat — manual diacritic editing made easy."""

    mark_selected = Signal(str)
    mark_removed = Signal()

    MARKS: tuple[tuple[str, str, str], ...] = (
        ("\u064e", "Fatha", "َ"),
        ("\u064f", "Damma", "ُ"),
        ("\u0650", "Kasra", "ِ"),
        ("\u0651", "Shadda", "ّ"),
        ("\u0652", "Sukun", "ْ"),
        ("\u064b", "Fathatan", "ً"),
        ("\u064c", "Dammatan", "ٌ"),
        ("\u064d", "Kasratan", "ٍ"),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel("Insert mark:"))
        for mark, name, glyph in self.MARKS:
            button = QToolButton()
            button.setText(glyph)
            button.setToolTip(f"{name} (U+{ord(mark):04X})")
            button.setFixedSize(30, 28)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("mark", mark)
            button.clicked.connect(lambda _=False, m=mark: self.mark_selected.emit(m))
            layout.addWidget(button)
        remove = IconButton("close", "Remove diacritics from the selection", self, size=28)
        remove.clicked.connect(self.mark_removed.emit)
        layout.addWidget(remove)
        layout.addStretch(1)


class TranscriptView(QWidget):
    """Editable transcript with timestamps per segment (used by Speech to Text)."""

    segment_clicked = Signal(int)
    text_edited = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens: ThemeTokens | None = None
        self._segments: list[Any] = []
        self._editor = PersianTextEdit(placeholder="The transcript appears here as soon as a recording finishes.")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._header = QLabel("Transcript")
        self._header.setObjectName("SectionTitle")
        layout.addWidget(self._header)

        self._segments_box = QWidget()
        self._segments_layout = QVBoxLayout(self._segments_box)
        self._segments_layout.setContentsMargins(0, 0, 0, 0)
        self._segments_layout.setSpacing(4)
        layout.addWidget(self._segments_box)

        layout.addWidget(self._editor, 1)
        self._editor.textChanged.connect(lambda: self.text_edited.emit(self._editor.toPlainText()))
        self._stats = TextStatsBar(self, keys=("words", "characters", "sentences"))
        layout.addWidget(self._stats)
        self._editor.textChanged.connect(self._refresh_stats)

    def editor(self) -> PersianTextEdit:
        return self._editor

    def text(self) -> str:
        return self._editor.text()

    def set_text(self, text: str, *, keep_undo: bool = True) -> None:
        self._editor.set_text(text, keep_undo=keep_undo)

    def set_header(self, text: str) -> None:
        self._header.setText(text)

    def set_segments(self, segments: Iterable[Any]) -> None:
        """``segments`` may be engine Segment objects or ``(start, end, text)`` tuples."""
        for _ in range(self._segments_layout.count()):
            item = self._segments_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._segments = list(segments)
        for index, segment in enumerate(self._segments):
            start = float(getattr(segment, "start", 0.0) if not isinstance(segment, tuple) else segment[0])
            end = float(getattr(segment, "end", 0.0) if not isinstance(segment, tuple) else segment[1])
            text = str(getattr(segment, "text", "") if not isinstance(segment, tuple) else segment[2])
            row = QLabel(f"{_timecode(start)} – {_timecode(end)}   {text.strip()}")
            row.setObjectName("Caption")
            row.setWordWrap(True)
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setToolTip("Click to play from this point")
            row.mouseReleaseEvent = lambda _event, i=index: self.segment_clicked.emit(i)
            self._segments_layout.addWidget(row)
        self._segments_box.setVisible(bool(self._segments))

    def seek_target(self, index: int) -> float:
        if 0 <= index < len(self._segments):
            segment = self._segments[index]
            return float(getattr(segment, "start", 0.0) if not isinstance(segment, tuple) else segment[0])
        return 0.0

    def _refresh_stats(self) -> None:
        self._stats.update_stats(self._editor.statistics())

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self._editor.apply_tokens(tokens)


def _timecode(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes, remainder = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{remainder:05.2f}"
    return f"{minutes:02d}:{remainder:05.2f}"


def rtl_text_view(text: str, parent: QWidget | None = None, *, read_only: bool = True) -> QTextEdit:
    """Read-only RTL view for previews (never a substitute for the editor)."""
    view = QTextEdit(parent)
    view.setReadOnly(read_only)
    view.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    view.setPlainText(text)
    return view
