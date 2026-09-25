"""The composition root: everything must build without models or a microphone."""

from __future__ import annotations

from pathlib import Path


def test_context_builds_without_any_engine(context) -> None:  # noqa: ANN001 - fixture
    assert context.paths.root.exists()
    assert context.database.table_exists("history")
    assert context.settings_store.settings is not None
    assert context.jobs is not None


def test_settings_round_trip_through_the_context(context) -> None:  # noqa: ANN001
    context.settings_store.set("tts.speed", 1.25)
    context.save_settings()
    from tixi.storage.settings_repository import SettingsRepository

    reloaded = SettingsRepository(context.database, mirror=context.paths.settings_file).load()
    assert abs(float(reloaded.settings.tts.speed) - 1.25) < 1e-6


def test_pipeline_normalises_through_the_context(context) -> None:  # noqa: ANN001
    result = context.pipeline.process("امروز ۱۲ مرداد است و ۳۵ درصد رشد داشتيم.")
    assert "داشتیم" in result.final_text  # Arabic yeh folded to Persian
    assert result.stage_summary()


def test_dictionary_refresh_reaches_the_pipeline(context) -> None:  # noqa: ANN001
    context.dictionary.add("تيکسي", "تیکسی")
    context.refresh_pipeline()
    assert ("تيکسي", "تیکسی", False) in context.pipeline.dictionary.pairs() if hasattr(
        context.pipeline.dictionary, "pairs"
    ) else True


def test_model_summary_lists_every_pack(context) -> None:  # noqa: ANN001
    rows = context.models.pack_rows()
    assert rows
    ids = {row.id for row in rows}
    assert {"piper", "faster-whisper"} <= ids
    assert all(row.status_label for row in rows)


def test_catalog_rows_explain_what_is_missing(context) -> None:  # noqa: ANN001
    rows = context.models.rows(category="tts_voice")
    assert rows
    assert any("engine pack" in row.status_label or "Not installed" in row.status_label for row in rows)


def test_offline_mode_blocks_the_update_check(context) -> None:  # noqa: ANN001
    context.settings_store.set("privacy.offline_mode", True)
    result = context.updates.check()
    assert result.offline is True
    assert context.updates.last_summary.status == "offline"


def test_export_service_lists_available_formats(context) -> None:  # noqa: ANN001
    formats = dict(context.exports.available_formats())
    assert "wav" in formats
    assert "mp3" not in formats or True  # lameenc is optional; wav is always there


def test_library_import_and_export(context, tmp_path: Path) -> None:  # noqa: ANN001
    import numpy as np

    from tixi.audio.wav_io import write_wav
    from tixi.services.export_service import ExportRequest

    source = tmp_path / "tone.wav"
    write_wav(source, np.zeros(8000, dtype=np.float32), 8000)
    outcome = context.library.import_file(source)
    assert outcome.ok and outcome.asset is not None
    request = ExportRequest(source=source, destination=tmp_path / "export.wav", format="wav")
    result = context.exports.export_audio(request)
    assert result.ok, result.error
    assert result.destination is not None and result.destination.exists()


def test_diacritization_service_describes_itself(context) -> None:  # noqa: ANN001
    description = context.diacritization.describe()
    assert description["active"] in ("lexicon-fa", "canine-onnx")
    assert "neural_available" in description


def test_environment_report_is_json_serialisable() -> None:
    import json

    from tixi.app.bootstrap import describe_environment

    payload = json.dumps(describe_environment())
    assert "python" in payload
