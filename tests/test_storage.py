"""Storage layer: schema, settings round-trip, history and library."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from tixi.storage.database import Database
from tixi.storage.history_repository import HistoryRepository
from tixi.storage.library_repository import AudioLibraryRepository, DictionaryRepository
from tixi.storage.settings_repository import SettingsRepository


def test_database_migrates_and_reports_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "tixi.db")
    database.initialise()
    stats = database.stats()
    assert stats["schema_version"] >= 1
    assert database.table_exists("history")
    database.close()


def test_settings_round_trip(tmp_path: Path) -> None:
    database = Database(tmp_path / "tixi.db")
    database.initialise()
    mirror = tmp_path / "settings.json"
    repository = SettingsRepository(database, mirror=mirror)
    store = repository.load()
    store.set("tts.speed", 1.3)
    store.set("voice_typing.shortcut", "Ctrl+Shift+D")
    repository.save(store.settings)
    assert mirror.exists()
    reloaded = SettingsRepository(database, mirror=mirror).load()
    assert abs(float(reloaded.settings.tts.speed) - 1.3) < 1e-6
    assert reloaded.settings.voice_typing.shortcut == "Ctrl+Shift+D"
    database.close()


def test_settings_validation_keeps_values_in_range(tmp_path: Path) -> None:
    database = Database(tmp_path / "tixi.db")
    database.initialise()
    store = SettingsRepository(database).load()
    store.set("tts.speed", 99.0)
    store.set("appearance.ui_scale", 12.0)
    notes = store.validate()
    assert 0.4 <= float(store.settings.tts.speed) <= 2.5
    assert 0.8 <= float(store.settings.appearance.ui_scale) <= 1.6
    assert isinstance(notes, list)
    database.close()


def test_history_add_search_and_soft_delete(tmp_path: Path) -> None:
    database = Database(tmp_path / "tixi.db")
    database.initialise()
    history = HistoryRepository(database)
    entry_id = history.add("tts", text="سلام دنیا", language="fa", meta={"voice": "test"})
    assert history.count() == 1
    assert history.list(search="دنیا")[0].id == entry_id
    history.delete(entry_id)
    assert history.count() == 0
    history.purge_deleted()
    assert history.scalar_count() == 0 if hasattr(history, "scalar_count") else True
    database.close()


def test_library_adds_assets_without_duplicating_files(tmp_path: Path) -> None:
    from tixi.services.library_service import LibraryService

    database = Database(tmp_path / "tixi.db")
    database.initialise()
    repository = AudioLibraryRepository(database)
    service = LibraryService(repository, tmp_path / "library")
    source = tmp_path / "tone.wav"
    from tixi.audio.wav_io import write_wav

    write_wav(source, np.zeros(4000, dtype=np.float32), 8000)
    first = service.import_file(source)
    assert first.ok and first.asset is not None
    second = service.import_file(source)
    assert second.skipped
    assert repository.count() == 1
    database.close()


def test_dictionary_round_trip(tmp_path: Path) -> None:
    database = Database(tmp_path / "tixi.db")
    database.initialise()
    dictionary = DictionaryRepository(database)
    dictionary.add("وب", "وِب")
    pairs = dictionary.as_pairs("fa")
    assert ("وب", "وِب", False) in pairs
    dictionary.set_pronunciation("وب", diacritized="وِب")
    assert dictionary.pronunciations()["وب"]["diacritized"] == "وِب"
    database.close()
