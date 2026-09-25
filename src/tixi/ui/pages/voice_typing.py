"""Voice typing: global push-to-talk dictation into any Windows application."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...app.config import STT_LANGUAGES
from ...utils.humanize import human_time_ago
from ...voice_typing.text_inserter import limitations, supported_applications
from ..widgets.base import Badge, Card, InfoBanner, SectionHeader
from ..widgets.buttons import FlatButton, PrimaryButton, SecondaryButton
from ..widgets.inputs import ComboRow, ShortcutEdit, SliderRow, Switch, TagInput
from ..widgets.meters import LevelMeter
from .base import Page, button_row, labelled_row, muted

MODES = [
    ("Push to talk (hold the shortcut)", "push_to_talk"),
    ("Toggle (press to start, press again to stop)", "toggle"),
    ("Push to talk with automatic punctuation", "push_to_talk_punctuation"),
    ("Continuous (keeps listening until stopped)", "continuous"),
]

INSERTION = [
    ("Automatic (recommended)", "auto"),
    ("Unicode typing only", "unicode"),
    ("Type keystrokes", "type"),
    ("Clipboard paste only", "clipboard"),
]


class VoiceTypingPage(Page):
    """Configure dictation and check that every piece of it works."""

    title = "Voice Typing"
    subtitle = "Hold a shortcut, speak, and the text is typed into whatever window has focus."
    icon_name = "keyboard"

    def build(self) -> None:
        self._banner = InfoBanner("", severity="info")
        self.add(self._banner)

        top = QHBoxLayout()
        top.setSpacing(14)

        shortcut_card = Card()
        shortcut_card.body().addWidget(SectionHeader("Shortcut", icon_name="keyboard"))
        self._shortcut = ShortcutEdit(str(self.context.settings.voice_typing.shortcut or "Ctrl+Shift+Space"))
        shortcut_card.body().addWidget(labelled_row("Global shortcut", self._shortcut))
        self._apply = PrimaryButton("Apply", shortcut_card, icon_name="check")
        self._apply.clicked.connect(self._apply_shortcut)
        self._test = SecondaryButton("Test conflict", shortcut_card, icon_name="search")
        self._test.clicked.connect(self._test_conflict)
        shortcut_card.body().addLayout(button_row(self._apply, self._test, stretch=False))
        self._shortcut_status = muted("")
        shortcut_card.body().addWidget(self._shortcut_status)
        top.addWidget(shortcut_card, 1)

        behaviour_card = Card()
        behaviour_card.body().addWidget(SectionHeader("Behaviour", icon_name="sliders"))
        self._enabled = Switch(bool(self.context.settings.voice_typing.enabled), label="Enabled")
        self._enabled.toggled.connect(self._apply_behaviour)
        behaviour_card.body().addWidget(
            labelled_row("Voice typing enabled", self._enabled, hint="The shortcut only works while this is on.")
        )
        self._mode = ComboRow(MODES)
        self._mode.set_current(str(self.context.settings.voice_typing.mode or "push_to_talk"))
        self._mode.changed.connect(lambda _key: self._apply_behaviour())
        behaviour_card.body().addWidget(labelled_row("Mode", self._mode))
        self._language = ComboRow([("Automatic detection", "auto")] + list(STT_LANGUAGES))
        self._language.set_current(str(self.context.settings.voice_typing.language or "fa"))
        self._language.changed.connect(lambda _key: self._apply_behaviour())
        behaviour_card.body().addWidget(labelled_row("Language", self._language))
        self._post = Switch(True, label="Post-process")
        behaviour_card.body().addWidget(
            labelled_row(
                "Persian post-processing",
                self._post,
                hint="Spacing, Arabic → Persian letters, punctuation and optional diacritization.",
            )
        )
        self._diacritize = Switch(bool(self.context.settings.voice_typing.diacritize), label="Diacritize")
        behaviour_card.body().addWidget(labelled_row("Diacritize dictation", self._diacritize))
        self._append_space = Switch(bool(self.context.settings.voice_typing.append_space), label="Space")
        behaviour_card.body().addWidget(
            labelled_row("Append a trailing space", self._append_space, hint="Handy when dictating sentences in a row.")
        )
        self._submit = ComboRow([("Do not press Enter", "none"), ("Press Enter", "enter"), ("Press Ctrl+Enter", "ctrl+enter")])
        self._submit.set_current(str(self.context.settings.voice_typing.submit_key or "none"))
        behaviour_card.body().addWidget(labelled_row("After inserting", self._submit))
        top.addWidget(behaviour_card, 1)
        self.add_layout(top)

        insert_card = Card()
        insert_card.body().addWidget(SectionHeader("Insertion", icon_name="edit"))
        self._method = ComboRow(INSERTION)
        self._method.set_current(str(self.context.settings.voice_typing.insert_method or "auto"))
        insert_card.body().addWidget(
            labelled_row(
                "Method",
                self._method,
                hint="Unicode keystrokes handle Persian correctly in most editors; the clipboard is a fallback.",
            )
        )
        self._clipboard = Switch(bool(self.context.settings.voice_typing.restore_clipboard), label="Restore clipboard")
        insert_card.body().addWidget(
            labelled_row(
                "Restore the clipboard afterwards",
                self._clipboard,
                hint="Tixi Voice only restores it when nothing else changed it in the meantime.",
            )
        )
        self._delay = SliderRow(
            float(self.context.settings.voice_typing.paste_delay_ms),
            minimum=20,
            maximum=400,
            step=10,
            suffix=" ms",
            formatter=lambda value: f"{int(value)}",
        )
        insert_card.body().addWidget(labelled_row("Paste delay", self._delay))
        self._blocked = TagInput(list(self.context.settings.voice_typing.blocked_apps or []), placeholder="app.exe")
        insert_card.body().addWidget(
            labelled_row(
                "Blocked applications",
                self._blocked,
                hint="Process names (for example keepass.exe) where Tixi Voice must never type.",
            )
        )
        self.add(insert_card)

        overlay_card = Card()
        overlay_card.body().addWidget(SectionHeader("On-screen indicator", icon_name="sparkles"))
        self._overlay = Switch(bool(self.context.settings.voice_typing.overlay_enabled), label="Overlay")
        overlay_card.body().addWidget(labelled_row("Show the floating overlay", self._overlay))
        self._overlay_position = ComboRow(
            [
                ("Bottom centre", "bottom_center"),
                ("Bottom right", "bottom_right"),
                ("Top centre", "top_center"),
                ("Top right", "top_right"),
                ("Near the cursor", "near_cursor"),
            ]
        )
        self._overlay_position.set_current(str(self.context.settings.voice_typing.overlay_position or "bottom_center"))
        overlay_card.body().addWidget(labelled_row("Position", self._overlay_position))
        self._sound = Switch(bool(self.context.settings.voice_typing.sound_feedback), label="Sound")
        overlay_card.body().addWidget(
            labelled_row("Sound feedback", self._sound, hint="A short tone when recording starts and stops.")
        )
        self.add(overlay_card)

        self._save = PrimaryButton("Save these settings", self, icon_name="check")
        self._save.clicked.connect(self._save_all)
        self.add(self._save)

        checks_card = Card()
        checks_card.body().addWidget(SectionHeader("Readiness checks", icon_name="check"))
        self._checks = QVBoxLayout()
        self._checks.setSpacing(4)
        checks_card.body().addLayout(self._checks)
        self.add(checks_card)

        dictation_card = Card()
        dictation_card.body().addWidget(
            SectionHeader("Recent dictations", "Everything below was inserted by the hotkey.", icon_name="clock")
        )
        self._recent = QListWidget()
        self._recent.setMinimumHeight(150)
        dictation_card.body().addWidget(self._recent)
        self._empty = muted("Nothing dictated yet.")
        dictation_card.body().addWidget(self._empty)
        copy = SecondaryButton("Copy selected text", dictation_card, icon_name="clipboard")
        copy.clicked.connect(self._copy_selected)
        dictation_card.body().addLayout(button_row(copy, stretch=False))
        self.add(dictation_card)

        test_card = Card()
        test_card.body().addWidget(
            SectionHeader(
                "Insertion test",
                "Type here, then dictate while this field has focus — the text is typed like a human would.",
                icon_name="edit",
            )
        )
        self._test_field = QPlainTextEdit()
        self._test_field.setPlaceholderText("Click here and dictate…")
        self._test_field.setMinimumHeight(90)
        test_card.body().addWidget(self._test_field)
        clear = FlatButton("Clear", test_card, icon_name="trash")
        clear.clicked.connect(self._test_field.clear)
        test_card.body().addLayout(button_row(clear, stretch=False))
        self.add(test_card)

        limits_card = Card()
        limits_card.body().addWidget(SectionHeader("Known limitations", icon_name="info"))
        for note in limitations():
            line = QLabel(f"• {note}")
            line.setObjectName("Caption")
            line.setWordWrap(True)
            limits_card.body().addWidget(line)
        limits_card.body().addWidget(
            muted("Verified in: " + ", ".join(supported_applications()[:10]) + " (see docs/MANUAL_TESTING.md).")
        )
        self.add(limits_card)

    # -- lifecycle ----------------------------------------------------------
    def refresh(self) -> None:
        self.ensure_built()
        self._refresh_checks()
        self._refresh_recent()

    def _dictation(self) -> Any:
        return getattr(self.context, "dictation", None)

    # -- actions ------------------------------------------------------------
    def _apply_shortcut(self) -> None:
        service = self._dictation()
        shortcut = self._shortcut.value()
        if service is None:
            self._shortcut_status.setText("Voice typing is unavailable on this system.")
            return
        ok = service.set_shortcut(shortcut, start=self._enabled.isChecked())
        self.context.settings_store.set("voice_typing.shortcut", shortcut)
        self.context.save_settings()
        if ok:
            self._shortcut_status.setText(f"{shortcut} is registered and active.")
            self.toast("Shortcut active", f"{shortcut} now starts dictation.", severity="success")
        else:
            detail = service.last_error() or "The hook could not be installed."
            self._shortcut_status.setText(detail)
            self.toast("Shortcut not active", detail, severity="warning")
        self._refresh_checks()

    def _test_conflict(self) -> None:
        service = self._dictation()
        shortcut = self._shortcut.value()
        if service is None:
            return
        report = service.hotkeys.test_conflict(shortcut)
        self._shortcut_status.setText(report.describe())
        self.toast("Shortcut check", report.describe(), severity="warning" if report.conflict else "success")

    def _apply_behaviour(self) -> None:
        store = self.context.settings_store
        store.update(
            {
                "voice_typing.enabled": self._enabled.isChecked(),
                "voice_typing.mode": str(self._mode.current_key() or "push_to_talk"),
                "voice_typing.language": str(self._language.current_key() or "fa"),
                "voice_typing.normalize_persian": self._post.isChecked(),
                "voice_typing.diacritize": self._diacritize.isChecked(),
                "voice_typing.append_space": self._append_space.isChecked(),
                "voice_typing.submit_key": str(self._submit.current_key() or "none"),
            }
        )
        service = self._dictation()
        if service is not None:
            service.apply_settings(self.context.settings)
        self.context.save_settings()

    def _save_all(self) -> None:
        store = self.context.settings_store
        store.update(
            {
                "voice_typing.enabled": self._enabled.isChecked(),
                "voice_typing.mode": str(self._mode.current_key() or "push_to_talk"),
                "voice_typing.language": str(self._language.current_key() or "fa"),
                "voice_typing.insert_method": str(self._method.current_key() or "auto"),
                "voice_typing.restore_clipboard": self._clipboard.isChecked(),
                "voice_typing.paste_delay_ms": int(self._delay.value()),
                "voice_typing.blocked_apps": self._blocked.values(),
                "voice_typing.overlay_enabled": self._overlay.isChecked(),
                "voice_typing.overlay_position": str(self._overlay_position.current_key() or "bottom_center"),
                "voice_typing.sound_feedback": self._sound.isChecked(),
                "voice_typing.diacritize": self._diacritize.isChecked(),
                "voice_typing.normalize_persian": self._post.isChecked(),
                "voice_typing.append_space": self._append_space.isChecked(),
                "voice_typing.submit_key": str(self._submit.current_key() or "none"),
            }
        )
        service = self._dictation()
        if service is not None:
            service.apply_settings(self.context.settings)
        self.context.save_settings()
        self.toast("Saved", "Voice typing settings were saved and applied.", severity="success")
        self._refresh_checks()

    # -- checks -------------------------------------------------------------
    def _refresh_checks(self) -> None:
        while self._checks.count():
            item = self._checks.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        service = self._dictation()
        if service is None:
            self._banner.set_message(
                "Voice typing is not available on this system. Global shortcuts and text insertion are "
                "Windows features; every other part of Tixi Voice works normally.",
                severity="warning",
            )
            self._banner.setVisible(True)
            return
        try:
            checks = service.health_check(self.context.settings)
        except Exception as exc:  # noqa: BLE001
            self._banner.set_message(f"The readiness check failed: {exc}", severity="error")
            self._banner.setVisible(True)
            return
        problems = [check for check in checks if not check["ok"]]
        if problems:
            self._banner.set_message(
                f"{len(problems)} item(s) need attention before dictation will work everywhere."
                if not any(check["ok"] for check in checks)
                else "Dictation works, but some configurations are not ideal — see the checks below.",
                severity="warning" if len(problems) < len(checks) else "error",
            )
            self._banner.setVisible(True)
        else:
            self._banner.set_message("Everything checks out — hold the shortcut and speak.", severity="success")
            self._banner.setVisible(True)
        for check in checks:
            self._checks.addWidget(_check_row(check))

    def _refresh_recent(self) -> None:
        self._recent.clear()
        service = self._dictation()
        results = list(service.results) if service is not None else []
        self._empty.setVisible(not results)
        for result in reversed(results[-40:]):
            header = "Inserted" if result.inserted else ("Copied" if result.text else "Failed")
            text = (result.text or result.raw_text or result.error or "").strip()
            item = QListWidgetItem(f"{header} · {human_time_ago(None) if False else ''}{text[:120]}")
            item.setData(Qt.ItemDataRole.UserRole, result)
            if result.warnings:
                item.setToolTip("\n".join(result.warnings))
            self._recent.addItem(item)
        counters = service.statistics() if service is not None else {}
        if counters:
            self._recent.setToolTip(
                f"{counters.get('sessions', 0)} sessions · {counters.get('inserted', 0)} inserted · "
                f"{counters.get('words', 0)} words"
            )

    def _copy_selected(self) -> None:
        item = self._recent.currentItem()
        if item is None:
            return
        result = item.data(Qt.ItemDataRole.UserRole)
        if result is None or not result.text:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(result.text)
        self.toast("Copied", "The dictation text is on the clipboard.", severity="success")


def _check_row(check: dict[str, Any]) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 2, 0, 2)
    layout.setSpacing(10)
    symbol = {"ok": "✔", "warning": "!", "error": "✕"}.get(check.get("status", "info"), "•")
    icon = QLabel(symbol)
    icon.setObjectName(
        {"ok": "SuccessText", "warning": "WarningText", "error": "DangerText"}.get(check.get("status", ""), "Caption")
    )
    layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(1)
    name = QLabel(str(check.get("label", "")))
    name.setObjectName("TitleLabel")
    column.addWidget(name)
    detail = QLabel(str(check.get("detail", "")))
    detail.setObjectName("Caption")
    detail.setWordWrap(True)
    column.addWidget(detail)
    layout.addLayout(column, 1)
    return row
