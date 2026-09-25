"""About: version, licences, environment and support information."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...app.config import APP_BUILD, APP_VERSION, GITHUB_URL
from ...app.paths import IS_FROZEN, IS_WINDOWS
from ...utils.humanize import human_size
from ..theme.icons import logo_pixmap, window_icon
from ..widgets.base import Card, HeroPanel, InfoBanner, SectionHeader
from ..widgets.buttons import FlatButton, PrimaryButton, SecondaryButton
from ..widgets.toast import DetailDialog, licence_viewer
from .base import Page, button_row, labelled_row, muted

LICENCES: tuple[tuple[str, str], ...] = (
    ("Tixi Voice", "MIT — this application's own source code."),
    ("PySide6 / Qt", "LGPL-3.0 (Qt for Python). Qt itself is used under the LGPL v3 with the "
                     "relinking exception that PySide6 provides."),
    ("numpy", "BSD-3-Clause"),
    ("sounddevice", "MIT (PortAudio: MIT-style licence)"),
    ("soundfile", "BSD-3-Clause (libsndfile: LGPL-2.1)"),
    ("requests", "Apache-2.0"),
    ("lameenc", "LGPL-3.0 (LAME is LGPL)"),
    ("onnxruntime", "MIT (engine pack, downloaded on demand)"),
    ("Piper (piper1-gpl)", "GPL-3.0-or-later — installed as a separate engine pack, with its "
                           "licence text, and never bundled into the application installer."),
    ("faster-whisper / CTranslate2", "MIT — installed as a separate engine pack."),
    ("OpenAI Whisper weights", "Apache-2.0"),
    ("Piper voices (fa_IR)", "Model cards published by rhasspy/piper-voices; the Persian voices are "
                             "derived from openly licensed datasets."),
    ("CANINE diacritizer", "MIT (google/canine-s base model is Apache-2.0)."),
    ("FFmpeg", "Optional external tool for Opus/M4A export — LGPL/GPL depending on the build you install."),
)


class AboutPage(Page):
    """Everything a user (or a support request) needs to know."""

    title = "About"
    subtitle = "Offline speech tools built for Persian — no account, no cloud, no telemetry."
    icon_name = "info"

    def build(self) -> None:
        hero = HeroPanel(
            "Tixi Voice",
            "A local voice productivity suite: text to speech, transcription, voice typing and "
            "context-aware Persian diacritization. Everything runs on this computer.",
            eyebrow=f"Version {APP_VERSION} · build {APP_BUILD}",
        )
        hero.add_action(PrimaryButton("Copy diagnostics", hero, icon_name="clipboard"))
        hero.add_action(SecondaryButton("Open GitHub", hero, icon_name="globe"))
        buttons = hero.findChildren(PrimaryButton) + hero.findChildren(SecondaryButton)
        if buttons:
            buttons[0].clicked.connect(self._copy_diagnostics)
        if len(buttons) > 1:
            buttons[1].clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB_URL)))
        self._hero = hero
        self.add(hero)

        self._banner = InfoBanner("")
        self._banner.setVisible(False)
        self.add(self._banner)

        facts = Card()
        facts.body().addWidget(SectionHeader("Build", icon_name="box"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(4)
        self._facts_grid = grid
        facts.body().addLayout(grid)
        self.add(facts)

        licensing = Card()
        licensing.body().addWidget(
            SectionHeader(
                "Licences",
                "Tixi Voice ships no GPL code. GPL-licensed engines (Piper) are downloaded as a "
                "separate pack with their licence text.",
                icon_name="lock",
            )
        )
        self._licence_holder = QVBoxLayout()
        self._licence_holder.setSpacing(4)
        licensing.body().addLayout(self._licence_holder)
        show_all = FlatButton("Show all licence texts", licensing, icon_name="list")
        show_all.clicked.connect(self._show_licences)
        licensing.body().addLayout(button_row(show_all, stretch=False))
        self.add(licensing)

        data_card = Card()
        data_card.body().addWidget(SectionHeader("Your data", icon_name="folder"))
        self._paths_holder = QVBoxLayout()
        self._paths_holder.setSpacing(4)
        data_card.body().addLayout(self._paths_holder)
        self.add(data_card)

        support = Card()
        support.body().addWidget(SectionHeader("Support", icon_name="info"))
        support.body().addWidget(
            muted(
                "When reporting a problem, include the diagnostics text (it contains versions and "
                "paths only — never your text or audio). Logs never contain recognised text unless "
                "you enable content logging in Settings ▸ Privacy."
            )
        )
        buttons_row = QWidget()
        layout = QHBoxLayout(buttons_row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for text, icon, callback in (
            ("Copy diagnostics", "clipboard", self._copy_diagnostics),
            ("Open log folder", "folder", self._open_logs),
            ("Troubleshooting guide", "book", self._open_troubleshooting),
        ):
            button = FlatButton(text, buttons_row, icon_name=icon)
            button.clicked.connect(callback)
            layout.addWidget(button)
        layout.addStretch(1)
        support.body().addWidget(buttons_row)
        self.add(support)

    # -- lifecycle ----------------------------------------------------------
    def refresh(self) -> None:
        self.ensure_built()
        while self._facts_grid.count():
            item = self._facts_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        rows = self._build_facts()
        for index, (label, value) in enumerate(rows):
            name = QLabel(label)
            name.setObjectName("Caption")
            amount = QLabel(str(value))
            amount.setObjectName("MonoSmall")
            amount.setWordWrap(True)
            amount.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._facts_grid.addWidget(name, index, 0, Qt.AlignmentFlag.AlignTop)
            self._facts_grid.addWidget(amount, index, 1)

        while self._licence_holder.count():
            item = self._licence_holder.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for name, text in LICENCES[:6]:
            line = QLabel(f"• {name} — {text}")
            line.setObjectName("Caption")
            line.setWordWrap(True)
            self._licence_holder.addWidget(line)

        while self._paths_holder.count():
            item = self._paths_holder.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        paths = self.context.paths
        for label, path in (
            ("Data folder", paths.root),
            ("Settings", paths.config_root),
            ("Models", paths.models),
            ("Engine packs", paths.engine_packs),
            ("Recordings", paths.recordings),
            ("Logs", paths.logs),
        ):
            self._paths_holder.addWidget(labelled_row(label, muted(str(path))))

        notes = [note for note in (self.context.notes or []) if note]
        if notes:
            self._banner.set_message(" · ".join(notes[:3]), severity="warning")
            self._banner.setVisible(True)
        else:
            self._banner.setVisible(False)

    def _build_facts(self) -> list[tuple[str, str]]:
        from ...app.bootstrap import describe_environment

        environment = describe_environment()
        packages = {
            name: version
            for name, version in environment["packages"].items()
            if version
        }
        missing = [name for name, version in environment["packages"].items() if not version]
        summary = self.context.models.summary()
        facts = [
            ("Tixi Voice", f"{APP_VERSION} (build {APP_BUILD})"),
            ("Python", environment["python"]),
            ("Operating system", environment["platform"]),
            ("Packaged build", "yes" if IS_FROZEN else "no (running from source)"),
            ("Windows features", "available" if IS_WINDOWS else "not on this platform"),
            ("Installed models", f"{summary.get('installed_count', 0)} · {summary.get('installed_label', '0 B')}"),
            ("Engine packs", f"{summary.get('packs_installed', 0)} of {summary.get('packs_total', 0)}"),
            ("Diacritizer", "neural (CANINE ONNX)" if summary.get("diacritizer_installed") else "built-in lexicon"),
            ("Modules present", ", ".join(sorted(packages)) or "none"),
            ("Modules missing", ", ".join(sorted(missing)) or "none"),
        ]
        return facts

    # -- actions ------------------------------------------------------------
    def _diagnostics(self) -> str:
        from ...app.bootstrap import describe_environment

        environment = describe_environment()
        summary = self.context.models.summary()
        paths = self.context.paths
        payload = {
            "application": {"version": APP_VERSION, "build": APP_BUILD, "frozen": IS_FROZEN},
            "environment": environment,
            "models": summary,
            "paths": {
                "data": str(paths.root),
                "config": str(paths.config_root),
                "models": str(paths.models),
                "logs": str(paths.logs),
            },
            "engine": self.context.engine_manager.availability()
            if hasattr(self.context.engine_manager, "availability")
            else {},
            "diacritization": self.context.diacritization.describe(),
            "startup_notes": list(self.context.notes or []),
            "python_path_entries": len(sys.path),
            "machine": platform.machine(),
        }
        try:
            payload["hardware"] = self.context.engine_manager.hardware.as_dict()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    def _copy_diagnostics(self) -> None:
        from PySide6.QtWidgets import QApplication

        text = self._diagnostics()
        QApplication.clipboard().setText(text)
        self.toast("Diagnostics copied", f"{len(text)} characters on the clipboard.", severity="success")

    def _show_licences(self) -> None:
        body = "\n\n".join(f"{name}\n{'-' * len(name)}\n{text}" for name, text in LICENCES)
        body += (
            "\n\nThird-party components installed as engine packs carry their own licence files, "
            "which are kept next to the pack in the application data folder."
        )
        licence_viewer("Licences", body, self)

    def _open_logs(self) -> None:
        from ...utils.win_integration import open_in_explorer

        open_in_explorer(self.context.paths.logs)

    def _open_troubleshooting(self) -> None:
        candidate = Path(__file__).resolve().parents[3].parent / "docs" / "TROUBLESHOOTING.md"
        if candidate.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate)))
            return
        DetailDialog(
            "Troubleshooting",
            "The troubleshooting guide ships next to the application (docs/TROUBLESHOOTING.md) and "
            f"online at {GITHUB_URL}/blob/main/docs/TROUBLESHOOTING.md.",
            self,
        ).exec()
