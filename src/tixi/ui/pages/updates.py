"""Application Updates: check GitHub Releases, verify and install."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...app.config import APP_BUILD, APP_VERSION, GITHUB_URL
from ...utils.humanize import human_datetime, human_size
from ..widgets.base import Badge, Card, InfoBanner, SectionHeader, StatTile
from ..widgets.buttons import FlatButton, PrimaryButton, SecondaryButton
from ..widgets.inputs import ComboRow, Switch
from ..widgets.text_views import rtl_text_view
from .base import Page, button_row, labelled_row, muted


class UpdatesPage(Page):
    """Keep the application itself up to date — with the user in control."""

    title = "Application Updates"
    subtitle = f"Releases are published on GitHub ({GITHUB_URL.split('//')[-1]})."
    icon_name = "download"

    def build(self) -> None:
        self._result: Any = None
        self._package: Any = None

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self._tile_version = StatTile("Installed version", APP_VERSION, icon_name="box", caption=f"build {APP_BUILD}")
        self._tile_status = StatTile("Status", "Not checked", icon_name="info")
        self._tile_checked = StatTile("Last check", "never", icon_name="clock")
        self._tile_size = StatTile("Update size", "—", icon_name="download")
        for tile in (self._tile_version, self._tile_status, self._tile_checked, self._tile_size):
            tiles.addWidget(tile)
        self.add_layout(tiles)

        self._banner = InfoBanner("", severity="info")
        self.add(self._banner)

        settings_card = Card()
        settings_card.body().addWidget(SectionHeader("Update settings", icon_name="settings"))
        self._channel = ComboRow([("Stable releases only", "stable"), ("Include betas and pre-releases", "beta")])
        self._channel.set_current(str(self.context.settings.updates.channel or "stable"))
        self._channel.changed.connect(lambda _key: self._save())
        settings_card.body().addWidget(labelled_row("Channel", self._channel))
        self._auto = Switch(bool(self.context.settings.updates.auto_check), label="Auto")
        self._auto.toggled.connect(lambda _state: self._save())
        settings_card.body().addWidget(
            labelled_row(
                "Check automatically",
                self._auto,
                hint="A single request to the GitHub API per interval — never a background download.",
            )
        )
        self._notify = Switch(bool(self.context.settings.updates.notify), label="Notify")
        self._notify.toggled.connect(lambda _state: self._save())
        settings_card.body().addWidget(labelled_row("Notify me when an update is available", self._notify))
        self._verify = Switch(bool(self.context.settings.updates.verify_signature), label="Verify")
        self._verify.toggled.connect(lambda _state: self._save())
        settings_card.body().addWidget(
            labelled_row(
                "Verify the SHA-256 of the installer",
                self._verify,
                hint="Always recommended: a mismatching download is deleted instead of being run.",
            )
        )
        interval = ComboRow([("Every 6 hours", "6"), ("Every 12 hours", "12"), ("Daily", "24"), ("Weekly", "168")])
        interval.set_current(str(self.context.settings.updates.check_interval_hours or 24))
        interval.changed.connect(lambda key: self._save_interval(key))
        settings_card.body().addWidget(labelled_row("Check interval", interval))
        self._skipped = muted("")
        settings_card.body().addWidget(self._skipped)
        skip_button = FlatButton("Forget the skipped version", settings_card, icon_name="refresh")
        skip_button.clicked.connect(self._forget_skipped)
        settings_card.body().addLayout(button_row(skip_button, stretch=False))
        self.add(settings_card)

        actions_card = Card()
        actions_card.body().addWidget(SectionHeader("Check for updates", icon_name="refresh"))
        self._check = PrimaryButton("Check now", actions_card, icon_name="refresh")
        self._check.clicked.connect(self._check_now)
        self._download = SecondaryButton("Download and verify", actions_card, icon_name="download")
        self._download.clicked.connect(self._download_now)
        self._download.setEnabled(False)
        self._install = PrimaryButton("Install and restart", actions_card, icon_name="check")
        self._install.clicked.connect(self._install_now)
        self._install.setEnabled(False)
        self._skip = FlatButton("Skip this version", actions_card, icon_name="stop")
        self._skip.clicked.connect(self._skip_version)
        self._skip.setEnabled(False)
        actions_card.body().addLayout(button_row(self._check, self._download, self._install, self._skip))
        self._progress = muted("")
        actions_card.body().addWidget(self._progress)
        self._manual = FlatButton("Open the releases page", actions_card, icon_name="globe")
        self._manual.clicked.connect(self._open_manual)
        actions_card.body().addLayout(button_row(self._manual, stretch=False))
        self.add(actions_card)

        notes_card = Card()
        notes_card.body().addWidget(SectionHeader("Release notes", icon_name="list"))
        self._notes = rtl_text_view("", read_only=True)
        self._notes.setMinimumHeight(220)
        notes_card.body().addWidget(self._notes)
        self.add(notes_card)

        cache_card = Card()
        cache_card.body().addWidget(SectionHeader("Downloaded installers", icon_name="folder"))
        self._cache_info = muted("")
        cache_card.body().addWidget(self._cache_info)
        clean = SecondaryButton("Delete downloaded installers", cache_card, icon_name="trash")
        clean.clicked.connect(self._clean_cache)
        cache_card.body().addLayout(button_row(clean, stretch=False))
        self.add(cache_card)

        policy = Card()
        policy.body().addWidget(SectionHeader("How updating works", icon_name="lock"))
        for line in (
            "Updates are checked against the GitHub Releases API of TiXi-Ai/Tixi-Voice only.",
            "Nothing is downloaded until you press “Download and verify”.",
            "The installer runs only after you confirm it, and only when the SHA-256 matches the "
            "checksum published in the release.",
            "In Offline Mode no request is made at all; the releases page stays available for manual "
            "downloads.",
        ):
            text = QLabel(f"• {line}")
            text.setObjectName("Caption")
            text.setWordWrap(True)
            policy.body().addWidget(text)
        self.add(policy)

    # -- lifecycle ----------------------------------------------------------
    def refresh(self) -> None:
        self.ensure_built()
        summary = self.context.updates.last_summary
        self._tile_status.set_value(_status_label(summary.status), summary.detail or "—")
        self._tile_checked.set_value(
            human_datetime(summary.checked_at) if summary.checked_at else "never",
            str(self.context.settings.updates.last_check or ""),
        )
        self._tile_size.set_value(summary.download_label or "—", summary.latest_version or "")
        skipped = str(self.context.settings.updates.skipped_version or "")
        self._skipped.setText(
            f"Version {skipped} is currently skipped." if skipped else "No version is skipped."
        )
        info = self.context.updates.build_info()
        self._cache_info.setText(
            f"Cache folder: {info.get('cache_dir', '')} · {human_size(int(info.get('cache_size_bytes', 0) or 0))}"
        )
        self._check.setEnabled(self.context.updates.network_allowed())
        if not self.context.updates.network_allowed():
            self._banner.set_message(
                "Offline Mode is on, so no update checks are performed. You can still download a new "
                "version from the releases page.",
                severity="info",
            )
            self._banner.setVisible(True)
        elif summary.status in ("available",):
            self._banner.set_message(
                f"Version {summary.latest_version} is available. Download it, then install when you are ready.",
                severity="info",
            )
            self._banner.setVisible(True)
        else:
            self._banner.setVisible(bool(summary.detail))
            self._banner.set_message(summary.detail, severity="success" if summary.status == "up-to-date" else "info")

    # -- settings -----------------------------------------------------------
    def _save(self) -> None:
        self.context.settings_store.update(
            {
                "updates.channel": str(self._channel.current_key() or "stable"),
                "updates.auto_check": self._auto.isChecked(),
                "updates.notify": self._notify.isChecked(),
                "updates.verify_signature": self._verify.isChecked(),
            }
        )
        self.context.save_settings()

    def _save_interval(self, key: str) -> None:
        try:
            hours = int(key)
        except (TypeError, ValueError):
            return
        self.context.settings_store.set("updates.check_interval_hours", hours)
        self.context.save_settings()

    def _forget_skipped(self) -> None:
        self.context.settings_store.set("updates.skipped_version", "")
        self.context.save_settings()
        self.refresh()
        self.toast("Done", "Skipped version cleared — the next check will offer it again.", severity="success")

    # -- actions ------------------------------------------------------------
    def _check_now(self) -> None:
        self._check.disable_with_reason("Checking…")
        self._progress.setText("Contacting GitHub…")
        self.context.jobs.submit(
            "Checking for updates",
            self._check_job,
            kind="update-check",
            exclusive=True,
            on_finished=self._on_check_done,
            on_failed=self._on_check_failed,
        )

    def _check_job(self, *, progress: Any = None, cancel: Any = None) -> Any:
        result = self.context.updates.check()
        self.context.settings_store.set("updates.last_check", self.context.updates.last_summary.checked_at)
        self.context.save_settings()
        return result

    def _on_check_done(self, payload: dict[str, Any]) -> None:
        self._check.enable()
        self._progress.setText("")
        summary = self.context.updates.last_summary
        self._tile_status.set_value(_status_label(summary.status), summary.detail)
        self._notes.set_text(summary.notes or "This release has no notes.")
        self._download.setEnabled(bool(summary.update_available))
        self._skip.setEnabled(bool(summary.update_available))
        self._install.setEnabled(False)
        self._tile_size.set_value(summary.download_label or "—", summary.latest_version or "")
        if summary.status == "available":
            self.toast("Update available", summary.detail, severity="info")
        elif summary.status in ("offline", "rate-limited", "error"):
            self.toast("Check failed", summary.detail, severity="warning")
        else:
            self.toast("Up to date", summary.detail or "No newer release was found.", severity="success")
        self.refresh()

    def _on_check_failed(self, payload: dict[str, Any]) -> None:
        self._check.enable()
        self._progress.setText("")
        self.toast("Check failed", str(payload.get("error", "")), severity="error")

    def _download_now(self) -> None:
        self._download.disable_with_reason("Downloading…")
        self.context.jobs.submit(
            "Downloading the update",
            self._download_job,
            kind="update-download",
            exclusive=True,
            on_progress=self._on_download_progress,
            on_finished=self._on_download_done,
            on_failed=self._on_download_failed,
        )

    def _download_job(self, *, progress: Any = None, cancel: Any = None) -> Any:
        return self.context.updates.download(progress=progress)

    def _on_download_progress(self, payload: dict[str, Any]) -> None:
        fraction = float(payload.get("progress", 0.0))
        detail = str(payload.get("detail") or "")
        self._progress.setText(f"{int(fraction * 100)}% {detail}")

    def _on_download_done(self, payload: dict[str, Any]) -> None:
        self._download.enable()
        package = payload.get("result")
        self._package = package
        self._progress.setText("")
        if package is None:
            return
        trust = str(getattr(package, "trust_summary", lambda: "")())
        self.toast("Download verified", trust or "The installer was downloaded and verified.", severity="success")
        possible, reason = self.context.updates.can_install()
        self._install.setEnabled(possible)
        if not possible:
            self._install.disable_with_reason(reason)
            self._progress.setText(reason)
        else:
            self._progress.setText("Ready to install. Tixi Voice will close while the installer runs.")

    def _on_download_failed(self, payload: dict[str, Any]) -> None:
        self._download.enable()
        self._progress.setText("")
        self.toast("Download failed", str(payload.get("error", "")), severity="error")

    def _install_now(self) -> None:
        from ..widgets.toast import confirm

        accepted, _checked = confirm(
            self,
            "Install the update now?",
            "Tixi Voice will close, run the verified installer and then you can start it again.",
            detail="The installer is only launched after you confirm here.",
            confirm_text="Install and close",
        )
        if not accepted:
            return
        try:
            self.context.updates.install(self._package, on_before_exit=self._before_exit)
        except Exception as exc:  # noqa: BLE001
            self.toast("Installation refused", str(exc), severity="error")
            return
        window = self.window()
        hook = getattr(window, "quit_application", None)
        if callable(hook):
            hook()

    def _before_exit(self) -> None:
        """Give the services a chance to flush before the installer replaces files."""
        try:
            self.context.save_settings()
        except Exception:  # noqa: BLE001
            pass

    def _skip_version(self) -> None:
        version = self.context.updates.last_summary.latest_version
        if not version:
            return
        self.context.settings_store.set("updates.skipped_version", version)
        self.context.save_settings()
        self.refresh()
        self.toast("Skipped", f"Version {version} will not be offered again.", severity="info")

    def _clean_cache(self) -> None:
        from ..widgets.toast import confirm

        accepted, _checked = confirm(
            self,
            "Delete the downloaded installers?",
            "Downloaded update packages in the cache folder will be removed. The verified package you "
            "just downloaded is kept.",
            confirm_text="Delete",
            destructive=True,
        )
        if not accepted:
            return
        removed = self.context.updates.clean_cache(keep_latest=True)
        self.toast("Cache cleaned", f"{removed} file(s) deleted.", severity="success")
        self.refresh()

    def _open_manual(self) -> None:
        QDesktopServices.openUrl(QUrl(GITHUB_URL + "/releases"))


def _status_label(status: str) -> str:
    return {
        "available": "Update available",
        "up-to-date": "Up to date",
        "offline": "Offline",
        "rate-limited": "Rate limited",
        "error": "Check failed",
        "skipped": "Skipped",
        "unknown": "Not checked",
    }.get(status, status or "Not checked")
