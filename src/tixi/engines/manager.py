"""Engine manager: discovery, lazy loading, idle unloading, hardware reporting.

Only one TTS and one STT model are kept in memory at a time by default (see
``AISettings.max_loaded_models``), and models are unloaded after a configurable
idle period so a 3 GB Whisper model does not sit in RAM all day.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..app.logging_config import get_logger
from ..models.model_registry import InstalledModel, ModelRegistry
from .base import BaseEngine, EngineUnavailable, STTEngine, TTSEngine

log = get_logger("tixi.engines.manager")


@dataclass
class HardwareProfile:
    """What this machine can do (used for diagnostics and defaults)."""

    cpu_count: int = 0
    cpu_threads: int = 0
    platform: str = ""
    total_memory_mb: int = 0
    available_memory_mb: int = 0
    cuda_available: bool = False
    cuda_device: str = ""
    cuda_memory_mb: int = 0
    directml_available: bool = False
    onnx_providers: list[str] = field(default_factory=list)
    torch_available: bool = False
    ctranslate2_available: bool = False
    recommended_device: str = "cpu"
    recommended_compute_type: str = "int8"
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = [f"{self.cpu_count} CPU cores", self.platform or "unknown platform"]
        if self.total_memory_mb:
            bits.append(f"{self.total_memory_mb / 1024:.1f} GB RAM")
        if self.cuda_available:
            bits.append(f"CUDA: {self.cuda_device}")
        else:
            bits.append("no CUDA GPU detected")
        return " · ".join(bits)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cpu_count": self.cpu_count,
            "cpu_threads": self.cpu_threads,
            "platform": self.platform,
            "total_memory_mb": self.total_memory_mb,
            "available_memory_mb": self.available_memory_mb,
            "cuda_available": self.cuda_available,
            "cuda_device": self.cuda_device,
            "cuda_memory_mb": self.cuda_memory_mb,
            "onnx_providers": list(self.onnx_providers),
            "recommended_device": self.recommended_device,
            "recommended_compute_type": self.recommended_compute_type,
            "notes": list(self.notes),
        }


def detect_hardware() -> HardwareProfile:
    """Detect CPU/GPU/memory capabilities without importing heavy libraries."""
    import os
    import platform

    profile = HardwareProfile(
        cpu_count=os.cpu_count() or 1,
        platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
    )
    profile.cpu_threads = max(1, int(profile.cpu_count * 0.75))

    try:
        import psutil  # noqa: PLC0415 - optional

        memory = psutil.virtual_memory()
        profile.total_memory_mb = int(memory.total / 1024 / 1024)
        profile.available_memory_mb = int(memory.available / 1024 / 1024)
    except Exception:  # noqa: BLE001
        profile.total_memory_mb = _fallback_memory_mb()
        profile.available_memory_mb = profile.total_memory_mb

    try:
        import onnxruntime  # noqa: PLC0415

        profile.onnx_providers = list(onnxruntime.get_available_providers())
    except Exception:  # noqa: BLE001
        profile.onnx_providers = []

    try:
        import torch  # noqa: PLC0415 - optional

        profile.torch_available = True
        if torch.cuda.is_available():
            profile.cuda_available = True
            profile.cuda_device = torch.cuda.get_device_name(0)
            profile.cuda_memory_mb = int(torch.cuda.get_device_properties(0).total_memory / 1024 / 1024)
    except Exception:  # noqa: BLE001
        profile.torch_available = False

    if not profile.cuda_available:
        profile.cuda_available, profile.cuda_device = _probe_cuda_without_torch(profile)

    profile.directml_available = "DmlExecutionProvider" in profile.onnx_providers
    profile.ctranslate2_available = _module_exists("ctranslate2")

    if profile.cuda_available:
        profile.recommended_device = "cuda"
        profile.recommended_compute_type = "float16"
    elif profile.directml_available:
        profile.recommended_device = "directml"
        profile.recommended_compute_type = "int8"
    else:
        profile.recommended_device = "cpu"
        profile.recommended_compute_type = "int8"

    if profile.total_memory_mb and profile.total_memory_mb < 8000:
        profile.notes.append(
            "This machine has less than 8 GB of RAM: large speech models may be slow "
            "or fail to load. Prefer 'small' or 'base' size models."
        )
    if not profile.cuda_available:
        profile.notes.append(
            "No NVIDIA GPU was detected. Inference will run on the CPU; "
            "'int8' quantisation is selected for the best speed."
        )
    return profile


def _probe_cuda_without_torch(profile: HardwareProfile) -> tuple[bool, str]:
    """Cheap CUDA probe through CTranslate2 (installed with faster-whisper)."""
    try:
        import ctranslate2  # noqa: PLC0415

        count = int(ctranslate2.get_cuda_device_count())
        if count > 0:
            return True, f"CUDA device {0}"
    except Exception:  # noqa: BLE001
        return False, ""
    return False, ""


def _fallback_memory_mb() -> int:
    try:
        import os
        import sys

        if sys.platform == "win32":  # pragma: no cover - Windows only
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return int(status.ullTotalPhys / 1024 / 1024)
    except Exception:  # noqa: BLE001
        pass
    return 4096


def _module_exists(name: str) -> bool:
    from importlib.util import find_spec

    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


@dataclass
class LoadedEngine:
    engine: BaseEngine
    model: InstalledModel | None
    loaded_at: float
    bytes_estimate: int = 0


class EngineManager:
    """Owns the live engine instances for TTS, STT and diacritization."""

    def __init__(self, registry: ModelRegistry, settings_provider: Callable[[], Any] | None = None) -> None:
        self.registry = registry
        self.settings_provider = settings_provider
        self.hardware = detect_hardware()
        self._tts: dict[str, TTSEngine] = {}
        self._stt: dict[str, STTEngine] = {}
        self._loaded: list[LoadedEngine] = []
        self._lock = threading.RLock()
        self._last_cleanup = 0.0
        self._unload_minutes = 0.0

    # -- settings -----------------------------------------------------------
    def _settings(self) -> Any:
        if self.settings_provider is None:
            return None
        try:
            return self.settings_provider()
        except Exception:  # pragma: no cover
            return None

    def unload_idle(self, force: bool = False) -> list[str]:
        """Unload engines idle for longer than the configured timeout."""
        settings = self._settings()
        timeout = getattr(getattr(settings, "ai", None), "unload_after_idle_s", 300) if settings else 300
        if timeout == 0 and not force:
            return []
        now = time.time()
        if not force and now - self._last_cleanup < 15:
            return []
        self._last_cleanup = now
        unloaded: list[str] = []
        with self._lock:
            keep: list[LoadedEngine] = []
            for item in self._loaded:
                idle = now - item.engine._last_used if item.engine._last_used else now - item.loaded_at
                if force or idle >= timeout:
                    item.engine.unload()
                    unloaded.append(item.engine.engine_id)
                else:
                    keep.append(item)
            self._loaded = keep
        if unloaded:
            log.info("unloaded idle engines", extra={"event": "engines_unloaded", "engines": ", ".join(unloaded)})
        return unloaded

    def register_loaded(self, engine: BaseEngine, model: InstalledModel | None = None, bytes_estimate: int = 0) -> None:
        with self._lock:
            self._loaded.append(
                LoadedEngine(engine=engine, model=model, loaded_at=time.time(), bytes_estimate=bytes_estimate)
            )

    def loaded_engines(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "engine": item.engine.engine_id,
                    "model": item.model.id if item.model else "",
                    "idle_s": round(time.time() - item.loaded_at, 1),
                    "bytes": item.bytes_estimate,
                }
                for item in self._loaded
            ]

    # -- TTS ----------------------------------------------------------------
    def tts_engine(self, engine_id: str = "piper") -> TTSEngine:
        from .tts.piper_engine import PiperTTSEngine  # noqa: PLC0415 - lazy import

        with self._lock:
            engine = self._tts.get(engine_id)
            if engine is None:
                if engine_id not in ("piper", "auto"):
                    raise EngineUnavailable(f"unknown text-to-speech engine: {engine_id}")
                engine = PiperTTSEngine(registry=self.registry)
                self._tts[engine_id] = engine
                self.register_loaded(engine, bytes_estimate=engine.memory_bytes if hasattr(engine, "memory_bytes") else 0)
            engine.touch()
            return engine

    # -- STT ----------------------------------------------------------------
    def stt_engine(self, engine_id: str = "faster-whisper") -> STTEngine:
        from .stt.faster_whisper_engine import FasterWhisperSTTEngine  # noqa: PLC0415

        with self._lock:
            engine = self._stt.get(engine_id)
            if engine is None:
                if engine_id not in ("faster-whisper", "faster_whisper", "auto"):
                    raise EngineUnavailable(f"unknown speech-recognition engine: {engine_id}")
                engine = FasterWhisperSTTEngine(registry=self.registry, hardware=self.hardware)
                self._stt[engine_id] = engine
                self.register_loaded(engine)
            engine.touch()
            return engine

    # -- reporting ----------------------------------------------------------
    def availability(self) -> dict[str, Any]:
        from .diacritization.canine_onnx import CanineOnnxDiacritizer  # noqa: PLC0415
        from .stt.faster_whisper_engine import FasterWhisperSTTEngine  # noqa: PLC0415
        from .tts.piper_engine import PiperTTSEngine  # noqa: PLC0415

        return {
            "tts": {
                "available": PiperTTSEngine.is_available(),
                "reason": PiperTTSEngine.unavailable_reason(),
                "voices": len(self.registry.by_category("tts_voice")),
            },
            "stt": {
                "available": FasterWhisperSTTEngine.is_available(),
                "reason": FasterWhisperSTTEngine.unavailable_reason(),
                "models": len(self.registry.by_category("stt")),
            },
            "diacritization": {
                "neural": _module_exists("onnxruntime") and bool(self.registry.by_category("diacritization")),
                "reason": ""
                if _module_exists("onnxruntime")
                else "ONNX Runtime is not installed (install the Persian diacritization engine pack).",
                "models": len(self.registry.by_category("diacritization")),
                "lexicon_fallback": True,
            },
            "hardware": self.hardware.as_dict(),
        }

    def shutdown(self) -> None:
        """Release every engine (called on application exit)."""
        self.unload_idle(force=True)
        with self._lock:
            for engine in list(self._tts.values()) + list(self._stt.values()):
                engine.unload()
            self._tts.clear()
            self._stt.clear()
        log.info("engine manager shut down", extra={"event": "engines_shutdown"})
