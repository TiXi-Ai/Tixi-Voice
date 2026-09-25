"""Text to speech: Persian-first synthesis with optional diacritization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...audio.playback import AudioPlayer
from ...utils.humanize import human_duration, human_size
from ..widgets.base import Badge, Card, InfoBanner, SectionHeader
from ..widgets.buttons import FlatButton, PrimaryButton, SecondaryButton
from ..widgets.inputs import ComboRow, SliderRow, SpinRow, Switch
from ..widgets.meters import WaveformView
from ..widgets.text_views import DiffView, PersianTextEdit, TextStatsBar
from .base import Page, button_row, labelled_row, muted

SAMPLE_TEXT = (
    "سلام! من تیکسی ویس هستم.\n"
    "این متن با موتور گفتار محلی خوانده می‌شود و هیچ داده‌ای به اینترنت فرستاده نمی‌شود.\n"
    "برای خواندن درست کسرهٔ اضافه، اِعراب‌گذاری هوشمند را روشن کن."
)


class TextToSpeechPage(Page):
    """Editor on the left, voice controls on the right."""

    title = "Text to Speech"
    subtitle = "Local neural voices — Persian first, with optional context-aware diacritization."
    icon_name = "speaker"

    def build(self) -> None:
        self.player = AudioPlayer()
        self.player.set_state_callback(self._on_playback_state)
        self._last_audio: dict[str, Any] = {}
        self._job_id = ""
        self._last_progress = 0.0

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_editor())
        splitter.addWidget(self._build_controls())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([720, 430])
        self.add(splitter)

        self._ticker = QTimer(self)
        self._ticker.setInterval(150)
        self._ticker.timeout.connect(self._tick)

    # -- editor -------------------------------------------------------------
    def _build_editor(self) -> QWidget:
        card = Card()
        header = SectionHeader("Text", "Type or paste Persian (or English) text.", icon_name="edit")
        self._char_badge = Badge("0 characters")
        header.add_trailing(self._char_badge)
        card.body().addWidget(header)

        tools = QWidget()
        tools_layout = QHBoxLayout(tools)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(6)
        for text, icon, callback in (
            ("Paste", "clipboard", self._paste),
            ("Load file", "folder", self._load_file),
            ("Sample", "sparkles", self._insert_sample),
            ("Clear", "trash", self._clear_text),
        ):
            button = FlatButton(text, tools, icon_name=icon)
            button.clicked.connect(callback)
            tools_layout.addWidget(button)
        tools_layout.addStretch(1)
        card.body().addWidget(tools)

        self.editor = PersianTextEdit(
            placeholder="متن فارسی را اینجا بنویسید…",
            rtl=True,
        )
        self.editor.setMinimumHeight(230)
        self.editor.textChanged.connect(self._on_text_changed)
        card.body().addWidget(self.editor)

        self.stats = TextStatsBar()
        card.body().addWidget(self.stats)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.diff = DiffView()
        self.tabs.addTab(self.diff, "Diacritization difference")
        self.preview = PersianTextEdit(read_only=True, highlight=False)
        self.tabs.addTab(self.preview, "Text sent to the voice")
        self.tabs.setVisible(False)
        card.body().addWidget(self.tabs)
        return card

    # -- controls -----------------------------------------------------------
    def _build_controls(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        voice_card = Card()
        voice_card.body().addWidget(SectionHeader("Voice", icon_name="waveform"))
        self._voice_combo = QComboBox()
        self._voice_combo.currentIndexChanged.connect(self._on_voice_changed)
        voice_card.body().addWidget(labelled_row("Voice model", self._voice_combo))
        self._engine_banner = InfoBanner("", severity="warning", action_text="Open AI Models")
        self._engine_banner.action_clicked.connect(lambda: self.goto("models"))
        voice_card.body().addWidget(self._engine_banner)
        self._voice_info = muted("")
        voice_card.body().addWidget(self._voice_info)
        layout.addWidget(voice_card)

        processing = Card()
        processing.body().addWidget(SectionHeader("Text processing", icon_name="wand"))
        self._normalize = Switch(True, label="Normalise")
        self._normalize.toggled.connect(self._on_options_changed)
        processing.body().addWidget(
            labelled_row(
                "Persian normalisation",
                self._normalize,
                hint="Arabic → Persian letters, ZWNJ repair, numbers and dates as words, spacing.",
            )
        )
        self._diacritize = Switch(False, label="Diacritize")
        self._diacritize.toggled.connect(self._on_options_changed)
        processing.body().addWidget(
            labelled_row(
                "Diacritization",
                self._diacritize,
                hint="Adds harakat from sentence context so the voice reads Ezafe correctly.",
            )
        )
        self._diac_mode = ComboRow(
            [("Smart — only confident marks", "smart"), ("Full — every predicted mark", "full")]
        )
        self._diac_mode.set_current("smart")
        self._diac_mode.changed.connect(lambda _key: self._on_options_changed())
        self._diac_row = labelled_row("Diacritization mode", self._diac_mode)
        processing.body().addWidget(self._diac_row)
        self._dictionary = Switch(True, label="Dictionary")
        self._dictionary.toggled.connect(self._on_options_changed)
        processing.body().addWidget(
            labelled_row(
                "Pronunciation dictionary",
                self._dictionary,
                hint="Apply your replacements and per-word pronunciation overrides.",
            )
        )
        self._diac_status = muted("")
        processing.body().addWidget(self._diac_status)
        layout.addWidget(processing)

        audio_card = Card()
        audio_card.body().addWidget(SectionHeader("Audio", icon_name="sliders"))
        self._speed = SliderRow(1.0, minimum=0.5, maximum=2.0, step=0.05, suffix="×")
        self._pitch = SliderRow(1.0, minimum=0.5, maximum=1.5, step=0.05, suffix="×")
        self._volume = SliderRow(1.0, minimum=0.0, maximum=2.0, step=0.05, suffix="×")
        for label, row in (("Speed", self._speed), ("Pitch", self._pitch), ("Volume", self._volume)):
            audio_card.body().addWidget(labelled_row(label, row))
        self._sentence_pause = SpinRow(180, minimum=0, maximum=2000, suffix=" ms")
        self._paragraph_pause = SpinRow(400, minimum=0, maximum=4000, suffix=" ms")
        self._chunk = SpinRow(900, minimum=200, maximum=2000, suffix=" chars")
        for label, row in (
            ("Sentence pause", self._sentence_pause),
            ("Paragraph pause", self._paragraph_pause),
            ("Chunk size", self._chunk),
        ):
            audio_card.body().addWidget(labelled_row(label, row))
        self._format = ComboRow([("WAV", "wav")])
        audio_card.body().addWidget(labelled_row("Output format", self._format))
        self._bit_depth = ComboRow([("16-bit", "16"), ("24-bit", "24"), ("32-bit float", "32")])
        self._bit_depth.set_current("16")
        audio_card.body().addWidget(labelled_row("Bit depth", self._bit_depth))
        self._auto_play = Switch(False, label="Auto play")
        audio_card.body().addWidget(
            labelled_row("Play after generating", self._auto_play, hint="Starts playback when rendering finishes.")
        )
        layout.addWidget(audio_card)

        render_card = Card()
        render_card.body().addWidget(SectionHeader("Render", icon_name="play"))
        self._generate = PrimaryButton("Generate speech", render_card, icon_name="speaker")
        self._generate.clicked.connect(self._generate_speech)
        self._cancel = SecondaryButton("Stop", render_card, icon_name="stop")
        self._cancel.clicked.connect(self._cancel_speech)
        self._cancel.setEnabled(False)
        render_card.body().addLayout(button_row(self._generate, self._cancel, stretch=False))

        self._play = SecondaryButton("Play", render_card, icon_name="play")
        self._play.clicked.connect(self._toggle_playback)
        self._play.setEnabled(False)
        self._save = SecondaryButton("Save as…", render_card, icon_name="download")
        self._save.clicked.connect(self._save_audio)
        self._save.setEnabled(False)
        self._open = SecondaryButton("Show file", render_card, icon_name="folder")
        self._open.clicked.connect(self._reveal_output)
        self._open.setEnabled(False)
        render_card.body().addLayout(button_row(self._play, self._save, self._open))

        self._waveform = WaveformView()
        self._waveform.setMinimumHeight(72)
        self._waveform.set_empty_text("Rendered audio appears here")
        render_card.body().addWidget(self._waveform)

        self._status = muted("Ready.")
        render_card.body().addWidget(self._status)
        self._result_info = QLabel("")
        self._result_info.setObjectName("Caption")
        self._result_info.setWordWrap(True)
        self._result_info.setVisible(False)
        render_card.body().addWidget(self._result_info)
        layout.addWidget(render_card)
        layout.addStretch(1)
        return wrapper

    # -- lifecycle ----------------------------------------------------------
    def refresh(self) -> None:
        self.ensure_built()
        self._reload_formats()
        self._reload_voices()
        self._on_text_changed()
        self._on_options_changed()

    def on_leave(self) -> None:
        try:
            self.player.stop()
        except Exception:  # noqa: BLE001
            pass

    def _reload_formats(self) -> None:
        options: list[tuple[str, str]] = []
        try:
            for name, note in self.context.exports.available_formats():
                label = name.upper()
                options.append((label, name))
        except Exception:  # noqa: BLE001
            options = []
        if not options:
            options = [("WAV", "wav")]
        current = self._format.current_key() or "wav"
        self._format.set_options(options, current=current if any(k == current for _l, k in options) else "wav")

    # -- voices -------------------------------------------------------------
    def _reload_voices(self) -> None:
        context = self.context
        rows = [row for row in context.models.rows() if row.category in ("tts_voice", "tts_multilingual")]
        installed = [row for row in rows if row.installed]
        pack = next((row for row in context.models.pack_rows() if row.id == "piper"), None)
        pack_ready = bool(pack and pack.installed and not pack.requires_restart)
        preferred = str(getattr(context.settings.tts, "model_id", "") or "")

        self._voice_combo.blockSignals(True)
        self._voice_combo.clear()
        for row in installed:
            label = f"{row.name} · {row.download_label}"
            if row.persian_capable:
                label += " · Persian"
            self._voice_combo.addItem(label, row.id)
        if not installed:
            self._voice_combo.addItem("No voice installed", "")
        index = self._voice_combo.findData(preferred)
        if index >= 0:
            self._voice_combo.setCurrentIndex(index)
        self._voice_combo.blockSignals(False)

        if not pack_ready:
            self._engine_banner.set_message(
                "The Piper engine pack is not installed yet. Install it from AI Models — it is a "
                "separate download so the application itself stays small."
            )
            self._engine_banner.setVisible(True)
            self._generate.disable_with_reason("Install the Piper engine pack first (AI Models).")
        elif not installed:
            self._engine_banner.set_message(
                "No voice model is installed. Persian voices are about 63 MB and install in seconds."
            )
            self._engine_banner.setVisible(True)
            self._generate.disable_with_reason("Install a voice model first (AI Models ▸ Text to Speech).")
        else:
            self._engine_banner.setVisible(False)
            self._generate.enable()
        self._on_voice_changed()

    def _current_voice_id(self) -> str:
        return str(self._voice_combo.currentData() or "")

    def _on_voice_changed(self) -> None:
        voice_id = self._current_voice_id()
        row = self.context.models.row(voice_id) if voice_id else None
        if row is None:
            self._voice_info.setText("No voice selected.")
            return
        spec = row.spec
        self._voice_info.setText(
            f"{row.persian_label} · {row.language_label} · {spec.hardware} · "
            f"licence: {spec.license or 'see the model card'}"
        )

    # -- options ------------------------------------------------------------
    def _on_text_changed(self) -> None:
        self.stats.update_stats(self.editor.statistics())
        length = len(self.editor.text())
        self._char_badge.setText(f"{length} characters")
        self._char_badge.set_severity("warning" if length > 20000 else "neutral")

    def _on_options_changed(self) -> None:
        try:
            self._diac_row.setEnabled(self._diacritize.isChecked())
        except Exception:  # noqa: BLE001
            pass
        if not self._diacritize.isChecked():
            self._diac_status.setText("")
            return
        try:
            description = self.context.diacritization.describe()
        except Exception:  # noqa: BLE001
            description = {}
        if description.get("active") == "canine-onnx":
            self._diac_status.setText(
                "Neural diacritizer active (CANINE, ONNX): marks are predicted from sentence context."
            )
        else:
            self._diac_status.setText(
                "No neural diacritizer installed — the built-in lexicon is used instead. It is a "
                "curated word list with morphology rules: approximate, and labelled as such."
            )

    # -- text helpers -------------------------------------------------------
    def _paste(self) -> None:
        from PySide6.QtWidgets import QApplication

        text = QApplication.clipboard().text()
        if not text:
            self.toast("Clipboard is empty", "There is nothing to paste.")
            return
        self.editor.set_text(self.editor.text() + ("\n" if self.editor.text() else "") + text, keep_undo=True)

    def _load_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Open a text file", str(Path.home()), "Text files (*.txt *.md *.srt *.vtt);;All files (*)"
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except OSError as exc:
            self.toast("Could not read the file", str(exc), severity="error")
            return
        self.editor.set_text(text)
        self.toast("Text loaded", f"{len(text)} characters from {Path(path).name}", severity="success")

    def _insert_sample(self) -> None:
        self.editor.set_text(SAMPLE_TEXT)

    def _clear_text(self) -> None:
        if self.editor.text().strip():
            self.editor.set_text("")

    # -- synthesis ----------------------------------------------------------
    def _generate_speech(self) -> None:
        context = self.context
        text = self.editor.text()
        if not text.strip():
            self.toast("Nothing to read", "Type or paste some text first.", severity="warning")
            return
        voice_id = self._current_voice_id()
        if not voice_id:
            self.toast("No voice installed", "Install a Persian voice from AI Models.", severity="warning")
            self.goto("models")
            return
        settings = context.settings
        self._generate.disable_with_reason("Rendering…")
        self._cancel.setEnabled(True)
        self._status.setText("Preparing…")
        self._last_progress = 0.0
        self._ticker.start()
        kwargs = dict(
            voice_id=voice_id,
            language=str(getattr(settings.tts, "language", "fa") or "fa"),
            speed=float(self._speed.value()),
            pitch=float(self._pitch.value()),
            volume=float(self._volume.value()),
            normalize=self._normalize.isChecked(),
            diacritize=self._diacritize.isChecked(),
            diacritization_mode=str(self._diac_mode.current_key() or "smart"),
            apply_dictionary=self._dictionary.isChecked(),
            sentence_pause_ms=int(self._sentence_pause.value()),
            paragraph_pause_ms=int(self._paragraph_pause.value()),
            chunk_chars=int(self._chunk.value()),
            output_format=str(self._format.current_key() or "wav"),
            bit_depth=int(self._bit_depth.current_key() or 16),
            sample_rate=int(getattr(settings.tts, "sample_rate", 22050) or 22050),
            diacritizer=context.diacritization,
        )
        self._job_id = context.jobs.submit(
            "Generating speech",
            self._synthesize_job,
            kwargs=kwargs,
            kind="tts",
            exclusive=True,
            on_progress=self._on_progress,
            on_finished=self._on_finished,
            on_failed=self._on_failed,
            on_cancelled=self._on_cancelled,
        )

    def _synthesize_job(self, *, progress: Any = None, cancel: Any = None, **kwargs: Any) -> Any:
        """Runs on a worker thread; the queue hop back is done by JobRunner."""
        return self.context.synthesis.synthesize(progress=progress, cancel=cancel, **kwargs)

    def _on_progress(self, payload: dict[str, Any]) -> None:
        self._last_progress = float(payload.get("progress", 0.0))
        detail = str(payload.get("detail") or "Working…")
        if detail:
            self._status.setText(detail)

    def _on_finished(self, payload: dict[str, Any]) -> None:
        self._ticker.stop()
        self._cancel.setEnabled(False)
        self._generate.enable()
        self._status.setText("Ready.")
        outcome = payload.get("result")
        if outcome is None:
            return
        for warning in outcome.warnings:
            if warning.startswith("ERROR:"):
                self.toast("Synthesis failed", warning.replace("ERROR:", "").strip(), severity="error")
                return
            self.toast("Note", warning, severity="warning")
        if outcome.result is None:
            self.toast("Synthesis failed", "The voice produced no audio.", severity="error")
            return
        samples = outcome.result.samples
        sample_rate = outcome.result.sample_rate
        self._last_audio = {
            "samples": samples,
            "sample_rate": sample_rate,
            "text": outcome.text_used,
            "voice_id": self._current_voice_id(),
            "output_path": outcome.output_path,
            "duration_s": outcome.duration_s,
        }
        for button in (self._play, self._save):
            button.setEnabled(True)
        self._open.setEnabled(outcome.output_path is not None)
        try:
            peaks = self.context.library.waveform_peaks(samples)
            self._waveform.set_peaks(peaks, max(0.001, outcome.duration_s))
        except Exception:  # noqa: BLE001
            pass
        self._result_info.setText(
            f"{human_duration(outcome.duration_s)} of audio · {outcome.chunks} chunk(s) · "
            f"rendered in {outcome.seconds_taken:.1f} s"
            + (f" · written to {outcome.output_path.name}" if outcome.output_path else "")
        )
        self._result_info.setVisible(True)
        self.diff.compare(self.editor.text(), outcome.text_used)
        self.preview.set_text(outcome.text_used)
        self.tabs.setVisible(True)

        entry_id = self.context.history.add(
            "tts",
            text=outcome.text_used,
            language=str(getattr(self.context.settings.tts, "language", "fa") or "fa"),
            model_id=self._current_voice_id(),
            audio_path=outcome.output_path,
            duration_ms=int(outcome.duration_s * 1000),
            meta={
                "diacritized": self._diacritize.isChecked(),
                "speed": float(self._speed.value()),
                "chunks": outcome.chunks,
            },
        )
        if outcome.output_path is not None:
            self.context.library.register_generated(
                outcome.output_path,
                history_id=entry_id,
                source_text=outcome.text_used,
                voice_id=self._current_voice_id(),
                language=str(getattr(self.context.settings.tts, "language", "fa") or "fa"),
                tags=["tts"],
            )
        self.toast("Speech ready", f"{human_duration(outcome.duration_s)} generated.", severity="success")
        if self._auto_play.isChecked():
            self._toggle_playback()

    def _on_failed(self, payload: dict[str, Any]) -> None:
        self._ticker.stop()
        self._cancel.setEnabled(False)
        self._generate.enable()
        self._status.setText("Failed.")
        self.toast("Synthesis failed", str(payload.get("error", "")), severity="error")

    def _on_cancelled(self, payload: dict[str, Any]) -> None:
        self._ticker.stop()
        self._cancel.setEnabled(False)
        self._generate.enable()
        self._status.setText("Cancelled — nothing was saved.")

    def _cancel_speech(self) -> None:
        if self._job_id:
            self.context.jobs.cancel(self._job_id)

    def _tick(self) -> None:
        fraction = max(0.0, min(1.0, float(self._last_progress)))
        filled = int(fraction * 10)
        self._status.setText(f"{'█' * filled}{'░' * (10 - filled)} {int(fraction * 100)}%")

    # -- playback / saving --------------------------------------------------
    def _toggle_playback(self) -> None:
        if self.player.is_playing():
            self.player.stop()
            return
        audio = self._last_audio
        if not audio:
            return
        self.player.load(audio["samples"], audio["sample_rate"])
        if not self.player.play(restart=True):
            self.toast("Playback unavailable", self.player.error() or "No output device.", severity="warning")

    def _on_playback_state(self, state: str) -> None:
        self._play.setText("Stop" if state == "playing" else "Play")

    def _save_audio(self) -> None:
        audio = self._last_audio
        if not audio:
            return
        fmt = str(self._format.current_key() or "wav")
        suggested = str(self.context.paths.exports / f"tixi-speech.{fmt}")
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save audio", suggested, f"{fmt.upper()} (*.{fmt});;All files (*)"
        )
        if not path:
            return
        target = self.context.synthesis.write_output(
            audio["samples"],
            audio["sample_rate"],
            Path(path),
            fmt=fmt,
            bit_depth=int(self._bit_depth.current_key() or 16),
        )
        self._last_audio["output_path"] = target
        self._open.setEnabled(True)
        self.context.library.register_generated(
            target, source_text=audio["text"], voice_id=audio["voice_id"], tags=["tts"]
        )
        self.toast("Saved", f"Written to {target.name} ({human_size(target.stat().st_size)})", severity="success")

    def _reveal_output(self) -> None:
        path = self._last_audio.get("output_path")
        if path is None:
            return
        from ...utils.win_integration import open_in_explorer

        open_in_explorer(Path(path))
