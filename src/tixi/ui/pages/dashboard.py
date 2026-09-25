"""Home dashboard: what is ready, what is missing, and what to do next."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ...app.config import APP_VERSION
from ...engines.manager import EngineManager
from ...utils.humanize import human_size, human_time_ago
from ..theme.icons import icon_pixmap
from ..widgets.base import Badge, Card, EmptyState, HeroPanel, InfoBanner, SectionHeader, StatTile
from ..widgets.buttons import FlatButton, PrimaryButton, SecondaryButton
from .base import Page, caption, muted


class DashboardPage(Page):
    """Landing page: setup checklist, quick actions and recent activity."""

    title = "Home"
    subtitle = "Everything runs on this machine — no cloud, no account."
    icon_name = "home"

    def build(self) -> None:
        self._hero = HeroPanel(
            "Tixi Voice",
            "Offline speech tools for Persian and English: text to speech, transcription, "
            "voice typing and context-aware diacritization.",
            eyebrow=f"Version {APP_VERSION}",
        )
        self._hero.add_action(PrimaryButton("New speech", self, icon_name="speaker"))
        self._hero.add_action(SecondaryButton("Transcribe audio", self, icon_name="mic"))
        self._hero.add_action(SecondaryButton("Dictate", self, icon_name="keyboard"))
        self.add(self._hero)
        buttons = self._hero.findChildren(PrimaryButton) + self._hero.findChildren(SecondaryButton)
        for button, target in zip(buttons, ("text_to_speech", "speech_to_text", "voice_typing")):
            button.clicked.connect(lambda _=False, page=target: self.goto(page))

        self._setup_card = Card()
        self._setup_card.body().addWidget(
            SectionHeader(
                "Setup checklist",
                "Tixi Voice works offline once the models below are installed.",
                icon_name="check",
            )
        )
        self._setup_holder = QVBoxLayout()
        self._setup_holder.setSpacing(6)
        self._setup_card.body().addLayout(self._setup_holder)
        self._setup_card.body().addWidget(
            self._make_actions(
                ("Open AI Models", lambda: self.goto("models"), True),
                ("Open Settings", lambda: self.goto("settings"), False),
            )
        )
        self.add(self._setup_card)

        tiles = QGridLayout()
        tiles.setSpacing(12)
        self._tile_models = StatTile("Installed models", "—", icon_name="box", caption="ready to use offline")
        self._tile_history = StatTile("History entries", "—", icon_name="clock", caption="text and audio you created")
        self._tile_library = StatTile("Audio library", "—", icon_name="music", caption="clips stored on this PC")
        self._tile_dictation = StatTile("Dictation", "—", icon_name="keyboard", caption="sessions this run")
        for index, tile in enumerate((self._tile_models, self._tile_history, self._tile_library, self._tile_dictation)):
            tiles.addWidget(tile, 0, index)
        self.add_layout(tiles)

        bottom = QHBoxLayout()
        bottom.setSpacing(14)

        recent_card = Card()
        recent_card.body().addWidget(SectionHeader("Recent activity", icon_name="clock"))
        self._recent = QListWidget()
        self._recent.setObjectName("RecentList")
        self._recent.setMinimumHeight(180)
        self._recent.itemDoubleClicked.connect(self._open_recent)
        recent_card.body().addWidget(self._recent)
        self._recent_empty = muted("Nothing yet — generate speech or transcribe a file to see it here.")
        recent_card.body().addWidget(self._recent_empty)
        recent_card.body().addWidget(
            self._make_actions(("Open history", lambda: self.goto("history"), False))
        )
        bottom.addWidget(recent_card, 3)

        storage_card = Card()
        storage_card.body().addWidget(SectionHeader("Storage", icon_name="folder"))
        self._storage = QVBoxLayout()
        self._storage.setSpacing(6)
        storage_card.body().addLayout(self._storage)
        storage_card.body().addWidget(
            self._make_actions(
                ("Open models folder", self._open_models_folder, False),
                ("Reveal data folder", self._open_data_folder, False),
            )
        )
        bottom.addWidget(storage_card, 2)
        self.add_layout(bottom)

        self._notes = Card()
        self._notes.body().addWidget(SectionHeader("Startup notes", icon_name="info"))
        self._notes_holder = QVBoxLayout()
        self._notes_holder.setSpacing(6)
        self._notes.body().addLayout(self._notes_holder)
        self._notes.setVisible(False)
        self.add(self._notes)

    # -- data ---------------------------------------------------------------
    def refresh(self) -> None:
        self.ensure_built()
        self._refresh_setup()
        self._refresh_tiles()
        self._refresh_recent()
        self._refresh_storage()
        self._refresh_notes()

    def _refresh_setup(self) -> None:
        _clear(self._setup_holder)
        context = self.context
        for check in self._setup_checks():
            self._setup_holder.addWidget(check)
        blocked = [note for note in (context.notes or []) if "not installed" in note.lower()]
        if blocked:
            banner = InfoBanner(
                "Some engines are not installed yet. Open AI Models to install them — "
                "the application keeps working and explains what is missing.",
                severity="warning",
                action_text="Open AI Models",
            )
            banner.action_clicked.connect(lambda: self.goto("models"))
            self._setup_holder.addWidget(banner)

    def _setup_checks(self) -> list[QWidget]:
        context = self.context
        rows: list[QWidget] = []
        summary = context.models.summary()
        packs = {row.id: row for row in context.models.pack_rows()}

        rows.append(
            _check_row(
                "Persian text-to-speech engine",
                bool(packs.get("piper") and packs["piper"].installed),
                "Piper runtime installed" if packs.get("piper") and packs["piper"].installed
                else "Needs the Piper engine pack (~15 MB)",
                lambda: self.goto("models"),
            )
        )
        rows.append(
            _check_row(
                "Persian voice model",
                summary.get("persian_tts_installed", False),
                "A Persian voice is installed (Amir / Gyro)"
                if summary.get("persian_tts_installed")
                else "Download a Persian voice (~63 MB)",
                lambda: self.goto("models"),
            )
        )
        rows.append(
            _check_row(
                "Speech recognition",
                bool(packs.get("faster-whisper") and packs["faster-whisper"].installed),
                "faster-whisper runtime installed"
                if packs.get("faster-whisper") and packs["faster-whisper"].installed
                else "Needs the faster-whisper engine pack",
                lambda: self.goto("models"),
            )
        )
        rows.append(
            _check_row(
                "Speech recognition model",
                summary.get("installed_count", 0) > 0 and summary.get("persian_stt_installed", False),
                "A Whisper model is installed"
                if summary.get("persian_stt_installed")
                else "Download Whisper small or above (~487 MB) for good Persian results",
                lambda: self.goto("models"),
            )
        )
        rows.append(
            _check_row(
                "Neural Persian diacritization",
                bool(summary.get("diacritizer_installed")),
                "CANINE diacritizer installed"
                if summary.get("diacritizer_installed")
                else "Optional — without it Tixi Voice uses the built-in lexicon and says so",
                lambda: self.goto("models"),
            )
        )
        health = _first_dictation_check(self.context)
        rows.append(_check_row(*health))
        return rows

    def _refresh_tiles(self) -> None:
        summary = self.context.models.summary()
        self._tile_models.set_value(
            str(summary.get("installed_count", 0)),
            f"{human_size(summary.get('installed_bytes', 0))} on disk",
        )
        stats = self.context.history.statistics()
        self._tile_history.set_value(str(stats.get("total", 0)), _kinds_caption(stats))
        library_count = self.context.library.count()
        self._tile_library.set_value(
            str(library_count), human_size(self.context.library.total_size())
        )
        dictation = self.context.dictation
        if dictation is None:
            self._tile_dictation.set_value("—", "voice typing is unavailable")
        else:
            counters = dictation.statistics()
            self._tile_dictation.set_value(
                str(counters.get("inserted", 0)),
                f"{counters.get('words', 0)} words · {counters.get('sessions', 0)} session(s)",
            )

    def _refresh_recent(self) -> None:
        self._recent.clear()
        entries = self.context.history.recent(limit=8)
        self._recent.setVisible(bool(entries))
        self._recent_empty.setVisible(not entries)
        for entry in entries:
            label = f"{entry.kind_label} · {human_time_ago(entry.created_at)}"
            item = QListWidgetItem(f"{label}\n{entry.preview[:110]}")
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            self._recent.addItem(item)

    def _refresh_storage(self) -> None:
        _clear(self._storage)
        context = self.context
        stats = context.database.stats() if hasattr(context.database, "stats") else {}
        database_bytes = int(stats.get("size_bytes", 0) or 0)
        rows = [
            ("Database", f"{human_size(database_bytes)} · {context.paths.database_file.name}"),
            ("Models", human_size(context.model_registry.total_size())),
            ("Engine packs", human_size(_dir_size(context.paths.engine_packs))),
            ("Audio library", human_size(context.library.total_size())),
            ("Recordings", human_size(_dir_size(context.paths.recordings))),
            ("Logs", human_size(_dir_size(context.paths.logs))),
        ]
        for label, value in rows:
            line = QWidget()
            layout = QHBoxLayout(line)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            name = QLabel(label)
            name.setObjectName("Caption")
            amount = QLabel(value)
            amount.setObjectName("TitleLabel")
            layout.addWidget(name, 1)
            layout.addWidget(amount, 0, Qt.AlignmentFlag.AlignRight)
            self._storage.addWidget(line)

    def _refresh_notes(self) -> None:
        _clear(self._notes_holder)
        notes = list(self.context.notes or [])
        self._notes.setVisible(bool(notes))
        for note in notes:
            banner = InfoBanner(note, severity="info")
            self._notes_holder.addWidget(banner)

    # -- actions ------------------------------------------------------------
    def _make_actions(self, *entries: tuple[str, Any, bool]) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for text, callback, primary in entries:
            button = PrimaryButton(text, row) if primary else SecondaryButton(text, row)
            button.clicked.connect(lambda _=False, cb=callback: cb())
            layout.addWidget(button)
        layout.addStretch(1)
        return row

    def _open_recent(self, item: QListWidgetItem) -> None:
        self.goto("history")

    def _open_models_folder(self) -> None:
        from ...utils.win_integration import open_in_explorer

        open_in_explorer(self.context.paths.models)

    def _open_data_folder(self) -> None:
        from ...utils.win_integration import open_in_explorer

        open_in_explorer(self.context.paths.root)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _check_row(label: str, ok: bool, detail: str, action: Any = None) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 2, 0, 2)
    layout.setSpacing(10)
    dot = QLabel("●" if ok else "○")
    dot.setStyleSheet("font-size: 15px;")
    dot.setObjectName("SuccessText" if ok else "WarningText")
    layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(1)
    name = QLabel(label)
    name.setObjectName("TitleLabel")
    column.addWidget(name)
    note = QLabel(detail)
    note.setObjectName("Caption")
    note.setWordWrap(True)
    column.addWidget(note)
    layout.addLayout(column, 1)
    if not ok and callable(action):
        button = FlatButton("Fix", row, icon_name="arrow-right")
        button.clicked.connect(lambda _=False: action())
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def _first_dictation_check(context: Any) -> tuple:
    dictation = getattr(context, "dictation", None)
    if dictation is None:
        return (
            "Voice typing",
            False,
            "Voice typing is unavailable on this system (Windows-only global shortcut).",
            None,
        )
    try:
        checks = dictation.health_check(context.settings_store.settings)
    except Exception as exc:  # noqa: BLE001
        return ("Voice typing", False, f"The dictation check failed: {exc}", None)
    mic = next((check for check in checks if check["label"] == "Microphone"), None)
    hotkey = next((check for check in checks if check["label"] == "Global shortcut"), None)
    if mic is None:
        return ("Voice typing", False, "No microphone was found.", None)
    ok = bool(mic["ok"]) and bool(hotkey["ok"] if hotkey else True)
    detail = mic["detail"]
    if hotkey is not None and not hotkey["ok"]:
        detail = f"{detail} · {hotkey['detail']}"
    return ("Voice typing", ok, detail, None)


def _kinds_caption(stats: dict[str, Any]) -> str:
    by_kind = stats.get("by_kind") or {}
    if not by_kind:
        return "nothing recorded yet"
    parts = [f"{row.get('c', 0)} {kind}" for kind, row in list(by_kind.items())[:3]]
    return " · ".join(parts)


def _clear(layout: Any) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _dir_size(path: Any) -> int:
    from ...utils.atomic import directory_size

    return directory_size(path)
