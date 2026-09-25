"""AI Models: install, verify and remove the offline models and engine packs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...models.catalog import CATEGORY_LABELS
from ...utils.humanize import human_size
from ..widgets.base import Badge, Card, InfoBanner, SectionHeader, StatTile
from ..widgets.buttons import DangerButton, FlatButton, PrimaryButton, SecondaryButton
from ..widgets.inputs import ComboRow, SearchBox, Switch
from ..widgets.toast import DetailDialog
from .base import Page, button_row, muted

CATEGORIES = [("All categories", "")] + [(label, key) for key, label in CATEGORY_LABELS.items()]


class ModelsPage(Page):
    """The model manager: what exists, what is installed, what it costs."""

    title = "AI Models"
    subtitle = "Everything runs locally. Downloads happen only when you press install."
    icon_name = "box"

    def build(self) -> None:
        self._rows: list[Any] = []
        self._job_id = ""
        self._progress_label: QLabel | None = None

        packs_card = Card()
        packs_card.body().addWidget(
            SectionHeader(
                "Engine packs",
                "The runtime libraries that run the models. They are separate downloads so the "
                "application installer stays small.",
                icon_name="settings",
            )
        )
        self._packs_layout = QGridLayout()
        self._packs_layout.setSpacing(10)
        packs_card.body().addLayout(self._packs_layout)
        self.add(packs_card)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self._tile_models = StatTile("Models installed", "—", icon_name="box")
        self._tile_size = StatTile("Disk used", "—", icon_name="folder")
        self._tile_packs = StatTile("Engine packs", "—", icon_name="settings")
        self._tile_fa = StatTile("Persian ready", "—", icon_name="globe")
        for tile in (self._tile_models, self._tile_size, self._tile_packs, self._tile_fa):
            tiles.addWidget(tile)
        self.add_layout(tiles)

        toolbar = Card()
        filters = QHBoxLayout()
        filters.setSpacing(8)
        self._search = SearchBox("Search models…")
        self._search.setMinimumWidth(220)
        self._search.textChanged.connect(lambda _text: self.refresh())
        filters.addWidget(self._search, 1)
        self._category = ComboRow(CATEGORIES)
        self._category.changed.connect(lambda _key: self.refresh())
        filters.addWidget(self._category)
        self._installed_only = Switch(False, label="Installed only")
        self._installed_only.toggled.connect(lambda _state: self.refresh())
        filters.addWidget(self._installed_only)
        self._persian_only = Switch(False, label="Persian only")
        self._persian_only.toggled.connect(lambda _state: self.refresh())
        filters.addWidget(self._persian_only)
        toolbar.body().addLayout(filters)

        self._banner = InfoBanner("", severity="info")
        toolbar.body().addWidget(self._banner)

        self._install = PrimaryButton("Install", toolbar, icon_name="download")
        self._install.clicked.connect(self._install_selected)
        self._remove = DangerButton("Remove", toolbar, icon_name="trash")
        self._remove.clicked.connect(self._remove_selected)
        self._verify = SecondaryButton("Verify", toolbar, icon_name="check")
        self._verify.clicked.connect(self._verify_selected)
        self._import = SecondaryButton("Import local files…", toolbar, icon_name="upload")
        self._import.clicked.connect(self._import_selected)
        self._cancel = FlatButton("Cancel download", toolbar, icon_name="stop")
        self._cancel.clicked.connect(self._cancel_job)
        self._cancel.setEnabled(False)
        toolbar.body().addLayout(button_row(self._install, self._remove, self._verify, self._import, self._cancel))
        self.add(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Model", "Category", "Language", "Size", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self.table)

        details = Card()
        details.body().addWidget(SectionHeader("Model details", icon_name="info"))
        self._title = QLabel("Nothing selected")
        self._title.setObjectName("H3")
        self._title.setWordWrap(True)
        details.body().addWidget(self._title)
        self._badges = QHBoxLayout()
        details.body().addLayout(self._badges)
        self._description = muted("")
        details.body().addWidget(self._description)
        self._facts = muted("")
        details.body().addWidget(self._facts)
        self._licence = muted("")
        details.body().addWidget(self._licence)
        self._notes = muted("")
        details.body().addWidget(self._notes)
        detail_buttons = QWidget()
        layout = QHBoxLayout(detail_buttons)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._details_button = FlatButton("Full details", detail_buttons, icon_name="list")
        self._details_button.clicked.connect(self._show_details)
        self._page_button = FlatButton("Model page", detail_buttons, icon_name="globe")
        self._page_button.clicked.connect(self._open_page)
        self._usage = FlatButton("Usage in Tixi Voice", detail_buttons, icon_name="info")
        self._usage.clicked.connect(self._show_usage)
        for button in (self._details_button, self._page_button, self._usage):
            layout.addWidget(button)
        layout.addStretch(1)
        details.body().addWidget(detail_buttons)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.add(splitter)

    # -- lifecycle ----------------------------------------------------------
    def refresh(self) -> None:
        self.ensure_built()
        self._refresh_packs()
        rows = self.context.models.rows(
            category=str(self._category.current_key() or ""),
            search=self._search.text().strip(),
            persian_only=self._persian_only.isChecked(),
            installed_only=self._installed_only.isChecked(),
        )
        self._rows = rows
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = [
                row.name,
                row.category_label,
                row.language_label,
                row.download_label,
                row.status_label,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0 and row.recommended:
                    item.setText(f"★ {value}")
                if row.persian_capable and column == 2:
                    item.setToolTip(f"Persian support: {row.persian_label}")
                self.table.setItem(index, column, item)
        self._refresh_tiles()
        summary = self.context.models.summary()
        if not summary.get("persian_tts_installed") or not summary.get("persian_stt_installed"):
            missing = []
            if not summary.get("persian_tts_installed"):
                missing.append("a Persian voice (Piper)")
            if not summary.get("persian_stt_installed"):
                missing.append("a Whisper model")
            self._banner.set_message(
                "For the full Persian experience install " + " and ".join(missing) + "."
            )
            self._banner.setVisible(True)
        else:
            self._banner.setVisible(False)
        self._on_selection()

    def _refresh_packs(self) -> None:
        while self._packs_layout.count():
            item = self._packs_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for index, row in enumerate(self.context.models.pack_rows()):
            self._packs_layout.addWidget(self._pack_widget(row), index // 2, index % 2)

    def _pack_widget(self, row: Any) -> QWidget:
        card = Card(interactive=False)
        header = SectionHeader(row.label, row.pack.description, icon_name="settings")
        badge = Badge(row.status_label, "success" if row.installed else "neutral")
        header.add_trailing(badge)
        card.body().addWidget(header)
        facts = f"{row.size_label} · {row.pack.license or 'see the licence note'}"
        if row.needed_by:
            facts += f" · required by {len(row.needed_by)} model(s)"
        card.body().addWidget(muted(facts))
        if row.pack.license_note:
            card.body().addWidget(muted(row.pack.license_note))
        if row.error:
            card.body().addWidget(InfoBanner(row.error, severity="error"))
        if row.requires_restart:
            card.body().addWidget(
                InfoBanner(
                    "The pack is installed but its modules are not loaded yet. Restart Tixi Voice.",
                    severity="warning",
                )
            )
        buttons = QWidget()
        layout = QHBoxLayout(buttons)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        if row.installed:
            action = SecondaryButton("Remove", buttons, icon_name="trash")
            action.clicked.connect(lambda _=False, pack=row.id: self._remove_pack(pack))
            layout.addWidget(action)
        else:
            action = PrimaryButton("Install engine pack", buttons, icon_name="download")
            action.clicked.connect(lambda _=False, pack=row.id: self._install_pack(pack))
            layout.addWidget(action)
        hint = FlatButton("Manual install command", buttons, icon_name="terminal")
        hint.clicked.connect(lambda _=False, text=row.install_command: self._show_command(text))
        layout.addWidget(hint)
        layout.addStretch(1)
        card.body().addWidget(buttons)
        return card

    def _refresh_tiles(self) -> None:
        summary = self.context.models.summary()
        self._tile_models.set_value(str(summary.get("installed_count", 0)), "ready to use offline")
        self._tile_size.set_value(summary.get("installed_label", "0 B"), str(Path(summary.get("models_dir", "")).name))
        self._tile_packs.set_value(
            f"{summary.get('packs_installed', 0)}/{summary.get('packs_total', 0)}", "runtime bundles"
        )
        ready = []
        if summary.get("persian_tts_installed"):
            ready.append("TTS")
        if summary.get("persian_stt_installed"):
            ready.append("STT")
        if summary.get("diacritizer_installed"):
            ready.append("diacritization")
        self._tile_fa.set_value(", ".join(ready) or "not yet", "Persian features installed")

    # -- selection / details ------------------------------------------------
    def _selected_rows(self) -> list[Any]:
        indexes = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self._rows[index] for index in indexes if 0 <= index < len(self._rows)]

    def _on_selection(self) -> None:
        selection = self._selected_rows()
        selected = selection[0] if selection else None
        self._install.setEnabled(bool(selection))
        self._verify.setEnabled(bool(selection))
        self._import.setEnabled(len(selection) == 1)
        self._remove.setEnabled(any(row.installed for row in selection))
        if selected is None:
            self._title.setText("Nothing selected")
            self._description.setText("")
            self._facts.setText("")
            self._licence.setText("")
            self._notes.setText("")
            return
        row = selected
        self._title.setText(row.name)
        while self._badges.count():
            item = self._badges.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._badges.addWidget(Badge(row.category_label, "neutral"))
        self._badges.addWidget(Badge(row.persian_label, "info" if row.persian_capable else "neutral"))
        if row.recommended:
            self._badges.addWidget(Badge("recommended", "success"))
        self._badges.addWidget(Badge(row.status_label, "success" if row.installed else "neutral"))
        self._badges.addStretch(1)
        self._description.setText(row.spec.description)
        self._facts.setText(
            f"{row.download_label} download · {row.installed_label} installed · "
            f"engine: {row.spec.engine} · languages: {row.language_label}\n"
            f"hardware: {row.spec.hardware} · GPU: {row.spec.gpu} · "
            f"quantisation: {row.spec.quantization or 'n/a'}"
        )
        self._licence.setText(f"Licence: {row.spec.license or 'see the model page'}")
        self._notes.setText(row.spec.notes)

    def _show_details(self) -> None:
        selection = self._selected_rows()
        if not selection:
            return
        row = selection[0]
        spec = row.spec
        body = "\n".join(
            [
                f"Model: {spec.name}",
                f"ID: {spec.id}",
                f"Category: {row.category_label}",
                f"Engine: {spec.engine}",
                f"Languages: {row.language_label}",
                f"Persian support: {row.persian_label}",
                f"Verification: {spec.verified or 'not documented yet'}",
                f"Download size: {row.download_label}",
                f"Installed size: {row.installed_label}",
                f"Licence: {spec.license}",
                f"Licence URL: {spec.license_url}",
                f"Source: {spec.source_name} <{spec.source_url}>",
                f"Homepage: {spec.homepage}",
                f"Hardware: {spec.hardware}",
                f"GPU: {spec.gpu}",
                f"Quantisation: {spec.quantization or 'n/a'}",
                f"Gated download: {'yes — a Hugging Face token is required' if row.gated else 'no'}",
                "",
                spec.description,
                "",
                spec.notes,
            ]
        )
        DetailDialog(spec.name, body, self).exec()

    def _show_usage(self) -> None:
        selection = self._selected_rows()
        if not selection:
            return
        row = selection[0]
        where = {
            "tts_voice": "Text to Speech page, voice picker.",
            "tts_multilingual": "Text to Speech page (switch the language to English).",
            "stt": "Speech to Text page and Voice Typing (model picker).",
            "diacritization": "Diacritization, enabled per document on the Text to Speech page.",
            "normalization": "Always active when Persian normalisation is on.",
            "noise_reduction": "Audio pre-processing switch on the Speech to Text page.",
            "diarization": "Speaker labels in transcripts (in development).",
        }.get(row.category, "See the page that mentions this model.")
        DetailDialog(f"Where {row.name} is used", where, self).exec()

    def _open_page(self) -> None:
        selection = self._selected_rows()
        if not selection:
            return
        url = selection[0].spec.source_url or selection[0].spec.homepage
        if url:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl

            QDesktopServices.openUrl(QUrl(url))

    # -- operations ---------------------------------------------------------
    def _install_pack(self, pack_id: str) -> None:
        self._start_job(
            f"Installing the {pack_id} engine pack",
            lambda progress, cancel: self.context.models.install_pack(pack_id, progress=progress),
            kind=f"pack:{pack_id}",
        )

    def _remove_pack(self, pack_id: str) -> None:
        from ..widgets.toast import confirm

        accepted, _checked = confirm(
            self,
            "Remove the engine pack?",
            f"The {pack_id} runtime will be deleted. Models that need it will stop working until it is "
            "installed again.",
            confirm_text="Remove",
            destructive=True,
        )
        if not accepted:
            return
        if self.context.models.remove_pack(pack_id):
            self.toast("Engine pack removed", f"{pack_id} was deleted.", severity="success")
            self.refresh()

    def _install_selected(self) -> None:
        selection = [row for row in self._selected_rows() if not row.installed]
        if not selection:
            return
        row = selection[0]
        if row.gated:
            from ..widgets.toast import ask_text

            token = ask_text(
                self,
                "Hugging Face token",
                f"{row.name} is a gated model: accept its licence on Hugging Face, then paste an "
                "access token here. The token is only used for this download.",
                placeholder="hf_…",
            )
            if token:
                self.context.models.set_token(token)
        self._start_job(
            f"Installing {row.name}",
            lambda progress, cancel: self.context.models.install_model(row.id, progress=progress),
            kind=f"model:{row.id}",
            model_id=row.id,
        )

    def _remove_selected(self) -> None:
        from ..widgets.toast import confirm

        selection = [row for row in self._selected_rows() if row.installed]
        if not selection:
            return
        names = ", ".join(row.name for row in selection)
        accepted, delete_files = confirm(
            self,
            "Remove installed models?",
            f"{names} will be unregistered.",
            detail="Nothing is deleted from disk unless you tick the box.",
            checkbox_text="Also delete the model files from disk",
            confirm_text="Remove",
            destructive=True,
        )
        if not accepted:
            return
        removed = 0
        for row in selection:
            if self.context.models.remove_model(row.id, delete_files=delete_files):
                removed += 1
        self.toast("Removed", f"{removed} model(s) unregistered.", severity="success")
        self.refresh()

    def _verify_selected(self) -> None:
        selection = [row for row in self._selected_rows() if row.installed]
        if not selection:
            self.toast("Nothing to verify", "Install a model first.", severity="info")
            return
        row = selection[0]
        self._start_job(
            f"Verifying {row.name}",
            lambda progress, cancel: self.context.models.verify_model(row.id, deep=True),
            kind=f"verify:{row.id}",
            model_id=row.id,
            verify=True,
        )

    def _import_selected(self) -> None:
        selection = self._selected_rows()
        if len(selection) != 1:
            return
        row = selection[0]
        path = QFileDialog.getExistingDirectory(self, f"Choose the folder with {row.name} files")
        if not path:
            return
        self._start_job(
            f"Importing {row.name}",
            lambda progress, cancel: self.context.models.import_model(row.id, Path(path), progress=progress),
            kind=f"import:{row.id}",
            model_id=row.id,
        )

    def _start_job(self, label: str, function: Any, *, kind: str, model_id: str = "", verify: bool = False) -> None:
        if self.context.jobs.is_running(kind):
            self.toast("Already running", "That operation is already in progress.", severity="info")
            return
        self._cancel.setEnabled(True)
        self._progress_label = QLabel(label)
        self._banner.set_message(label, severity="info")
        self._banner.setVisible(True)
        self._install.setEnabled(False)
        self._job_id = self.context.jobs.submit(
            label,
            function,
            kind=kind,
            exclusive=True,
            on_progress=self._on_progress,
            on_finished=lambda payload: self._on_job_done(payload, model_id=model_id, verify=verify),
            on_failed=self._on_job_failed,
            on_cancelled=self._on_job_cancelled,
        )

    def _on_progress(self, payload: dict[str, Any]) -> None:
        detail = str(payload.get("detail") or "")
        fraction = float(payload.get("progress", 0.0))
        self._banner.set_message(
            f"{payload.get('label', 'Working')} — {int(fraction * 100)}%{(' · ' + detail) if detail else ''}",
            severity="info",
        )

    def _on_job_done(self, payload: dict[str, Any], *, model_id: str, verify: bool) -> None:
        self._cancel.setEnabled(False)
        result = payload.get("result")
        if verify and result is not None:
            ok = bool(getattr(result, "ok", False))
            summary = str(getattr(result, "summary", lambda: "")())
            self.toast(
                "Verification passed" if ok else "Verification found problems",
                summary,
                severity="success" if ok else "error",
            )
        else:
            summary = ""
            if result is not None:
                describe = getattr(result, "summary", None)
                if callable(describe):
                    try:
                        summary = str(describe())
                    except Exception:  # noqa: BLE001
                        summary = ""
            self.toast("Finished", summary or "The operation completed.", severity="success")
        self.refresh()

    def _on_job_failed(self, payload: dict[str, Any]) -> None:
        self._cancel.setEnabled(False)
        error = str(payload.get("error", ""))
        friendly = self.context.models.describe_install_error(error)
        self._banner.set_message(friendly, severity="error")
        self._banner.setVisible(True)
        self.toast("Failed", friendly, severity="error", detail=error)
        self.refresh()

    def _on_job_cancelled(self, payload: dict[str, Any]) -> None:
        self._cancel.setEnabled(False)
        self.toast(
            "Cancelled",
            "The download stopped. Partial files are kept so the next attempt resumes.",
            severity="info",
        )
        self.refresh()

    def _cancel_job(self) -> None:
        """Stop the running download/verification (wired to the Cancel button)."""
        self.context.models.cancel()
        if self._job_id:
            self.context.jobs.cancel(self._job_id)

    def _show_command(self, command: str) -> None:
        DetailDialog(
            "Manual installation",
            "You can also install the runtime yourself with pip, if you prefer to manage Python "
            "packages by hand:\n\n"
            f"    {command}\n\n"
            "Tixi Voice never runs pip for you — engine packs are downloaded as wheel files, their "
            "SHA-256 is verified against PyPI, and they are unpacked into the application's data "
            "folder.",
            self,
        ).exec()
