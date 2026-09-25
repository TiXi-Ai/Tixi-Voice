"""Composition root.

Builds every long-lived object exactly once — paths, database, repositories,
settings, model registry, engine manager and the feature services — and hands
them to the UI through :class:`AppContext`.

Two important properties:

* construction is **fail-soft**: if an optional piece (engine pack, audio
  device, GitHub access) is unavailable the context records a note instead of
  raising, so the application always opens and can explain what is missing;
* nothing here touches the network or the microphone — that only happens when
  the user asks for it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..app.config import Settings, SettingsStore
from ..app.logging_config import get_logger
from ..app.paths import AppPaths, paths
from ..audio.converter import AudioConverter
from ..engines.diacritization.service import DiacritizationService
from ..engines.manager import EngineManager
from ..engines.text_normalization.pipeline import TextPipeline
from ..models.engine_packs import EnginePackManager
from ..models.model_installer import ModelInstaller
from ..models.model_registry import ModelRegistry
from ..services.dictation_service import DictationService
from ..services.export_service import ExportService
from ..services.jobs import JobRunner
from ..services.library_service import LibraryService
from ..services.model_service import ModelService
from ..services.synthesis_service import SynthesisService
from ..services.transcription_service import TranscriptionService
from ..services.update_service import UpdateCenter
from ..storage.database import Database
from ..storage.history_repository import HistoryRepository
from ..storage.library_repository import (
    AudioLibraryRepository,
    DiacritizationRepository,
    DictionaryRepository,
    TranscriptionRepository,
)
from ..storage.settings_repository import SettingsRepository

log = get_logger("tixi.app.bootstrap")

#: Catalogue id of the neural Persian diacritizer (see models/catalog.py).
DIACRITIZER_MODEL_ID = "canine-fa-diacritizer"
LEXICON_MODEL_ID = "fa-pronunciation-lexicon"


@dataclass
class AppContext:
    """Everything the UI needs, wired together."""

    paths: AppPaths
    database: Database
    settings_store: SettingsStore
    settings_repository: SettingsRepository

    history: HistoryRepository
    library_repository: AudioLibraryRepository
    transcriptions: TranscriptionRepository
    diacritizations: DiacritizationRepository
    dictionary: DictionaryRepository

    model_registry: ModelRegistry
    model_installer: ModelInstaller
    engine_packs: EnginePackManager
    engine_manager: EngineManager

    pipeline: TextPipeline
    diacritization: DiacritizationService

    jobs: JobRunner
    models: ModelService
    library: LibraryService
    synthesis: SynthesisService
    transcription: TranscriptionService
    exports: ExportService
    updates: UpdateCenter
    dictation: DictationService | None = None

    notes: list[str] = field(default_factory=list)
    _started: bool = False

    # -- convenience --------------------------------------------------------
    @property
    def settings(self) -> Settings:
        return self.settings_store.settings

    def save_settings(self) -> None:
        """Persist the current settings to SQLite + the JSON mirror."""
        try:
            self.settings_repository.save(self.settings_store.settings)
        except Exception:  # noqa: BLE001 - never lose the app because of a disk hiccup
            log.exception("settings could not be saved", extra={"event": "settings_save_failed"})

    def refresh_pipeline(self) -> None:
        """Re-read dictionary entries and pronunciation overrides from the DB."""
        try:
            self.pipeline.set_dictionary_entries(self.dictionary.as_pairs("fa"))
            self.pipeline.set_pronunciation_overrides(self.dictionary.pronunciations())
        except Exception:  # noqa: BLE001
            log.exception("the text pipeline could not be refreshed")

    def note(self, message: str) -> None:
        if message and message not in self.notes:
            self.notes.append(message)
            log.info("startup note", extra={"event": "startup_note", "note": message})

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            from ..models.engine_packs import EnginePackManager as _Manager

            activated = self.engine_packs.activate()
            if activated:
                log.info(
                    "engine packs activated",
                    extra={"event": "packs_activated", "entries": len(activated)},
                )
        except Exception as exc:  # noqa: BLE001
            self.note(f"Engine packs could not be activated: {exc}")
        try:
            report = self.model_registry.reconcile()
            missing = report.get("missing") or []
            if missing:
                self.note(
                    f"{len(missing)} installed model(s) are missing from disk and were marked as such."
                )
        except Exception:  # noqa: BLE001
            log.exception("model reconciliation failed")

    def shutdown(self) -> None:
        """Stop background work and close the database cleanly."""
        try:
            if self.dictation is not None:
                self.dictation.shutdown()
        except Exception:  # noqa: BLE001
            log.exception("dictation shutdown failed")
        try:
            self.jobs.cancel_all()
            self.jobs.wait_for_all(3000)
        except Exception:  # noqa: BLE001
            log.exception("jobs could not be stopped cleanly")
        try:
            self.engine_manager.unload_idle(force=True)
        except Exception:  # noqa: BLE001
            log.exception("engines could not be unloaded")
        try:
            self.save_settings()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.database.close()
        except Exception:  # noqa: BLE001
            log.exception("the database could not be closed")


def build_context(*, offline: bool = False, model_dir: Path | None = None) -> AppContext:
    """Create the whole object graph.  Called once, from ``application.main``."""
    location = paths()
    location.ensure()
    notes: list[str] = []

    database = Database(location.database_file)
    database.initialise()

    settings_repository = SettingsRepository(database, mirror=location.settings_file)
    settings_store = settings_repository.load()

    history = HistoryRepository(database)
    library_repository = AudioLibraryRepository(database)
    transcriptions = TranscriptionRepository(database)
    diacritizations = DiacritizationRepository(database)
    dictionary = DictionaryRepository(database)

    model_registry = ModelRegistry(database, model_dir=model_dir)
    model_installer = ModelInstaller(model_registry)
    engine_packs = EnginePackManager()
    engine_manager = EngineManager(model_registry, settings_provider=lambda: settings_store.settings)

    pipeline = TextPipeline()
    try:
        pipeline.set_dictionary_entries(dictionary.as_pairs("fa"))
        pipeline.set_pronunciation_overrides(dictionary.pronunciations())
    except Exception as exc:  # noqa: BLE001
        notes.append(f"The custom dictionary could not be loaded: {exc}")

    diacritization = build_diacritization(model_registry, settings_store, dictionary)

    jobs = JobRunner(max_threads=4)
    model_service = ModelService(
        model_registry,
        installer=model_installer,
        packs=engine_packs,
        allow_network=not offline,
    )
    library_service = LibraryService(
        library_repository, location.recordings, converter=AudioConverter()
    )
    synthesis = SynthesisService(
        lambda: _safe_engine(engine_manager, "tts", notes), pipeline=pipeline
    )
    transcription = TranscriptionService(
        lambda: _safe_engine(engine_manager, "stt", notes), pipeline=pipeline
    )
    exports = ExportService(
        library_service,
        transcription,
        settings_provider=lambda: settings_store.settings,
    )
    updates = UpdateCenter(settings_provider=lambda: settings_store.settings, offline=offline)

    context = AppContext(
        paths=location,
        database=database,
        settings_store=settings_store,
        settings_repository=settings_repository,
        history=history,
        library_repository=library_repository,
        transcriptions=transcriptions,
        diacritizations=diacritizations,
        dictionary=dictionary,
        model_registry=model_registry,
        model_installer=model_installer,
        engine_packs=engine_packs,
        engine_manager=engine_manager,
        pipeline=pipeline,
        diacritization=diacritization,
        jobs=jobs,
        models=model_service,
        library=library_service,
        synthesis=synthesis,
        transcription=transcription,
        exports=exports,
        updates=updates,
        notes=notes,
    )

    try:
        context.dictation = DictationService(
            engine_provider=lambda: _safe_engine(engine_manager, "stt", notes),
            pipeline_provider=lambda: context.pipeline,
            diacritizer_provider=lambda: diacritization,
            settings_provider=lambda: settings_store.settings,
        )
    except Exception as exc:  # noqa: BLE001 - voice typing is optional at startup
        context.note(f"Voice typing could not be initialised: {exc}")

    return context


def build_diacritization(
    registry: ModelRegistry,
    settings_store: SettingsStore,
    dictionary: DictionaryRepository | None = None,
) -> DiacritizationService:
    """Attach the neural diacritizer when its model is installed, else keep the lexicon."""
    service = DiacritizationService()
    ai_settings = getattr(settings_store.settings, "ai", None)
    if ai_settings is not None:
        service.settings.use_neural = bool(getattr(ai_settings, "use_neural_diacritization", True))
        service.settings.learn_from_corrections = bool(
            getattr(ai_settings, "learn_from_corrections", True)
        )
    record = registry.get(DIACRITIZER_MODEL_ID)
    if record is not None and record.status == "installed":
        model_path = record.resolved_path()
        if model_path is not None and service.set_neural_model(model_path):
            log.info("neural diacritizer ready", extra={"event": "diacritizer_ready"})
    if dictionary is not None:
        try:
            service.load_overrides(dictionary.pronunciations())
        except Exception:  # noqa: BLE001
            log.exception("pronunciation overrides could not be loaded")
    return service


def _safe_engine(manager: EngineManager, kind: str, notes: list[str]) -> Any:
    """Return an engine or ``None`` (the services turn ``None`` into advice)."""
    try:
        if kind == "tts":
            return manager.tts_engine("piper")
        return manager.stt_engine("faster-whisper")
    except Exception as exc:  # noqa: BLE001 - "not installed" is a normal state
        message = f"{type(exc).__name__}: {exc}"
        if message not in notes:
            notes.append(message)
        log.info("engine unavailable", extra={"event": "engine_unavailable", "kind": kind, "error": str(exc)})
        return None


def describe_environment() -> dict[str, Any]:
    """Diagnostics shown on the About page and in a support report."""
    import platform

    data: dict[str, Any] = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "packages": {},
    }
    for name in ("PySide6", "numpy", "sounddevice", "soundfile", "requests", "lameenc",
                 "onnxruntime", "ctranslate2", "faster_whisper", "piper", "pynput"):
        try:
            module = __import__(name)
            version = getattr(module, "__version__", "")
            data["packages"][name] = version or "installed"
        except Exception:  # noqa: BLE001 - absence is the interesting bit
            data["packages"][name] = None
    return data
