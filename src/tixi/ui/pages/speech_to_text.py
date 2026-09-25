"""Speech to text: file import, microphone capture and transcript export."""

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

from ...app.config import STT_LANGUAGES
from ...audio.devices import describe_device, resolve_input_device
from ...audio.recorder import AudioRecorder, RecorderOptions
from ...utils.humanize import human_duration, human_size
from ..widgets.base import Badge, Card, InfoBanner, SectionHeader
from ..widgets.buttons import FlatButton, PrimaryButton, SecondaryButton
from ..widgets.inputs import ComboRow, DropZone, SliderRow, SpinRow, Switch
from ..widgets.meters import LevelMeter, WaveformView
from ..widgets.text_views import TranscriptView
from .base import Page, button_row, labelled_row, muted

LANGUAGES = [("Automatic detection", "auto")] + list(STT_LANGUAGES)


class SpeechToTextPage(Page):
    """Transcribe a file or the microphone, then export the transcript."""

    title = "Speech to Text"
    subtitle = "Whisper runs locally on the CPU or GPU — audio never leaves this computer."
    icon_name = "waveform"

    def build(self) -> None:
        self.context_any = self.context
        self._recorder: AudioRecorder | None = None
        self._job_id = ""
        self._outcome: Any = None
        self._source_path: Path | None = None

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_transcript())
        splitter.addWidget(self._build_controls())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([720, 430])
        self.add(splitter)

        self._level_timer = QTimer(self)
        self._level_timer.setInterval(60)
        self._level_timer.timeout.connect(self._poll_level)

    # -- transcript ---------------------------------------------------------
    def _build_transcript(self) -> QWidget:
        card = Card()
        header = SectionHeader("Transcript", "Editable — fix a word before you export it.", icon_name="edit")
        self._status_badge = Badge("Idle", "neutral")
        header.add_trailing(self._status_badge)
        card.body().addWidget(header)

        self.view = TranscriptView()
        self.view.setMinimumHeight(320)
        self.view.text_edited.connect(self._on_text_edited)
        card.body().addWidget(self.view)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self._raw = QWidget()
        self._raw_layout = QVBoxLayout(self._raw)
        self._raw_layout.setContentsMargins(0, 8, 0, 0)
        self.tabs.addTab(self._raw, "Model output (raw)")
        self.tabs.setVisible(False)
        card.body().addWidget(self.tabs)

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(6)
        self._copy = FlatButton("Copy", actions, icon_name="clipboard")
        self._copy.clicked.connect(self._copy_transcript)
        self._export_dir = FlatButton("Export…", actions, icon_name="download")
        self._export_dir.clicked.connect(self._export_transcript)
        self._save_history = FlatButton("Save to history", actions, icon_name="clock")
        self._save_history.clicked.connect(self._save_to_history)
        for button in (self._copy, self._export_dir, self._save_history):
            button.setEnabled(False)
            actions_layout.addWidget(button)
        actions_layout.addStretch(1)
        card.body().addWidget(actions)
        return card

    # -- controls -----------------------------------------------------------
    def _build_controls(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        source_card = Card()
        source_card.body().addWidget(SectionHeader("Source", icon_name="folder"))
        self.drop = DropZone(
            title="Drop an audio or video file here",
            hint="WAV, MP3, M4A, FLAC, OGG, OPUS — or click to browse",
        )
        self.drop.files_dropped.connect(self._on_files_dropped)
        self.drop.clicked.connect(self._browse_file)
        source_card.body().addWidget(self.drop)

        browse = SecondaryButton("Choose a file…", source_card, icon_name="folder")
        browse.clicked.connect(self._browse_file)
        record = PrimaryButton("Record from microphone", source_card, icon_name="mic")
        record.clicked.connect(self._toggle_recording)
        self._record_button = record
        source_card.body().addLayout(button_row(browse, record, stretch=False))

        self._source_label = muted("No file selected.")
        source_card.body().addWidget(self._source_label)
        self._level = LevelMeter()
        self._level.setVisible(False)
        source_card.body().addWidget(self._level)
        self._record_time = muted("")
        source_card.body().addWidget(self._record_time)
        layout.addWidget(source_card)

        model_card = Card()
        model_card.body().addWidget(SectionHeader("Model & language", icon_name="box"))
        self._model_combo = QComboBox()
        model_card.body().addWidget(labelled_row("Whisper model", self._model_combo))
        self._language = ComboRow(LANGUAGES)
        self._language.set_current("fa")
        model_card.body().addWidget(labelled_row("Language", self._language))
        self._task = ComboRow([("Transcribe (keep the language)", "transcribe"), ("Translate to English", "translate")])
        model_card.body().addWidget(labelled_row("Task", self._task))
        self._engine_banner = InfoBanner("", severity="warning", action_text="Open AI Models")
        self._engine_banner.action_clicked.connect(lambda: self.goto("models"))
        model_card.body().addWidget(self._engine_banner)
        layout.addWidget(model_card)

        quality_card = Card()
        quality_card.body().addWidget(SectionHeader("Recognition quality", icon_name="sliders"))
        self._vad = Switch(True, label="VAD")
        self._vad.toggled.connect(lambda _state: None)
        quality_card.body().addWidget(
            labelled_row(
                "Voice activity filter",
                self._vad,
                hint="Skips silence; improves speed and reduces hallucinated text.",
            )
        )
        self._word_times = Switch(True, label="Word timestamps")
        quality_card.body().addWidget(
            labelled_row("Word timestamps", self._word_times, hint="Needed for precise subtitle timing.")
        )
        self._post_process = Switch(True, label="Post-process")
        quality_card.body().addWidget(
            labelled_row(
                "Persian post-processing",
                self._post_process,
                hint="Arabic → Persian letters, spacing, optional diacritization.",
            )
        )
        self._diacritize = Switch(False, label="Diacritize")
        quality_card.body().addWidget(
            labelled_row("Diacritize the transcript", self._diacritize)
        )
        self._beam = SpinRow(5, minimum=1, maximum=10)
        quality_card.body().addWidget(labelled_row("Beam size", self._beam))
        self._temperature = SliderRow(0.0, minimum=0.0, maximum=1.0, step=0.1, suffix="", formatter=lambda v: f"{v:.1f}")
        quality_card.body().addWidget(labelled_row("Temperature", self._temperature))
        layout.addWidget(quality_card)

        run_card = Card()
        run_card.body().addWidget(SectionHeader("Run", icon_name="play"))
        self._start = PrimaryButton("Transcribe", run_card, icon_name="waveform")
        self._start.clicked.connect(self._start_transcription)
        self._cancel = SecondaryButton("Stop", run_card, icon_name="stop")
        self._cancel.clicked.connect(self._cancel_transcription)
        self._cancel.setEnabled(False)
        run_card.body().addLayout(button_row(self._start, self._cancel, stretch=False))
        self._progress = WaveformView()
        self._progress.setMinimumHeight(64)
        self._progress.set_empty_text("No audio loaded")
        run_card.body().addWidget(self._progress)
        self._status = muted("Ready.")
        run_card.body().addWidget(self._status)
        layout.addWidget(run_card)
        layout.addStretch(1)
        return wrapper

    # -- lifecycle ----------------------------------------------------------
    def refresh(self) -> None:
        self.ensure_built()
        self._reload_models()

    def on_leave(self) -> None:
        if self._recorder is not None and self._recorder.is_recording:
            self._stop_recording()

    # -- models -------------------------------------------------------------
    def _reload_models(self) -> None:
        context = self.context
        models = [row for row in context.models.rows(category="stt") if row.installed]
        pack = next((row for row in context.models.pack_rows() if row.id == "faster-whisper"), None)
        pack_ready = bool(pack and pack.installed and not pack.requires_restart)
        preferred = str(getattr(context.settings.stt, "model_id", "") or "")
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for row in models:
            self._model_combo.addItem(f"{row.name} · {row.download_label}", row.id)
        if not models:
            self._model_combo.addItem("No model installed", "")
        index = self._model_combo.findData(preferred)
        if index >= 0:
            self._model_combo.setCurrentIndex(index)
        self._model_combo.blockSignals(False)

        if not pack_ready:
            self._engine_banner.set_message(
                "The faster-whisper engine pack is not installed. Whisper needs a one-time runtime "
                "download that is kept out of the installer."
            )
            self._engine_banner.setVisible(True)
            self._start.disable_with_reason("Install the faster-whisper engine pack (AI Models).")
        elif not models:
            self._engine_banner.set_message(
                "No Whisper model is installed yet. 'small' or above is recommended for Persian."
            )
            self._engine_banner.setVisible(True)
            self._start.disable_with_reason("Install a Whisper model (AI Models ▸ Speech Recognition).")
        else:
            self._engine_banner.setVisible(False)
            self._start.enable()

    # -- input --------------------------------------------------------------
    def _browse_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose audio",
            str(self.context.paths.exports),
            "Audio and video (*.wav *.mp3 *.m4a *.flac *.ogg *.opus *.aac *.wma *.mp4 *.mkv);;All files (*)",
        )
        if path:
            self._load_source(Path(path))

    def _on_files_dropped(self, paths: Any) -> None:
        items = [Path(item) for item in (paths if isinstance(paths, (list, tuple)) else [paths])]
        if not items:
            return
        self._load_source(items[0])
        if len(items) > 1:
            self.toast(
                "Several files dropped",
                f"{len(items)} files were dropped; {items[0].name} was loaded. Use the Audio Library "
                "to import them all at once.",
                severity="info",
            )

    def _load_source(self, path: Path) -> None:
        if not path.exists():
            self.toast("File not found", str(path), severity="error")
            return
        self._source_path = path
        try:
            description = self.context.exports.describe_source(path)
        except Exception:  # noqa: BLE001
            description = human_size(path.stat().st_size)
        self._source_label.setText(f"{path.name} · {description}")
        peaks = self.context.library.peak_list(path)
        duration = self.context.library.duration_of(path)
        self._progress.set_peaks(peaks, max(0.01, duration)) if peaks else self._progress.clear()
        self._status.setText("Ready to transcribe.")

    def _toggle_recording(self) -> None:
        if self._recorder is not None and self._recorder.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        settings = self.context.settings.audio
        options = RecorderOptions(
            device=resolve_input_device(getattr(settings, "input_device", "")),
            sample_rate=int(getattr(settings, "input_sample_rate", 48000) or 48000),
            channels=int(getattr(settings, "input_channels", 1) or 1),
            gain=float(getattr(settings, "mic_gain", 1.0) or 1.0),
            max_duration_s=600,
        )
        self._recorder = AudioRecorder(options)
        self._recorder.set_level_callback(lambda level, elapsed: None)
        try:
            self._recorder.start()
        except Exception as exc:  # noqa: BLE001
            self._recorder = None
            self.toast("Microphone unavailable", str(exc), severity="error")
            return
        self._record_button.setText("Stop recording")
        self._level.setVisible(True)
        self._level_timer.start()
        self._status.setText(f"Recording from {describe_device(options.device)}…")

    def _poll_level(self) -> None:
        if self._recorder is None:
            return
        self._level.push(self._recorder.level())
        self._record_time.setText(f"{self._recorder.elapsed_s:.1f} s")

    def _stop_recording(self) -> None:
        recorder = self._recorder
        self._level_timer.stop()
        self._level.setVisible(False)
        self._record_button.setText("Record from microphone")
        if recorder is None:
            return
        self._recorder = None
        try:
            result = recorder.stop()
        except Exception as exc:  # noqa: BLE001
            self.toast("Recording failed", str(exc), severity="error")
            self._record_time.setText("")
            return
        finally:
            recorder.close()
        if result.samples.size == 0:
            self.toast("Nothing recorded", "The microphone returned silence.", severity="warning")
            return
        target = self.context.paths.recordings / f"dictation-{_stamp()}.wav"
        from ...audio.recorder import save_recording

        saved = save_recording(result, target, bit_depth=16)
        self._record_time.setText("")
        self._load_source(saved)
        self._pending_audio = (result.samples, result.sample_rate)
        self._status.setText(f"Recorded {human_duration(result.duration_s)} — press Transcribe.")

    # -- transcription ------------------------------------------------------
    def _start_transcription(self) -> None:
        model_id = str(self._model_combo.currentData() or "")
        if not model_id:
            self.toast("No model installed", "Install a Whisper model first.", severity="warning")
            self.goto("models")
            return
        pending = getattr(self, "_pending_audio", None)
        source = self._source_path
        if pending is None and source is None:
            self.toast("No audio", "Choose a file or record from the microphone.", severity="warning")
            return

        settings = self.context.settings.stt
        self._start.disable_with_reason("Transcribing…")
        self._cancel.setEnabled(True)
        self._status.setText("Preparing…")
        self._status_badge.setText("Running")
        self._status_badge.set_severity("info")

        common = dict(
            language=str(self._language.current_key() or "fa"),
            task=str(self._task.current_key() or "transcribe"),
            beam_size=int(self._beam.value()),
            temperature=float(self._temperature.value()),
            vad_filter=self._vad.isChecked(),
            word_timestamps=self._word_times.isChecked(),
            post_process=self._post_process.isChecked(),
            normalize=True,
            diacritize=self._diacritize.isChecked(),
            initial_prompt=str(getattr(settings, "initial_prompt", "") or ""),
            compute_type=str(getattr(settings, "compute_type", "auto") or "auto"),
        )
        if pending is not None:
            samples, sample_rate = pending
            self._job_id = self.context.jobs.submit(
                "Transcribing",
                self._transcribe_samples_job,
                args=(samples, sample_rate),
                kwargs=common,
                kind="stt",
                exclusive=True,
                on_progress=self._on_progress,
                on_finished=self._on_finished,
                on_failed=self._on_failed,
                on_cancelled=self._on_cancelled,
            )
        else:
            self._job_id = self.context.jobs.submit(
                "Transcribing",
                self._transcribe_file_job,
                args=(source,),
                kwargs=common,
                kind="stt",
                exclusive=True,
                on_progress=self._on_progress,
                on_finished=self._on_finished,
                on_failed=self._on_failed,
                on_cancelled=self._on_cancelled,
            )

    def _transcribe_file_job(self, path: Path, *, progress: Any = None, cancel: Any = None, **kwargs: Any) -> Any:
        return self.context.transcription.transcribe_file(path, progress=progress, cancel=cancel, **kwargs)

    def _transcribe_samples_job(
        self, samples: Any, sample_rate: int, *, progress: Any = None, cancel: Any = None, **kwargs: Any
    ) -> Any:
        return self.context.transcription.transcribe_samples(
            samples, sample_rate, progress=progress, cancel=cancel, **kwargs
        )

    def _on_progress(self, payload: dict[str, Any]) -> None:
        detail = str(payload.get("detail") or "")
        if detail:
            self._status.setText(detail)

    def _on_finished(self, payload: dict[str, Any]) -> None:
        self._cancel.setEnabled(False)
        self._start.enable()
        self._status_badge.setText("Done")
        self._status_badge.set_severity("success")
        outcome = payload.get("result")
        if outcome is None:
            self._status.setText("Nothing was transcribed.")
            return
        self._outcome = outcome
        text = outcome.text or outcome.raw_text
        self.view.set_header(
            f"{self._source_path.name if self._source_path else 'Recording'} · "
            f"{human_duration(outcome.duration_s)} · {outcome.language or 'auto'} · "
            f"{len(outcome.segments)} segment(s)"
        )
        self.view.set_segments(outcome.segments)
        self.view.set_text(text)
        self._raw_layout.addWidget(_raw_label(outcome.raw_text))
        self.tabs.setVisible(True)
        for button in (self._copy, self._export_dir, self._save_history):
            button.setEnabled(bool(text.strip()))
        for warning in outcome.warnings:
            if not warning.startswith("ERROR:"):
                self.toast("Note", warning, severity="warning")
        if not text.strip():
            self._status.setText("No speech was detected.")
            self.toast("No speech found", "Try a louder recording or a larger model.", severity="info")
            return
        self._status.setText(
            f"Finished in {outcome.seconds_taken:.1f} s · {outcome.speed_ratio:.1f}× realtime · "
            f"{len(text)} characters"
        )
        self._save_to_history(silent=True)

    def _on_failed(self, payload: dict[str, Any]) -> None:
        self._cancel.setEnabled(False)
        self._start.enable()
        self._status_badge.setText("Failed")
        self._status_badge.set_severity("error")
        self._status.setText("Failed.")
        self.toast("Transcription failed", str(payload.get("error", "")), severity="error")

    def _on_cancelled(self, payload: dict[str, Any]) -> None:
        self._cancel.setEnabled(False)
        self._start.enable()
        self._status_badge.setText("Cancelled")
        self._status_badge.set_severity("neutral")
        self._status.setText("Cancelled — nothing was saved.")

    def _cancel_transcription(self) -> None:
        if self._job_id:
            self.context.jobs.cancel(self._job_id)

    # -- output -------------------------------------------------------------
    def _on_text_edited(self, text: str) -> None:
        if self._outcome is not None:
            self._outcome.text = text
        for button in (self._copy, self._export_dir, self._save_history):
            button.setEnabled(bool(text.strip()))

    def _copy_transcript(self) -> None:
        from PySide6.QtWidgets import QApplication

        text = self.view.text()
        if not text.strip():
            return
        QApplication.clipboard().setText(text)
        self.toast("Copied", f"{len(text)} characters to the clipboard.", severity="success")

    def _export_transcript(self) -> None:
        if self._outcome is None:
            return
        formats = ";;".join(
            [
                "Text (*.txt)",
                "SubRip subtitles (*.srt)",
                "WebVTT subtitles (*.vtt)",
                "JSON (*.json)",
                "Markdown (*.md)",
            ]
        )
        stem = self._source_path.stem if self._source_path else "transcript"
        suggested = str(self.context.paths.exports / f"{stem}.txt")
        path, selected = QFileDialog.getSaveFileName(self, "Export transcript", suggested, formats)
        if not path:
            return
        target = Path(path)
        fmt = selected.split("(")[-1].strip(")*. ") or target.suffix.lstrip(".") or "txt"
        outcome = self._outcome
        outcome.text = self.view.text()
        result = self.context.exports.export_transcript(outcome, target, fmt=fmt)
        if result.ok:
            self.toast("Transcript exported", result.summary(), severity="success")
        else:
            self.toast("Export failed", result.error, severity="error")

    def _save_to_history(self, *, silent: bool = False) -> None:
        if self._outcome is None:
            return
        self._outcome.text = self.view.text()
        payload = self.context.transcription.to_history_entry(self._outcome)
        entry_id = self.context.history.add(**payload)
        if self._source_path is not None:
            self.context.library.register_generated(
                self._source_path,
                history_id=entry_id,
                source_text=self._outcome.text,
                language=self._outcome.language,
                tags=["stt"],
            )
        if not silent:
            self.toast("Saved to history", payload.get("title", ""), severity="success")


def _raw_label(text: str) -> QLabel:
    label = QLabel(text or "(no raw output)")
    label.setObjectName("MonoSmall")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _stamp() -> str:
    import time

    return time.strftime("%Y%m%d-%H%M%S")
