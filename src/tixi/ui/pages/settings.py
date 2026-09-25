"""Settings: every switch that changes behaviour, in one place."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...app.config import ACCENTS, THEME_MODES, Settings
from ...audio.devices import cached_devices, describe_device, invalidate_device_cache
from ...utils.humanize import human_size
from ..theme.icons import icon_pixmap
from ..widgets.base import Card, InfoBanner, SectionHeader
from ..widgets.buttons import DangerButton, FlatButton, PrimaryButton, SecondaryButton
from ..widgets.inputs import (
    ColourSwatch,
    ComboRow,
    NumberedListEditor,
    PathPicker,
    SettingRow,
    SliderRow,
    SpinRow,
    Switch,
    TagInput,
)
from ..widgets.toast import DetailDialog, confirm
from .base import Page, button_row, labelled_row, muted

ACCENT_LABELS = {
    "ocean_blue": "Ocean blue",
    "midnight_purple": "Midnight purple",
    "emerald_green": "Emerald green",
    "sunset_orange": "Sunset orange",
    "rose_pink": "Rose pink",
    "arctic_cyan": "Arctic cyan",
    "graphite": "Graphite",
    "custom": "Custom colour…",
}


class SettingsPage(Page):
    """Grouped settings with immediate apply and explicit save."""

    title = "Settings"
    subtitle = "Changes apply immediately and are stored locally in an SQLite database."
    icon_name = "settings"

    def build(self) -> None:
        self._loading = True
        self._banner = InfoBanner("", severity="info")
        self.add(self._banner)

        self._build_general()
        self._build_appearance()
        self._build_audio()
        self._build_processing()
        self._build_privacy()
        self._build_dictionary()
        self._build_maintenance()
        self._loading = False

        save = PrimaryButton("Save settings", self, icon_name="check")
        save.clicked.connect(self.save_now)
        export = SecondaryButton("Export settings…", self, icon_name="download")
        export.clicked.connect(self._export)
        load = SecondaryButton("Import settings…", self, icon_name="upload")
        load.clicked.connect(self._import)
        reset = DangerButton("Reset everything", self, icon_name="refresh")
        reset.clicked.connect(self._reset)
        self.add_layout(button_row(save, export, load, reset))

    # -- general ------------------------------------------------------------
    def _build_general(self) -> None:
        card = Card()
        card.body().addWidget(SectionHeader("General", icon_name="home"))
        self._ui_language = ComboRow([("English", "en"), ("فارسی", "fa")])
        self._ui_language.changed.connect(lambda key: self._set("general.ui_language", key))
        card.body().addWidget(labelled_row("Interface language", self._ui_language, hint="Menus and labels; model text is unaffected."))
        self._startup = ComboRow(
            [
                ("Home dashboard", "dashboard"),
                ("Text to Speech", "tts"),
                ("Speech to Text", "stt"),
                ("Voice Typing", "voice_typing"),
                ("Start in the tray", "tray"),
            ]
        )
        self._startup.changed.connect(lambda key: self._set("general.startup_behavior", key))
        card.body().addWidget(labelled_row("When Tixi Voice starts", self._startup))
        self._autostart = Switch(False, label="Autostart")
        self._autostart.toggled.connect(self._toggle_autostart)
        card.body().addWidget(
            labelled_row("Start with Windows", self._autostart, hint="Adds a shortcut to your user account only.")
        )
        self._tray_min = Switch(True, label="Minimise")
        self._tray_min.toggled.connect(lambda state: self._set("general.minimize_to_tray", state))
        card.body().addWidget(labelled_row("Minimise to the tray", self._tray_min))
        self._close_tray = Switch(True, label="Close")
        self._close_tray.toggled.connect(lambda state: self._set("general.close_to_tray", state))
        card.body().addWidget(
            labelled_row(
                "Closing the window keeps Tixi Voice running in the tray",
                self._close_tray,
                hint="Turn this off if you prefer the X button to quit completely.",
            )
        )
        self._confirm_exit = Switch(False, label="Confirm")
        self._confirm_exit.toggled.connect(lambda state: self._set("general.confirm_on_exit", state))
        card.body().addWidget(labelled_row("Ask before quitting", self._confirm_exit))
        self._restore_view = Switch(True, label="Restore")
        self._restore_view.toggled.connect(lambda state: self._set("general.restore_last_view", state))
        card.body().addWidget(labelled_row("Reopen the last page", self._restore_view))
        self._export_dir = PathPicker(mode="directory")
        self._export_dir.changed.connect(lambda value: self._set("general.export_dir", value))
        card.body().addWidget(labelled_row("Default export folder", self._export_dir, hint=str(Path.home())))
        self._model_dir = PathPicker(mode="directory")
        self._model_dir.changed.connect(self._set_model_dir)
        card.body().addWidget(
            labelled_row(
                "Model storage folder",
                self._model_dir,
                hint="Move this to another drive if you keep several Whisper models.",
            )
        )
        self.add(card)

    # -- appearance ---------------------------------------------------------
    def _build_appearance(self) -> None:
        card = Card()
        card.body().addWidget(SectionHeader("Appearance", icon_name="palette"))
        self._theme = ComboRow([(mode.title() if mode != "system" else "Follow Windows", mode) for mode in THEME_MODES])
        self._theme.changed.connect(self._apply_theme)
        card.body().addWidget(labelled_row("Theme", self._theme))
        self._accent = ComboRow([(ACCENT_LABELS.get(name, name.title()), name) for name in ACCENTS] + [("Custom colour…", "custom")])
        self._accent.changed.connect(self._apply_accent)
        card.body().addWidget(labelled_row("Accent colour", self._accent))
        self._custom_accent = ColourSwatch("#0A84FF")
        self._custom_accent.colour_changed.connect(self._apply_custom_accent)
        card.body().addWidget(labelled_row("Custom accent", self._custom_accent, hint="Used when the accent above is set to Custom."))
        self._radius = SliderRow(14, minimum=0, maximum=28, step=1, suffix=" px", formatter=lambda value: str(int(value)))
        self._radius.value_changed.connect(
            lambda value: self._theme_manager(lambda manager: manager.set_radius(int(value)))
        )
        card.body().addWidget(labelled_row("Corner radius", self._radius))
        self._density = ComboRow([("Comfortable", "comfortable"), ("Compact", "compact"), ("Spacious", "spacious")])
        self._density.changed.connect(lambda key: self._theme_manager(lambda manager: manager.set_density(key)))
        card.body().addWidget(labelled_row("Density", self._density))
        self._scale = SliderRow(1.0, minimum=0.85, maximum=1.3, step=0.05, suffix="×")
        self._scale.value_changed.connect(
            lambda value: self._theme_manager(lambda manager: manager.set_scale(float(value)))
        )
        card.body().addWidget(labelled_row("Interface scale", self._scale))
        self._animations = Switch(True, label="Animations")
        self._animations.toggled.connect(
            lambda state: self._theme_manager(lambda manager: manager.set_animations(bool(state)))
        )
        card.body().addWidget(
            labelled_row("Animations", self._animations, hint="Turn off for a snappier, lighter interface.")
        )
        self._transparency = Switch(False, label="Reduce transparency")
        self._transparency.toggled.connect(
            lambda state: self._theme_manager(lambda manager: manager.set_reduce_transparency(bool(state)))
        )
        card.body().addWidget(
            labelled_row(
                "Reduce transparency",
                self._transparency,
                hint="Disables the acrylic/blur effects — recommended for remote desktop sessions.",
            )
        )
        self.add(card)

    # -- audio --------------------------------------------------------------
    def _build_audio(self) -> None:
        card = Card()
        card.body().addWidget(SectionHeader("Audio devices", icon_name="mic"))
        self._input = ComboRow([])
        self._input.changed.connect(lambda key: self._set("audio.input_device", key))
        card.body().addWidget(labelled_row("Microphone", self._input))
        self._output = ComboRow([])
        self._output.changed.connect(lambda key: self._set("audio.output_device", key))
        card.body().addWidget(labelled_row("Output device", self._output))
        refresh = FlatButton("Re-scan devices", card, icon_name="refresh")
        refresh.clicked.connect(self._reload_devices)
        card.body().addLayout(button_row(refresh, stretch=False))
        self._sample_rate = ComboRow([("44.1 kHz", "44100"), ("48 kHz", "48000")])
        self._sample_rate.changed.connect(lambda key: self._set("audio.input_sample_rate", int(key)))
        card.body().addWidget(labelled_row("Recording sample rate", self._sample_rate))
        self._gain = SliderRow(1.0, minimum=0.2, maximum=3.0, step=0.05, suffix="×")
        self._gain.value_changed.connect(lambda value: self._set("audio.mic_gain", round(float(value), 2)))
        card.body().addWidget(labelled_row("Microphone gain", self._gain))
        self._noise = ComboRow(
            [("Off", "off"), ("Noise gate", "gate"), ("Spectral reduction", "spectral")]
        )
        self._noise.changed.connect(lambda key: self._set("audio.noise_reduction", key))
        card.body().addWidget(
            labelled_row(
                "Noise reduction",
                self._noise,
                hint="Applied while recording; spectral reduction is slower but stronger.",
            )
        )
        self._gate = SliderRow(-45.0, minimum=-70.0, maximum=-20.0, step=1.0, suffix=" dB")
        self._gate.value_changed.connect(lambda value: self._set("audio.noise_gate_db", float(value)))
        card.body().addWidget(labelled_row("Gate threshold", self._gate))
        self._record_format = ComboRow([("WAV (lossless)", "wav"), ("FLAC (lossless, smaller)", "flac")])
        self._record_format.changed.connect(lambda key: self._set("audio.record_format", key))
        card.body().addWidget(labelled_row("Recording format", self._record_format))
        self.add(card)

    # -- speech processing --------------------------------------------------
    def _build_processing(self) -> None:
        card = Card()
        card.body().addWidget(SectionHeader("Speech processing", icon_name="sliders"))
        self._tts_speed = SliderRow(1.0, minimum=0.5, maximum=2.0, step=0.05, suffix="×")
        self._tts_speed.value_changed.connect(lambda value: self._set("tts.speed", round(float(value), 2)))
        card.body().addWidget(labelled_row("Default speaking speed", self._tts_speed))
        self._tts_pause = SpinRow(180, minimum=0, maximum=2000, suffix=" ms")
        self._tts_pause.value_changed.connect(lambda value: self._set("tts.sentence_pause_ms", int(value)))
        card.body().addWidget(labelled_row("Sentence pause", self._tts_pause))
        self._tts_diac = Switch(False, label="Diacritize")
        self._tts_diac.toggled.connect(lambda state: self._set("tts.diacritize", bool(state)))
        card.body().addWidget(
            labelled_row("Diacritize before speaking", self._tts_diac, hint="Improves Ezafe pronunciation.")
        )
        self._stt_model = ComboRow([("Automatic", "auto")])
        self._stt_model.changed.connect(lambda key: self._set("stt.model_id", key))
        card.body().addWidget(labelled_row("Default Whisper model", self._stt_model))
        self._stt_language = ComboRow([("Automatic detection", "auto"), ("Persian", "fa"), ("English", "en")])
        self._stt_language.changed.connect(lambda key: self._set("stt.language", key))
        card.body().addWidget(labelled_row("Default language", self._stt_language))
        self._stt_beam = SpinRow(5, minimum=1, maximum=10)
        self._stt_beam.value_changed.connect(lambda value: self._set("stt.beam_size", int(value)))
        card.body().addWidget(labelled_row("Beam size", self._stt_beam, hint="Higher is slower but more accurate."))
        self._stt_vad = Switch(True, label="VAD")
        self._stt_vad.toggled.connect(lambda state: self._set("stt.vad_filter", bool(state)))
        card.body().addWidget(labelled_row("Voice activity detection", self._stt_vad))
        self._compute = ComboRow(
            [("Automatic", "auto"), ("int8 (fastest, CPU)", "int8"), ("float16 (GPU)", "float16"), ("float32", "float32")]
        )
        self._compute.changed.connect(lambda key: self._set("stt.compute_type", key))
        card.body().addWidget(labelled_row("Compute precision", self._compute))
        self._unload = SpinRow(300, minimum=0, maximum=3600, suffix=" s")
        self._unload.value_changed.connect(lambda value: self._set("ai.unload_after_idle_s", int(value)))
        card.body().addWidget(
            labelled_row(
                "Free memory after",
                self._unload,
                hint="Unloads idle models after this many seconds (0 = keep them loaded).",
            )
        )
        self.add(card)

    # -- privacy ------------------------------------------------------------
    def _build_privacy(self) -> None:
        card = Card()
        card.body().addWidget(SectionHeader("Privacy", icon_name="lock"))
        self._offline = Switch(False, label="Offline mode")
        self._offline.toggled.connect(self._toggle_offline)
        card.body().addWidget(
            labelled_row(
                "Offline Mode",
                self._offline,
                hint="Blocks every network call: model downloads, engine packs and update checks.",
            )
        )
        self._history_enabled = Switch(True, label="History")
        self._history_enabled.toggled.connect(lambda state: self._set("privacy.history_enabled", bool(state)))
        card.body().addWidget(labelled_row("Keep a history of what I create", self._history_enabled))
        self._retention = SpinRow(0, minimum=0, maximum=3650, suffix=" days")
        self._retention.value_changed.connect(lambda value: self._set("privacy.retention_days", int(value)))
        card.body().addWidget(
            labelled_row("Delete history older than", self._retention, hint="0 keeps everything forever.")
        )
        self._audio_retention = SpinRow(0, minimum=0, maximum=3650, suffix=" days")
        self._audio_retention.value_changed.connect(lambda value: self._set("privacy.auto_delete_audio_days", int(value)))
        card.body().addWidget(labelled_row("Delete generated audio after", self._audio_retention))
        self._log_content = Switch(False, label="Log content")
        self._log_content.toggled.connect(lambda state: self._set("privacy.log_content", bool(state)))
        card.body().addWidget(
            labelled_row(
                "Include recognised text in the log",
                self._log_content,
                hint="Off by default: logs contain timings and errors only.",
            )
        )
        self._anonymise = Switch(True, label="Anonymise")
        self._anonymise.toggled.connect(lambda state: self._set("privacy.anonymise_paths_in_logs", bool(state)))
        card.body().addWidget(labelled_row("Anonymise file paths in logs", self._anonymise))
        self._telemetry = muted("Telemetry is not implemented and is never sent.")
        card.body().addWidget(self._telemetry)
        self.add(card)

    # -- dictionary ---------------------------------------------------------
    def _build_dictionary(self) -> None:
        card = Card()
        card.body().addWidget(
            SectionHeader(
                "Pronunciation dictionary",
                "Case-sensitive replacements and per-word overrides applied before synthesis and dictation.",
                icon_name="book",
            )
        )
        self._dictionary = NumberedListEditor(labels=("Word", "Replacement"))
        card.body().addWidget(self._dictionary)
        save = SecondaryButton("Save dictionary", card, icon_name="check")
        save.clicked.connect(self._save_dictionary)
        self._dictionary_info = muted("")
        card.body().addWidget(self._dictionary_info)
        card.body().addLayout(button_row(save, stretch=False))
        self.add(card)

    # -- maintenance --------------------------------------------------------
    def _build_maintenance(self) -> None:
        card = Card()
        card.body().addWidget(SectionHeader("Maintenance", icon_name="settings"))
        self._db_info = muted("")
        card.body().addWidget(self._db_info)
        backup = SecondaryButton("Back up the database", card, icon_name="download")
        backup.clicked.connect(self._backup)
        vacuum = SecondaryButton("Compact the database", card, icon_name="refresh")
        vacuum.clicked.connect(self._vacuum)
        logs = FlatButton("Open the log folder", card, icon_name="folder")
        logs.clicked.connect(self._open_logs)
        card.body().addLayout(button_row(backup, vacuum, logs))
        self.add(card)

    # -- lifecycle ----------------------------------------------------------
    def refresh(self) -> None:
        self.ensure_built()
        self._loading = True
        settings = self.context.settings
        general, appearance, audio = settings.general, settings.appearance, settings.audio
        self._ui_language.set_current(str(general.ui_language), emit=False)
        self._startup.set_current(str(general.startup_behavior), emit=False)
        self._tray_min.setChecked(bool(general.minimize_to_tray))
        self._close_tray.setChecked(bool(general.close_to_tray))
        self._confirm_exit.setChecked(bool(general.confirm_on_exit))
        self._restore_view.setChecked(bool(general.restore_last_view))
        self._export_dir.set_path(str(general.export_dir))
        self._model_dir.set_path(str(general.model_dir))
        self._autostart.setChecked(self._startup_enabled())
        self._theme.set_current(str(appearance.theme_mode), emit=False)
        self._accent.set_current(str(appearance.accent), emit=False)
        custom = str(getattr(appearance, "custom_accent", "#0A84FF") or "#0A84FF")
        self._custom_accent.set_colour(custom, emit=False) if custom.startswith("#") and len(custom) == 7 else None
        self._radius.set_value(float(appearance.corner_radius))
        self._density.set_current(str(appearance.density), emit=False)
        self._scale.set_value(float(appearance.ui_scale))
        self._animations.setChecked(bool(appearance.animations))
        self._transparency.setChecked(bool(appearance.reduce_transparency))
        self._reload_devices()
        self._sample_rate.set_current(str(audio.input_sample_rate), emit=False)
        self._gain.set_value(float(audio.mic_gain))
        self._noise.set_current(str(audio.noise_reduction), emit=False)
        self._gate.set_value(float(audio.noise_gate_db))
        self._record_format.set_current(str(audio.record_format), emit=False)
        tts, stt, ai = settings.tts, settings.stt, settings.ai
        self._tts_speed.set_value(float(tts.speed))
        self._tts_pause.set_value(int(tts.sentence_pause_ms))
        self._tts_diac.setChecked(bool(tts.diacritize))
        self._stt_language.set_current(str(stt.language), emit=False)
        self._stt_beam.set_value(int(stt.beam_size))
        self._stt_vad.setChecked(bool(stt.vad_filter))
        self._compute.set_current(str(stt.compute_type), emit=False)
        self._unload.set_value(int(ai.unload_after_idle_s))
        self._reload_stt_models()
        privacy = settings.privacy
        self._offline.setChecked(bool(privacy.offline_mode))
        self._history_enabled.setChecked(bool(privacy.history_enabled))
        self._retention.set_value(int(privacy.retention_days))
        self._audio_retention.set_value(int(privacy.auto_delete_audio_days))
        self._log_content.setChecked(bool(privacy.log_content))
        self._anonymise.setChecked(bool(privacy.anonymise_paths_in_logs))
        self._reload_dictionary()
        stats = self.context.database.stats()
        self._db_info.setText(f"{stats.get('path', '')} · {human_size(int(stats.get('size_bytes', 0) or 0))}")
        self._loading = False

    # -- helpers ------------------------------------------------------------
    def _set(self, dotted: str, value: Any, *, save: bool = True) -> None:
        if self._loading:
            return
        self.context.settings_store.set(dotted, value)
        if save:
            self.context.save_settings()
        self._react(dotted)

    def _react(self, dotted: str) -> None:
        """Apply the change to the live services."""
        settings = self.context.settings
        if dotted.startswith("voice_typing") and self.context.dictation is not None:
            self.context.dictation.apply_settings(settings)
        elif dotted.startswith("general.model_dir"):
            self.context.model_registry.set_model_dir(Path(settings.general.model_dir) if settings.general.model_dir else None)
        elif dotted.startswith("privacy"):
            if self.context.updates is not None:
                self.context.updates.offline = bool(settings.privacy.offline_mode)
            if self.context.models is not None:
                self.context.models.allow_network = bool(settings.privacy.allow_network) and not settings.privacy.offline_mode

    def _theme_manager(self, action: Callable[[Any], None]) -> None:
        if self._loading:
            return
        manager = getattr(self.window(), "theme_manager", None)
        if manager is None:
            return
        action(manager)
        self.context.save_settings()

    def _apply_theme(self, key: str) -> None:
        self._theme_manager(lambda manager: manager.set_mode(key))

    def _apply_accent(self, key: str) -> None:
        self._theme_manager(lambda manager: manager.set_accent(key, custom=self._custom_accent.colour()))
        self._set("appearance.accent", key)

    def _apply_custom_accent(self, colour: str) -> None:
        self._custom_accent.set_colour(colour, emit=False)
        self._theme_manager(lambda manager: manager.set_custom_accent(colour))
        self._set("appearance.custom_accent", colour)

    def _set_model_dir(self, value: str) -> None:
        self._set("general.model_dir", value)

    def _toggle_autostart(self, enabled: bool) -> None:
        if self._loading:
            return
        from ...utils.win_integration import set_startup_enabled

        ok, message = set_startup_enabled(bool(enabled))
        self._set("general.start_with_windows", bool(enabled) and ok)
        if not ok:
            self._autostart.setChecked(False)
            self.toast("Could not change autostart", message, severity="warning")
        else:
            self.toast("Autostart updated", message or ("enabled" if enabled else "disabled"), severity="success")

    def _startup_enabled(self) -> bool:
        from ...utils.win_integration import is_startup_enabled

        try:
            return bool(is_startup_enabled())
        except Exception:  # noqa: BLE001
            return False

    def _toggle_offline(self, enabled: bool) -> None:
        if self._loading:
            return
        self._set("privacy.offline_mode", bool(enabled))
        self._set("privacy.allow_network", not bool(enabled))
        self.toast(
            "Offline Mode " + ("enabled" if enabled else "disabled"),
            "No network calls will be made." if enabled else "Model downloads and update checks are possible again.",
            severity="info",
        )

    def _reload_devices(self) -> None:
        invalidate_device_cache()
        devices = cached_devices(refresh=True)
        inputs = [("System default", "")] + [
            (f"{device.label}", str(device.index)) for device in devices.inputs
        ]
        outputs = [("System default", "")] + [
            (f"{device.label}", str(device.index)) for device in devices.outputs
        ]
        current_in = self._input.current_key()
        current_out = self._output.current_key()
        self._input.set_options(inputs, current=current_in or str(self.context.settings.audio.input_device or ""))
        self._output.set_options(outputs, current=current_out or str(self.context.settings.audio.output_device or ""))
        if devices.error:
            self._banner.set_message(devices.error, severity="warning")
            self._banner.setVisible(True)

    def _reload_stt_models(self) -> None:
        rows = [row for row in self.context.models.rows(category="stt") if row.installed]
        options = [("Automatic", "auto")] + [(row.name, row.id) for row in rows]
        self._stt_model.set_options(options, current=str(self.context.settings.stt.model_id or "auto"))

    def _reload_dictionary(self) -> None:
        entries = self.context.dictionary.list("fa", enabled_only=False)
        self._dictionary.set_values([[row.get("word", ""), row.get("replacement", "")] for row in entries])
        self._dictionary_info.setText(
            f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'} stored in the database."
        )

    def _save_dictionary(self) -> None:
        existing = {row.get("word", ""): row for row in self.context.dictionary.list("fa", enabled_only=False)}
        keep: set[str] = set()
        added = 0
        for pair in self._dictionary.values():
            if len(pair) < 2 or not pair[0].strip():
                continue
            word, replacement = pair[0].strip(), pair[1].strip()
            keep.add(word)
            if not replacement:
                continue
            current = existing.get(word)
            if current is None or current.get("replacement") != replacement:
                self.context.dictionary.add(word, replacement, language="fa")
                added += 1
        for word, row in existing.items():
            if word not in keep and row.get("id"):
                self.context.dictionary.delete(int(row["id"]))
        self.context.refresh_pipeline()
        self._reload_dictionary()
        self.toast("Dictionary saved", f"{added} change(s) applied to the text pipeline.", severity="success")

    def save_now(self) -> None:
        self.context.save_settings()
        self.toast("Saved", "Settings were written to the database and the JSON mirror.", severity="success")

    def _export(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export settings", str(self.context.paths.exports / "tixi-settings.json"), "JSON (*.json)"
        )
        if not path:
            return
        target = self.context.settings_repository.export_to(Path(path))
        self.toast("Exported", f"Settings written to {target}", severity="success")

    def _import(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Import settings", str(self.context.paths.exports), "JSON (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            store = self.context.settings_repository.import_from(Path(path))
        except Exception as exc:  # noqa: BLE001
            self.toast("Import failed", str(exc), severity="error")
            return
        self.context.settings_store.replace(store.settings)
        self.refresh()
        window = self.window()
        hook = getattr(window, "apply_settings_everywhere", None)
        if callable(hook):
            hook()
        self.toast("Imported", "Settings were replaced with the imported file.", severity="success")

    def _reset(self) -> None:
        accepted, _checked = confirm(
            self,
            "Reset every setting?",
            "All settings return to their defaults. History, the audio library and installed models "
            "are not touched.",
            confirm_text="Reset settings",
            destructive=True,
        )
        if not accepted:
            return
        self.context.settings_store.reset_all()
        self.context.save_settings()
        self.refresh()
        window = self.window()
        hook = getattr(window, "apply_settings_everywhere", None)
        if callable(hook):
            hook()
        self.toast("Reset", "All settings are back to their defaults.", severity="success")

    def _backup(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Back up the database", str(self.context.paths.exports / "tixi-backup.db"), "SQLite (*.db)"
        )
        if not path:
            return
        try:
            target = self.context.database.backup_to(Path(path))
        except Exception as exc:  # noqa: BLE001
            self.toast("Backup failed", str(exc), severity="error")
            return
        self.toast("Backed up", f"{target.name} ({human_size(target.stat().st_size)})", severity="success")

    def _vacuum(self) -> None:
        try:
            self.context.database.vacuum()
        except Exception as exc:  # noqa: BLE001
            self.toast("Compaction failed", str(exc), severity="error")
            return
        self.refresh()
        self.toast("Compacted", "The database was vacuumed.", severity="success")

    def _open_logs(self) -> None:
        from ...utils.win_integration import open_in_explorer

        open_in_explorer(self.context.paths.logs)
