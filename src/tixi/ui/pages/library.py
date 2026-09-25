"""Audio library: everything Tixi Voice has generated, recorded or imported."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...audio.playback import AudioPlayer
from ...utils.humanize import human_datetime, human_duration, human_size, human_time_ago
from ..widgets.base import Badge, Card, EmptyState, InfoBanner, SectionHeader
from ..widgets.buttons import FlatButton, PrimaryButton, SecondaryButton
from ..widgets.inputs import ComboRow, SearchBox, Switch, TagInput
from ..widgets.meters import WaveformView
from ..widgets.text_views import rtl_text_view
from .base import Page, button_row, labelled_row, muted


class LibraryPage(Page):
    """Browse, play, export and clean up stored audio."""

    title = "Audio Library"
    subtitle = "Generated speech, recordings and imported files — with sizes and formats."
    icon_name = "music"

    def build(self) -> None:
        self.player = AudioPlayer()
        self.player.set_position_callback(self._on_position)
        self.player.set_state_callback(self._on_state)
        self._assets: list[Any] = []
        self._current: Any = None

        toolbar = Card()
        row = QHBoxLayout()
        row.setSpacing(8)
        self._search = SearchBox("Filter by name or text…")
        self._search.setMinimumWidth(220)
        self._search.textChanged.connect(lambda _text: self.refresh())
        row.addWidget(self._search, 1)
        self._format = ComboRow([("All formats", "")])
        self._format.changed.connect(lambda _key: self.refresh())
        row.addWidget(self._format)
        self._favourites = Switch(False, label="Favourites")
        self._favourites.toggled.connect(lambda _state: self.refresh())
        row.addWidget(self._favourites)
        toolbar.body().addLayout(row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._import = PrimaryButton("Import files…", toolbar, icon_name="upload")
        self._import.clicked.connect(self._import_files)
        self._play = SecondaryButton("Play", toolbar, icon_name="play")
        self._play.clicked.connect(self._toggle_playback)
        self._play.setEnabled(False)
        self._export = SecondaryButton("Export…", toolbar, icon_name="download")
        self._export.clicked.connect(self._export_selected)
        self._export.setEnabled(False)
        self._favourite = SecondaryButton("Favourite", toolbar, icon_name="star")
        self._favourite.clicked.connect(self._toggle_favourite)
        self._favourite.setEnabled(False)
        self._delete = SecondaryButton("Delete", toolbar, icon_name="trash")
        from ..widgets.buttons import DangerButton

        self._delete = DangerButton("Delete", toolbar, icon_name="trash")
        self._delete.clicked.connect(self._delete_selected)
        self._delete.setEnabled(False)
        self._delete.clicked.connect(self._delete_selected)
        self._delete.setEnabled(False)
        for button in (self._import, self._play, self._export, self._favourite, self._delete):
            actions.addWidget(button)
        actions.addStretch(1)
        toolbar.body().addLayout(actions)
        self.add(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Name", "Kind", "Length", "Format", "Size", "Created"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.doubleClicked.connect(lambda _index: self._toggle_playback())
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self.table)

        details = Card()
        details.body().addWidget(SectionHeader("Details", icon_name="info"))
        self._details_title = QLabel("Nothing selected")
        self._details_title.setObjectName("H3")
        self._details_title.setWordWrap(True)
        details.body().addWidget(self._details_title)
        self._details_meta = muted("")
        details.body().addWidget(self._details_meta)
        self._waveform = WaveformView()
        self._waveform.setMinimumHeight(80)
        self._waveform.set_empty_text("Select a clip")
        details.body().addWidget(self._waveform)
        self._player_status = muted("")
        details.body().addWidget(self._player_status)
        self._tags = TagInput(placeholder="add a tag")
        self._tags.changed.connect(self._on_tags_changed)
        details.body().addWidget(labelled_row("Tags", self._tags))
        reveal = FlatButton("Show in Explorer", details, icon_name="folder")
        reveal.clicked.connect(self._reveal)
        details.body().addLayout(button_row(reveal, stretch=False))
        self._transcript = rtl_text_view("")
        self._transcript.setMinimumHeight(120)
        details.body().addWidget(self._transcript)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.add(splitter)

        self._empty = EmptyState(
            "The library is empty",
            "Generate speech, record from the microphone or import existing audio files.",
            icon_name="music",
            action_text="Import audio",
        )
        self._empty.action_clicked.connect(self._import_files)
        self.add(self._empty)

        self._missing_banner = InfoBanner("", severity="warning", action_text="Remove entries")
        self._missing_banner.action_clicked.connect(self._remove_missing)
        self._missing_banner.setVisible(False)
        self.add(self._missing_banner)

    # -- lifecycle ----------------------------------------------------------
    def refresh(self) -> None:
        self.ensure_built()
        self._reload_formats()
        self._reload()

    def on_leave(self) -> None:
        try:
            self.player.stop()
        except Exception:  # noqa: BLE001
            pass

    def _reload_formats(self) -> None:
        current = self._format.current_key() or ""
        base = [("All formats", "")]
        try:
            formats = [name for name, _label in self.context.exports.available_formats()]
        except Exception:  # noqa: BLE001
            formats = []
        options = base + [(name.upper(), name) for name in formats]
        for existing in self._assets:
            if existing.format and not any(key == existing.format for _label, key in options):
                options.append((str(existing.format).upper(), existing.format))
        self._format.set_options(options, current=current)

    def _reload(self) -> None:
        assets = self.context.library_repository.list(
            search=self._search.text().strip(),
            format_filter=str(self._format.current_key() or ""),
            favourites_only=self._favourites.isChecked() and hasattr(self._favourites, "isChecked"),
            limit=500,
        )
        self._assets = assets
        self.table.setRowCount(len(assets))
        for row, asset in enumerate(assets):
            values = [
                asset.file_name,
                asset.kind or "",
                human_duration(asset.duration_ms / 1000) if asset.duration_ms else "—",
                (asset.format or "").upper(),
                human_size(asset.size_bytes),
                human_time_ago(asset.created_at),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 3 and asset.missing:
                    item.setForeground(Qt.GlobalColor.red)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        self._empty.setVisible(not assets)
        self.table.setVisible(bool(assets))
        missing = [asset for asset in assets if asset.missing]
        self._missing_banner.setVisible(bool(missing))
        if missing:
            self._missing_banner.set_message(
                f"{len(missing)} entr{'y' if len(missing) == 1 else 'ies'} point to files that are no longer "
                "on disk. They are kept in the database so nothing is lost silently."
            )
        self._on_selection()

    # -- selection / playback ----------------------------------------------
    def _selected_assets(self) -> list[Any]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self._assets[row] for row in rows if 0 <= row < len(self._assets)]

    def _on_selection(self) -> None:
        selection = self._selected_assets()
        self._current = selection[0] if selection else None
        for button in (self._play, self._export, self._favourite, self._delete):
            button.setEnabled(bool(selection))
        if self._current is None:
            self._details_title.setText("Nothing selected")
            self._details_meta.setText("")
            self._transcript.setPlainText("")
            self._waveform.clear()
            return
        asset = self._current
        self._details_title.setText(asset.file_name)
        self._details_meta.setText(
            f"{asset.kind or 'audio'} · {human_duration(asset.duration_ms / 1000)} · "
            f"{asset.sample_rate or 0} Hz · {asset.channels or 1} ch · {human_size(asset.size_bytes)} · "
            f"created {human_datetime(asset.created_at)}"
            + (" · file missing" if asset.missing else "")
        )
        peaks = self.context.library.peak_list(asset.path)
        if peaks:
            self._waveform.set_peaks(peaks, max(0.01, asset.duration_ms / 1000))
        else:
            self._waveform.set_empty_text("Waveform unavailable")
            self._waveform.clear()
        self._transcript.setPlainText(asset.source_text or "(no source text stored for this clip)")
        self._tags.blockSignals(True)
        self._tags.set_values(asset.tags or [])
        self._tags.blockSignals(False)

    def _toggle_playback(self) -> None:
        if self.player.is_playing():
            self.player.stop()
            return
        asset = self._current
        if asset is None or asset.missing:
            return
        try:
            self.player.load_file(asset.path)
        except Exception as exc:  # noqa: BLE001
            self.toast("Could not open the file", str(exc), severity="error")
            return
        if not self.player.play(restart=True):
            self.toast("Playback failed", self.player.error() or "No output device.", severity="error")

    def _on_state(self, state: str) -> None:
        self._play.setText("Stop" if state == "playing" else "Play")
        self._player_status.setText(f"Playback: {state}" if state != "idle" else "")

    def _on_position(self, position: float, duration: float) -> None:
        if duration:
            self._waveform.set_position(position)
            self._player_status.setText(f"{human_duration(position)} / {human_duration(duration)}")

    # -- operations ---------------------------------------------------------
    def _import_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            self,
            "Import audio",
            str(Path.home()),
            "Audio (*.wav *.mp3 *.m4a *.flac *.ogg *.opus *.aac);;All files (*)",
        )
        if not paths:
            return
        outcomes, imported = self.context.library.import_many(paths)
        errors = [outcome for outcome in outcomes if outcome.error]
        duplicates = [outcome for outcome in outcomes if outcome.skipped]
        self.refresh()
        if imported:
            self.toast("Imported", f"{imported} file(s) added to the library.", severity="success")
        if duplicates:
            self.toast("Already in the library", f"{len(duplicates)} file(s) were skipped.", severity="info")
        if errors:
            self.toast("Some files failed", errors[0].error, severity="warning", detail="\n".join(e.error for e in errors))

    def _export_selected(self) -> None:
        asset = self._current
        if asset is None:
            return
        formats = self.context.exports.available_formats()
        options = ";;".join(f"{name.upper()} (*.{name})" for name, _note in formats)
        suggested = str(self.context.paths.exports / asset.file_name)
        path, selected = QFileDialog.getSaveFileName(self, "Export audio", suggested, options)
        if not path:
            return
        fmt = selected.split("(")[-1].strip(")*. ") or Path(path).suffix.lstrip(".") or "wav"
        from ...services.export_service import ExportRequest

        result = self.context.exports.export_audio(
            ExportRequest(source=Path(asset.path), destination=Path(path), format=fmt)
        )
        if result.ok:
            self.toast("Exported", result.summary(), severity="success")
        else:
            self.toast("Export failed", result.error, severity="error")

    def _toggle_favourite(self) -> None:
        asset = self._current
        if asset is None:
            return
        self.context.library_repository.set_favourite(asset.id, not asset.favourite) if hasattr(
            self.context.library_repository, "set_favourite"
        ) else None
        self.refresh()

    def _on_tags_changed(self, values: list[str]) -> None:
        asset = self._current
        if asset is None:
            return
        setter = getattr(self.context.library_repository, "set_tags", None)
        if callable(setter):
            setter(asset.id, values)
            asset.tags = list(values)

    def _delete_selected(self) -> None:
        from ..widgets.toast import confirm

        selection = self._selected_assets()
        if not selection:
            return
        names = ", ".join(asset.file_name for asset in selection[:3])
        if len(selection) > 3:
            names += f" and {len(selection) - 3} more"
        accepted, delete_files = confirm(
            self,
            "Remove from the library?",
            f"{names} will be removed from the library database.",
            detail="Files are never deleted silently — tick the box to delete them from disk as well.",
            checkbox_text="Also delete the audio files from disk",
            confirm_text="Remove entries",
            destructive=True,
        )
        if not accepted:
            return
        for asset in selection:
            self.context.library.delete_asset(asset.id, remove_file=delete_files)
        self.refresh()
        self.toast("Removed", f"{len(selection)} entr{'y' if len(selection) == 1 else 'ies'} removed.", severity="success")

    def _remove_missing(self) -> None:
        missing = [asset for asset in self._assets if asset.missing]
        for asset in missing:
            self.context.library.delete_asset(asset.id, remove_file=False)
        self.refresh()

    def _reveal(self) -> None:
        asset = self._current
        if asset is None:
            return
        from ...utils.win_integration import open_in_explorer

        open_in_explorer(asset.path, select=True)
