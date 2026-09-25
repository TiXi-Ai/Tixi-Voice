"""Model catalogue, registry and validation."""

from __future__ import annotations

from pathlib import Path

from tixi.models.catalog import CATALOG
from tixi.models.engine_packs import ENGINE_PACKS, pack_for_id
from tixi.models.model_registry import ModelRegistry
from tixi.storage.database import Database


def test_catalog_has_persian_voices_and_whisper() -> None:
    ids = {spec.id for spec in CATALOG.all()}
    assert "piper-fa-ir-amir" in ids
    assert "piper-fa-ir-gyro" in ids
    assert "whisper-small" in ids
    assert "whisper-large-v3" in ids


def test_persian_support_is_never_claimed_without_a_language_tag() -> None:
    for spec in CATALOG.all():
        if spec.persian_support in ("native", "verified-multilingual"):
            assert "fa" in spec.languages, f"{spec.id} claims Persian but lists no Persian language"


def test_every_spec_has_a_licence() -> None:
    missing = [spec.id for spec in CATALOG.all() if not spec.license]
    assert not missing, f"models without a documented licence: {missing}"


def test_engine_packs_are_documented() -> None:
    assert {pack.id for pack in ENGINE_PACKS} >= {"piper", "faster-whisper", "diacritization"}
    piper = pack_for_id("piper")
    assert piper is not None
    assert "GPL" in (piper.license or "").upper()
    assert piper.license_note


def test_registry_tracks_installed_models(tmp_path: Path) -> None:
    database = Database(tmp_path / "tixi.db")
    database.initialise()
    registry = ModelRegistry(database, model_dir=tmp_path / "models")
    spec = CATALOG.get("whisper-small")
    assert spec is not None
    path = registry.install_path(spec)
    path.mkdir(parents=True, exist_ok=True)
    registry.register(spec, path, size_bytes=1024, status="installed")
    assert registry.is_installed(spec.id)
    assert registry.total_size() >= 1024
    assert registry.remove(spec.id, delete_files=True)
    assert not registry.is_installed(spec.id)
    database.close()


def test_validator_reports_missing_files(tmp_path: Path) -> None:
    from tixi.models.model_validator import validate_model

    spec = CATALOG.get("whisper-small")
    assert spec is not None
    empty = tmp_path / "empty"
    empty.mkdir()
    report = validate_model(spec, empty)
    assert not report.ok
    assert report.issues
