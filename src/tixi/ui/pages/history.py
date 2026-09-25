"""History: every text Tixi Voice produced or recognised."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...audio.playback import AudioPlayer
from ...storage.history_repository import KIND_LABELS
from ...utils.humanize import human_datetime, human_duration, human_time_ago
from ..widgets.base import Badge, Card, EmptyState, SectionHeader, StatTile
from ..widgets.buttons import DangerButton, FlatButton, PrimaryButton, SecondaryButton
from ..widgets.inputs import ComboRow, SearchBox, Switch
from ..widgets.text_views import PersianTextEdit
from .base import Page, button_row, labelled_row, muted


class HistoryPage(Page):
    """Search, replay, re-open and clean up the operation log."""

    title = "History"
    subtitle = "Searchable log of generated speech, transcriptions and dictation."
    icon_name = "clock"

    def build(self) -> None:
        self.player = AudioPlayer()
        self._entries: list[Any] = []
        self._current: Any = None

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self._tile_total = StatTile("Entries", "—", icon_name="list")
        self._tile_words = StatTile("Words", "—", icon_name="edit")
        self._tile_audio = StatTile("Audio", "—", icon_name="music")
        self._tile_kinds = StatTile("Kinds", "—", icon_name="tag")
        for tile in (self._tile_total, self._tile_words, self._tile_audio, self._tile_kinds):
            tiles.addWidget(tile)
        self.add_layout(tiles)

        toolbar = Card()
        row = QHBoxLayout()
        row.setSpacing(8)
        self._search = SearchBox("Search text, titles and models…")
        self._search.setMinimumWidth(240)
        self._search.textChanged.connect(lambda _text: self.refresh())
        row.addWidget(self._search, 1)
        self._kind = ComboRow([("All kinds", "")] + [(label, key) for key, label in KIND_LABELS.items()])
        self._kind.changed.connect(lambda _key: self.refresh())
        row.addWidget(self._kind)
        self._pinned = Switch(False, label="Pinned")
        self._pinned.toggled.connect(lambda _state: self.refresh())
        row.addWidget(self._pinned)
        toolbar.body().addLayout(row)
        self.add(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Kind", "Title", "Words", "Length", "When"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (0, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self.table)

        preview = Card()
        preview.body().addWidget(SectionHeader("Preview", icon_name="edit"))
        self._meta = muted("Select an entry.")
        preview.body().addWidget(self._meta)
        self._text = PersianTextEdit(read_only=True, highlight=False)
        self._text.setMinimumHeight(240)
        preview.body().addWidget(self._text)

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(6)
        self._copy = FlatButton("Copy", actions, icon_name="clipboard")
        self._copy.clicked.connect(self._copy_text)
        self._play = FlatButton("Play audio", actions, icon_name="play")
        self._play.clicked.connect(self._toggle_playback)
        self._reveal = FlatButton("Show file", actions, icon_name="folder")
        self._reveal.clicked.connect(self._reveal_audio)
        self._pin = FlatButton("Pin", actions, icon_name="star")
        self._pin.clicked.connect(self._toggle_pin)
        for button in (self._copy, self._play, self._reveal, self._pin):
            actions_layout.addWidget(button)
        actions_layout.addStretch(1)
        preview.body().addWidget(actions)

        self._use_text = PrimaryButton("Send to Text to Speech", preview, icon_name="arrow-right")
        self._use_text.clicked.connect(self._send_to_tts)
        self._export = SecondaryButton("Export shown entries", preview, icon_name="download")
        self._export.clicked.connect(self._export_entries)
        self._delete = DangerButton("Delete selected", preview, icon_name="trash")
        self._delete.clicked.connect(self._delete_selected)
        preview.body().addLayout(button_row(self._use_text, self._export, self._delete))
        splitter.addWidget(preview)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.add(splitter)

        self._empty = EmptyState(
            "Nothing logged yet",
            "Generate speech, transcribe a file or dictate something and it will appear here.",
            icon_name="clock",
        )
        self.add(self._empty)

        retention = Card()
        retention.body().addWidget(
            SectionHeader(
                "Privacy & retention",
                "History lives in a local SQLite database. Nothing is uploaded anywhere.",
                icon_name="lock",
            )
        )
        self._retention_note = muted("")
        retention.body().addWidget(self._retention_note)
        apply_button = SecondaryButton("Apply retention policy now", retention, icon_name="clock")
        apply_button.clicked.connect(self._apply_retention)
        purge = FlatButton("Purge deleted entries", retention, icon_name="trash")
        purge.clicked.connect(self._purge_deleted)
        retention.body().addLayout(button_row(apply_button, purge, stretch=False))
        self.add(retention)

    # -- lifecycle ----------------------------------------------------------
    def refresh(self) -> None:
        self.ensure_built()
        entries = self.context.history.list(
            kind=str(self._kind.current_key() or ""),
            search=self._search.text().strip(),
            limit=500,
        )
        if self._pinned.isChecked():
            entries = [entry for entry in entries if entry.pinned]
        self._entries = entries
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                entry.kind_label,
                entry.title or entry.preview[:60],
                str(entry.word_count or 0),
                human_duration(entry.duration_ms / 1000) if entry.duration_ms else "—",
                human_time_ago(entry.created_at),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0 and entry.pinned:
                    item.setText(f"★ {value}")
                self.table.setItem(row, column, item)
        self._empty.setVisible(not entries)
        self.table.setVisible(bool(entries))
        self._refresh_stats()
        self._on_selection()

    def on_leave(self) -> None:
        try:
            self.player.stop()
        except Exception:  # noqa: BLE001
            pass

    def _refresh_stats(self) -> None:
        stats = self.context.history.statistics()
        by_kind = stats.get("by_kind") or {}
        self._tile_total.set_value(str(stats.get("total", 0)), f"{len(by_kind)} kind(s)")
        self._tile_words.set_value(str(sum(row.get("chars", 0) for row in by_kind.values())), "characters stored")
        self._tile_audio.set_value(
            human_duration(int(stats.get("duration_ms", 0)) / 1000),
            "of audio produced",
        )
        self._tile_kinds.set_value(str(len(by_kind)), " · ".join(list(by_kind)[:3]) or "—")
        retention_days = int(getattr(self.context.settings.privacy, "retention_days", 0) or 0)
        self._retention_note.setText(
            "History is kept forever."
            if retention_days == 0
            else f"Entries older than {retention_days} days are removed when you press the button below."
        )

    # -- selection ----------------------------------------------------------
    def _selected(self) -> list[Any]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self._entries[row] for row in rows if 0 <= row < len(self._entries)]

    def _on_selection(self) -> None:
        selection = self._selected()
        self._current = selection[0] if selection else None
        entry = self._current
        if entry is None:
            self._meta.setText("Select an entry.")
            self._text.set_text("")
            return
        bits = [entry.kind_label, f"created {human_datetime(entry.created_at)}"]
        if entry.language:
            bits.append(entry.language)
        if entry.model_id:
            bits.append(entry.model_id)
        if entry.duration_ms:
            bits.append(human_duration(entry.duration_ms / 1000))
        if entry.audio_path:
            bits.append("audio available")
        self._meta.setText(" · ".join(bits))
        self._text.set_text(entry.text or "(no text)")

    # -- actions ------------------------------------------------------------
    def _copy_text(self) -> None:
        """Copy the selected entry to the clipboard (wired to the Copy button)."""
        if self._current is None:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._current.text or "")
        self.toast("Copied", "The entry text is on the clipboard.", severity="success")

    def _toggle_playback(self) -> None:
        if self.player.is_playing():
            self.player.stop()
            return
        entry = self._current
        if entry is None or not entry.audio_path:
            return
        try:
            self.player.load_file(entry.audio_path)
        except Exception as exc:  # noqa: BLE001
            self.toast("Could not play the audio", str(exc), severity="error")
            return
        self.player.play(restart=True)

    def _reveal_audio(self) -> None:
        entry = self._current
        if entry is None or not entry.audio_path:
            return
        from ...utils.win_integration import open_in_explorer

        open_in_explorer(entry.audio_path, select=True)

    def _toggle_pin(self) -> None:
        entry = self._current
        if entry is None:
            return
        self.context.history.set_pinned(entry.id, not entry.pinned)
        self.refresh()

    def _send_to_tts(self) -> None:
        entry = self._current
        if entry is None or not entry.text:
            return
        window = self.window()
        page = getattr(window, "page", lambda _id: None)("text_to_speech")
        editor = getattr(page, "editor", None)
        if editor is not None:
            editor.set_text(entry.text)
            self.goto("text_to_speech")
            self.toast("Text loaded", "The entry is now in the Text to Speech editor.", severity="success")

    def _export_entries(self) -> None:
        """Export the selected entries (or everything shown) to a file."""
        entries = self._selected() or list(self._entries)
        if not entries:
            return
        path, selected = QFileDialog.getSaveFileName(
            self,
            "Export history",
            str(self.context.paths.exports / "tixi-history.csv"),
            "CSV (*.csv);;JSON (*.json);;Markdown (*.md)",
        )
        if not path:
            return
        fmt = selected.split("(")[-1].strip(")*. ") or Path(path).suffix.lstrip(".") or "csv"
        result = self.context.exports.export_history(entries, Path(path), fmt=fmt)
        if result.ok:
            self.toast("Exported", result.summary(), severity="success")
        else:
            self.toast("Export failed", result.error, severity="error")

    def _delete_selected(self) -> None:
        from ..widgets.toast import confirm

        selection = self._selected()
        if not selection:
            return
        accepted, hard = confirm(
            self,
            "Delete history entries?",
            f"{len(selection)} entr{'y' if len(selection) == 1 else 'ies'} will be deleted.",
            detail="Deleted entries can be restored from the database unless you purge them.",
            checkbox_text="Also delete the generated audio files",
            confirm_text="Delete",
            destructive=True,
        )
        if not accepted:
            return
        for entry in selection:
            if hard and entry.audio_path:
                Path(entry.audio_path).unlink(missing_ok=True)
            self.context.history.delete(entry.id, hard=hard)
        self.refresh()
        self.toast("Deleted", f"{len(selection)} entr{'y' if len(selection) == 1 else 'ies'} removed.", severity="success")

    def _apply_retention(self) -> None:
        days = int(getattr(self.context.settings.privacy, "retention_days", 0) or 0)
        if days <= 0:
            self.toast("Retention is off", "History is kept forever (Settings ▸ Privacy).", severity="info")
            return
        removed = self.context.history.apply_retention(days)
        self.refresh()
        self.toast("Retention applied", f"{removed} entr{'y' if removed == 1 else 'ies'} removed.", severity="success")

    def _purge_deleted(self) -> None:
        removed = self.context.history.purge_deleted()
        self.toast("Purged", f"{removed} entr{'y' if removed == 1 else 'ies'} permanently removed.", severity="success")
