"""Feature services with stub engines: no model and no microphone required."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from tixi.engines.base import SynthesisRequest, SynthesisResult, TranscriptionRequest, TranscriptionResult, TranscriptSegment
from tixi.services.jobs import JobRunner
from tixi.services.synthesis_service import SynthesisService
from tixi.services.transcription_service import TranscriptionService


class _StubTTSEngine:
    engine_id = "stub-tts"
    display_name = "Stub voice"

    def __init__(self, rate: int = 22050) -> None:
        self.rate = rate
        self.calls = 0

    def synthesize(self, request: SynthesisRequest, *, progress=None, cancel=None) -> SynthesisResult:  # noqa: ANN001
        self.calls += 1
        seconds = max(0.05, len(request.text) / 40.0)
        tone = np.zeros(int(seconds * self.rate), dtype=np.float32)
        return SynthesisResult(samples=tone, sample_rate=self.rate, voice_id=request.voice_id or "stub")


class _StubSTTEngine:
    engine_id = "stub-stt"
    display_name = "Stub recogniser"

    def __init__(self) -> None:
        self.requests = 0

    def transcribe(self, request: TranscriptionRequest, *, progress=None, cancel=None) -> TranscriptionResult:  # noqa: ANN001
        self.requests += 1
        segments = [
            TranscriptSegment(index=0, start=0.0, end=1.0, text="سلام دنیا"),
            TranscriptSegment(index=1, start=1.0, end=2.0, text="حال شما چطور است"),
        ]
        return TranscriptionResult(
            text=" ".join(segment.text for segment in segments),
            segments=segments,
            language="fa",
            duration_s=2.0,
            model_id="stub",
            engine_id=self.engine_id,
        )


def test_synthesis_chunks_and_writes(tmp_path: Path) -> None:
    engine = _StubTTSEngine()
    service = SynthesisService(lambda: engine, sample_rate=22050)
    outcome = service.synthesize(
        "یک جمله کوتاه. و یک جمله دیگر؟",
        voice_id="stub",
        language="fa",
        output_format="wav",
        write_to=tmp_path / "out.wav",
        sentence_pause_ms=50,
    )
    assert outcome.ok
    assert outcome.result is not None
    assert outcome.output_path is not None and outcome.output_path.exists()
    assert outcome.duration_s > 0
    assert engine.calls >= 1


def test_empty_text_is_rejected_with_a_message() -> None:
    service = SynthesisService(lambda: _StubTTSEngine())
    outcome = service.synthesize("   ")
    assert outcome.result is None
    assert any("ERROR" in warning for warning in outcome.warnings)


def test_missing_engine_is_reported_not_raised() -> None:
    service = SynthesisService(lambda: None)
    outcome = service.synthesize("سلام")
    assert outcome.result is None
    assert any("not installed" in warning for warning in outcome.warnings)


def test_transcription_renders_caption_formats() -> None:
    engine = _StubSTTEngine()
    service = TranscriptionService(lambda: engine)
    outcome = service.transcribe_samples(np.zeros(16000, dtype=np.float32), 16000, language="fa")
    assert outcome.ok
    assert outcome.text
    srt = service.render(outcome, fmt="srt")
    vtt = service.render(outcome, fmt="vtt")
    payload = service.render(outcome, fmt="json")
    assert "00:00:00,000" in srt
    assert vtt.startswith("WEBVTT")
    assert "\"segments\"" in payload


def test_export_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    engine = _StubSTTEngine()
    service = TranscriptionService(lambda: engine)
    outcome = service.transcribe_samples(np.zeros(16000, dtype=np.float32), 16000)
    target = tmp_path / "transcript.txt"
    first = service.export(outcome, target)
    first.write_text("edited by the user", encoding="utf-8")
    second = service.export(outcome, target)
    assert first != second
    assert first.read_text(encoding="utf-8") == "edited by the user"


def test_job_runner_reports_results_and_failures() -> None:
    from PySide6.QtCore import QCoreApplication

    application = QCoreApplication.instance() or QCoreApplication([])
    runner = JobRunner(max_threads=2)
    done: list[dict] = []
    failed: list[dict] = []

    def work(*, progress=None, cancel=None) -> int:  # noqa: ANN001
        if progress:
            progress(0.5, "half way")
        return 42

    def boom(*, progress=None, cancel=None) -> int:  # noqa: ANN001
        raise ValueError("nope")

    runner.submit("work", work, on_finished=done.append, kind="test-work")
    runner.submit("boom", boom, on_failed=failed.append, kind="test-boom")
    assert runner.wait_for_all(8000)
    for _ in range(50):
        application.processEvents()
    assert done and done[0]["result"] == 42
    assert failed and "nope" in failed[0]["error"]
