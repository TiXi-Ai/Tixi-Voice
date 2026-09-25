"""AI model + engine pack service.

One façade for everything the *AI Models* page needs: what is installed, what
can be downloaded, how big it is, whether the engine that runs it is present,
and how to install or remove either one.

Nothing here downloads silently — every operation is triggered by an explicit
UI action, reports progress through a ``(fraction, message)`` callback and can
be cancelled.  A model only becomes *installed* after it has been validated on
disk by :mod:`tixi.models.model_validator`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..app.logging_config import get_logger
from ..models.catalog import CATEGORY_LABELS, ModelCatalog, ModelSpec
from ..models.catalog import CATALOG as DEFAULT_CATALOG
from ..models.engine_packs import (
    ENGINE_PACKS,
    EnginePack,
    EnginePackManager,
    install_command_for,
    install_hint,
    pack_for_id,
)
from ..models.model_installer import InstallResult, ModelInstaller
from ..models.model_registry import STATUS_ERROR, STATUS_INSTALLED, STATUS_MISSING, ModelRegistry
from ..models.model_validator import ValidationReport
from ..utils.humanize import human_size

log = get_logger("tixi.services.models")

Reporter = Callable[[float, str], None]


@dataclass
class ModelRow:
    """A catalogue entry merged with its local state — one row in the UI."""

    spec: ModelSpec
    installed: bool = False
    status: str = STATUS_MISSING
    path: Path | None = None
    size_on_disk: int = 0
    partial_bytes: int = 0
    pack_id: str = ""
    pack_installed: bool = True
    error: str = ""

    # -- convenient pass-throughs ------------------------------------------
    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def category(self) -> str:
        return self.spec.category

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.spec.category, self.spec.category)

    @property
    def languages(self) -> list[str]:
        return list(self.spec.languages)

    @property
    def language_label(self) -> str:
        if self.spec.language_names:
            return ", ".join(self.spec.language_names)
        return ", ".join(self.spec.languages) or "Unknown"

    @property
    def persian_support(self) -> str:
        return self.spec.persian_support

    @property
    def persian_capable(self) -> bool:
        return self.spec.persian_support in ("native", "verified-multilingual")

    @property
    def persian_label(self) -> str:
        return {
            "native": "Persian (native)",
            "verified-multilingual": "Persian (verified multilingual)",
            "none": "No Persian",
            "unknown": "Persian support not verified",
        }.get(self.spec.persian_support, "Persian support not verified")

    @property
    def gated(self) -> bool:
        return bool(self.spec.hf_token_required)

    @property
    def download_bytes(self) -> int:
        return int(self.spec.download_bytes or 0)

    @property
    def download_label(self) -> str:
        return human_size(self.download_bytes) if self.download_bytes else "bundled"

    @property
    def installed_label(self) -> str:
        size = self.size_on_disk or self.spec.installed_bytes
        return human_size(size) if size else "—"

    @property
    def status_label(self) -> str:
        if self.installed:
            return "Installed"
        if self.error:
            return f"Needs attention: {self.error}"
        if self.partial_bytes:
            return f"Partly downloaded ({human_size(self.partial_bytes)}) — resumable"
        if not self.pack_installed:
            pack = pack_for_id(self.pack_id)
            name = pack.name if pack else self.pack_id
            return f"Needs the {name} engine pack"
        return "Not installed"

    @property
    def recommended(self) -> bool:
        return bool(self.spec.recommended)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "category_label": self.category_label,
            "engine": self.spec.engine,
            "languages": self.languages,
            "language_label": self.language_label,
            "persian_support": self.persian_support,
            "persian_label": self.persian_label,
            "persian_capable": self.persian_capable,
            "size": self.download_label,
            "installed": self.installed,
            "status": self.status_label,
            "license": self.spec.license,
            "license_url": self.spec.license_url,
            "homepage": self.spec.homepage,
            "source_url": self.spec.source_url,
            "hardware": self.spec.hardware,
            "gpu": self.spec.gpu,
            "quantization": self.spec.quantization,
            "description": self.spec.description,
            "notes": self.spec.notes,
            "verified": self.spec.verified,
            "gated": self.gated,
            "recommended": self.recommended,
            "pack_id": self.pack_id,
        }


@dataclass
class PackRow:
    """An engine pack merged with its local state."""

    pack: EnginePack
    installed: bool = False
    version: str = ""
    path: str = ""
    size_bytes: int = 0
    modules_available: bool = False
    error: str = ""
    needed_by: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.needed_by is None:
            self.needed_by = []

    @property
    def id(self) -> str:
        return self.pack.id

    @property
    def label(self) -> str:
        return self.pack.name

    @property
    def size_label(self) -> str:
        return human_size(self.size_bytes or self.pack.download_bytes or self.pack.installed_bytes)

    @property
    def requires_restart(self) -> bool:
        """An installed pack whose modules are not importable needs a restart."""
        return bool(self.installed and not self.modules_available)

    @property
    def status_label(self) -> str:
        if not self.installed:
            return "Not installed"
        if self.error:
            return f"Installed, but broken: {self.error}"
        if self.requires_restart:
            return "Installed — restart Tixi Voice to activate it"
        return f"Installed{f' v{self.version}' if self.version else ''}"

    @property
    def install_command(self) -> str:
        return install_command_for(self.pack)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "installed": self.installed,
            "size": self.size_label,
            "version": self.version,
            "status": self.status_label,
            "description": self.pack.description,
            "license": self.pack.license,
            "license_note": self.pack.license_note,
            "notes": self.pack.notes,
            "required_for": list(self.needed_by or []),
            "install_command": self.install_command,
            "requires_restart": self.requires_restart,
        }


class ModelService:
    """Read/write access to models and engine packs."""

    def __init__(
        self,
        registry: ModelRegistry,
        installer: ModelInstaller | None = None,
        packs: EnginePackManager | None = None,
        catalog: ModelCatalog | None = None,
        *,
        allow_network: bool = True,
    ) -> None:
        self.registry = registry
        self.catalog = catalog or DEFAULT_CATALOG
        self.installer = installer or ModelInstaller(registry)
        self.packs = packs or EnginePackManager()
        self.allow_network = allow_network

    # -- queries ------------------------------------------------------------
    def specs(
        self,
        *,
        category: str = "",
        search: str = "",
        persian_only: bool = False,
        sort: str = "recommended",
    ) -> list[ModelSpec]:
        if search or category or persian_only:
            return self.catalog.search(
                search, category=category, persian_only=persian_only, sort=sort
            )
        return self.catalog.all()

    def rows(
        self,
        *,
        category: str = "",
        search: str = "",
        persian_only: bool = False,
        installed_only: bool = False,
    ) -> list[ModelRow]:
        specs = self.specs(category=category, search=search, persian_only=persian_only)
        if installed_only:
            specs = [spec for spec in specs if self.registry.is_installed(spec.id)]
        pack_status = {row.id: row for row in self.pack_rows()}
        rows: list[ModelRow] = []
        for spec in specs:
            record = self.registry.get(spec.id)
            needed = spec.requires_engine_pack
            first = needed[0] if needed else ""
            pack_row = pack_status.get(first)
            rows.append(
                ModelRow(
                    spec=spec,
                    installed=bool(record and record.status == STATUS_INSTALLED),
                    status=record.status if record else STATUS_MISSING,
                    path=(record.resolved_path() if record else None),
                    size_on_disk=(record.size_bytes if record else 0),
                    partial_bytes=self.installer.staging_size(spec),
                    pack_id=first,
                    pack_installed=True if not first else bool(pack_row and pack_row.installed),
                    error=(record.error if record and record.status == STATUS_ERROR else ""),
                )
            )
        return rows

    def row(self, model_id: str) -> ModelRow | None:
        for row in self.rows():
            if row.id == model_id:
                return row
        return None

    def spec(self, model_id: str) -> ModelSpec | None:
        return self.catalog.get(model_id)

    def require_spec(self, model_id: str) -> ModelSpec:
        spec = self.catalog.get(model_id)
        if spec is None:
            raise KeyError(f"Unknown model: {model_id}")
        return spec

    def installed_rows(self) -> list[ModelRow]:
        return self.rows(installed_only=True)

    def pack_rows(self) -> list[PackRow]:
        needed: dict[str, list[str]] = {}
        for spec in self.catalog.all():
            for pack_id in spec.requires_engine_pack:
                needed.setdefault(pack_id, []).append(spec.name)
        rows: list[PackRow] = []
        for pack in ENGINE_PACKS:
            status = self.packs.status(pack)
            rows.append(
                PackRow(
                    pack=pack,
                    installed=bool(status.installed),
                    version=status.version,
                    path=status.path,
                    size_bytes=status.size_bytes,
                    modules_available=bool(status.modules_available),
                    error=status.error,
                    needed_by=needed.get(pack.id, []),
                )
            )
        return rows

    def pack_row(self, pack_id: str) -> PackRow | None:
        for row in self.pack_rows():
            if row.id == pack_id:
                return row
        return None

    def summary(self) -> dict[str, Any]:
        installed = self.registry.all()
        total = self.registry.total_size()
        packs = self.pack_rows()
        return {
            "catalog_count": len(self.catalog.all()),
            "installed_count": sum(1 for model in installed if model.status == STATUS_INSTALLED),
            "installed_label": human_size(total),
            "installed_bytes": total,
            "packs_installed": sum(1 for row in packs if row.installed),
            "packs_total": len(packs),
            "models_dir": str(self.registry.model_dir),
            "pack_dir": str(getattr(self.packs, "root", "")),
            "persian_tts_installed": self.has_persian(kind="tts_voice"),
            "persian_stt_installed": self.has_persian(kind="stt"),
            "diacritizer_installed": self.registry.is_installed("persian-diacritizer-onnx"),
        }

    def has_persian(self, *, kind: str = "tts_voice") -> bool:
        return any(
            row.installed and row.persian_capable
            for row in self.rows()
            if row.category == kind or (kind == "tts_voice" and row.category == "tts_multilingual")
        )

    def default_voice_id(self) -> str:
        spec = self.catalog.default_for("tts_voice")
        return spec.id if spec else ""

    def default_stt_id(self) -> str:
        spec = self.catalog.default_for("stt")
        return spec.id if spec else ""

    def hint_for(self, model_id: str) -> str:
        spec = self.catalog.get(model_id)
        if spec is None or not spec.requires_engine_pack:
            return ""
        return install_hint(spec.requires_engine_pack)

    def total_download_size(self, model_ids: Iterable[str]) -> int:
        return self.catalog.total_download_size(list(model_ids))

    # -- operations --------------------------------------------------------
    def install_model(
        self,
        model_id: str,
        *,
        progress: Reporter | None = None,
        install_pack: bool = True,
        hf_token: str = "",
    ) -> InstallResult:
        """Install the engine pack first (if needed), then the model itself."""
        spec = self.require_spec(model_id)
        if hf_token:
            self.installer.set_token(hf_token)
        pack_ids = list(spec.requires_engine_pack)
        if install_pack:
            for index, pack_id in enumerate(pack_ids):
                row = self.pack_row(pack_id)
                if row is not None and not row.installed:
                    span = 0.35 / max(1, len(pack_ids))
                    self.install_pack(
                        pack_id,
                        progress=_scaled(progress, index * span, (index + 1) * span),
                    )
        return self.installer.install(
            spec,
            progress=_progress_adapter(progress, 0.35 if pack_ids else 0.0, 1.0)
            if pack_ids
            else _progress_adapter(progress, 0.0, 1.0),
        )

    def install_pack(self, pack_id: str, *, progress: Reporter | None = None) -> Any:
        if not self.allow_network:
            raise RuntimeError(
                "Engine packs are downloaded from PyPI, which needs a network connection. "
                "Offline Mode is enabled in Settings ▸ Privacy."
            )
        from ..models.engine_packs import PackProgress  # noqa: PLC0415 - local alias

        def on_pack(pack_progress: PackProgress) -> None:
            if progress is None:
                return
            detail = pack_progress.message or pack_progress.status
            progress(pack_progress.fraction, f"{detail} ({human_size(pack_progress.bytes_done)} of "
                     f"{human_size(pack_progress.bytes_total)})" if pack_progress.bytes_total else detail)

        return self.packs.install(pack_id, progress=on_pack if progress else None)

    def cancel(self) -> None:
        """Ask the running installation to stop (called from the UI thread)."""
        self.installer.cancel()
        self.packs.cancel()

    def pause(self) -> None:
        self.installer.pause()

    def resume(self) -> None:
        self.installer.resume()

    def remove_model(self, model_id: str, *, delete_files: bool = True) -> bool:
        return bool(self.installer.remove(self.require_spec(model_id), delete_files=delete_files))

    def remove_pack(self, pack_id: str) -> bool:
        return bool(self.packs.uninstall(pack_id))

    def verify_model(self, model_id: str, *, deep: bool = False) -> ValidationReport:
        return self.installer.verify(self.require_spec(model_id), deep=deep)

    def import_model(self, model_id: str, source: Path, *, progress: Reporter | None = None) -> InstallResult:
        return self.installer.import_local(
            self.require_spec(model_id),
            Path(source),
            progress=_progress_adapter(progress, 0.0, 1.0) if progress else None,
        )

    def discard_partial(self, model_id: str) -> bool:
        """Delete resumable staging files for a model (only after a confirmation)."""
        return bool(self.installer.discard_staging(self.require_spec(model_id)))

    def reconcile(self) -> dict[str, list[str]]:
        """Reconcile the database with what is actually on disk."""
        return self.registry.reconcile()

    def set_token(self, token: str) -> None:
        """Hugging Face token used for gated models (stored by the settings layer)."""
        self.installer.set_token(token)

    def describe_install_error(self, error: str) -> str:
        """Turn a raw error into something the user can act on."""
        lowered = (error or "").lower()
        if "401" in lowered or "403" in lowered or "gated" in lowered:
            return (
                "This model is gated on Hugging Face: open its page, accept the licence, then paste "
                "your Hugging Face access token in Settings ▸ AI."
            )
        if "sha256" in lowered or "checksum" in lowered:
            return "The download did not match its checksum. Try again; if it keeps failing, report it."
        if "space" in lowered or "disk" in lowered:
            return "There is not enough free disk space for this model."
        return error


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _progress_adapter(progress: Reporter | None, start: float, end: float) -> Callable[[Any], None] | None:
    """Convert the model layer's rich progress objects into ``(fraction, text)``."""
    if progress is None:
        return None

    def on_progress(item: Any) -> None:
        fraction = getattr(item, "fraction", 0.0) or 0.0
        text = ""
        describe = getattr(item, "describe", None)
        if callable(describe):
            try:
                text = str(describe())
            except Exception:  # noqa: BLE001
                text = ""
        if not text:
            text = str(getattr(item, "message", "") or getattr(item, "file_name", "") or getattr(item, "status", ""))
        progress(start + (end - start) * max(0.0, min(1.0, float(fraction))), text)

    return on_progress


def _scaled(progress: Reporter | None, start: float, end: float) -> Reporter | None:
    if progress is None:
        return None

    def report(fraction: float, detail: str = "") -> None:
        fraction = max(0.0, min(1.0, float(fraction)))
        progress(start + (end - start) * fraction, detail)

    return report
