"""Audio device enumeration and validation.

``sounddevice`` (PortAudio) is the only audio backend used by Tixi Voice: it
supports WASAPI/MME/DirectSound on Windows, gives us input *and* output with a
single small dependency, and exposes device hot-plug information.

Every function degrades gracefully when PortAudio is missing or when the
machine has no sound card at all, because "no microphone" must show an
actionable message rather than crash the application.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ..app.logging_config import get_logger

log = get_logger("tixi.audio.devices")


class AudioBackendUnavailable(RuntimeError):
    """Raised when the PortAudio backend cannot be loaded."""


@dataclass
class AudioDevice:
    """One input or output device."""

    index: int
    name: str
    host_api: str = ""
    max_input_channels: int = 0
    max_output_channels: int = 0
    default_sample_rate: float = 0.0
    default_low_input_latency: float = 0.0
    default_high_input_latency: float = 0.0
    is_default_input: bool = False
    is_default_output: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_input(self) -> bool:
        return self.max_input_channels > 0

    @property
    def is_output(self) -> bool:
        return self.max_output_channels > 0

    def label(self) -> str:
        marks = []
        if self.is_default_input:
            marks.append("default mic")
        if self.is_default_output:
            marks.append("default output")
        suffix = f" — {', '.join(marks)}" if marks else ""
        api = f"[{self.host_api}] " if self.host_api else ""
        return f"{api}{self.name}{suffix}"


@dataclass
class DeviceList:
    """Snapshot of the machine's audio devices."""

    inputs: list[AudioDevice] = field(default_factory=list)
    outputs: list[AudioDevice] = field(default_factory=list)
    default_input_index: int | None = None
    default_output_index: int | None = None
    error: str = ""

    @property
    def available(self) -> bool:
        return not self.error

    @property
    def has_input(self) -> bool:
        return bool(self.inputs)

    @property
    def has_output(self) -> bool:
        return bool(self.outputs)


def backend() -> Any:
    """Import and return the ``sounddevice`` module."""
    try:
        import sounddevice  # noqa: PLC0415 - optional at import time
    except Exception as exc:  # pragma: no cover - depends on the machine
        raise AudioBackendUnavailable(
            "The audio backend (PortAudio via the 'sounddevice' package) could not be loaded: "
            f"{exc}. Reinstall the application, or install 'sounddevice' with pip."
        ) from exc
    return sounddevice


def list_devices(refresh: bool = False) -> DeviceList:
    """Enumerate input/output devices, never raising."""
    result = DeviceList()
    try:
        sd = backend()
        if refresh:
            sd._terminate()
            sd._initialize()
        raw_devices = sd.query_devices()
        default_in, default_out = _default_indices(sd)
        host_apis = _host_api_names(sd)
        for index, raw in enumerate(raw_devices):
            device = AudioDevice(
                index=index,
                name=str(raw.get("name", f"Device {index}")).strip(),
                host_api=host_apis.get(int(raw.get("hostapi", -1)), ""),
                max_input_channels=int(raw.get("max_input_channels", 0) or 0),
                max_output_channels=int(raw.get("max_output_channels", 0) or 0),
                default_sample_rate=float(raw.get("default_samplerate", 0) or 0),
                default_low_input_latency=float(raw.get("default_low_input_latency", 0) or 0),
                default_high_input_latency=float(raw.get("default_high_input_latency", 0) or 0),
                is_default_input=(index == default_in),
                is_default_output=(index == default_out),
            )
            if device.is_input:
                result.inputs.append(device)
            if device.is_output:
                result.outputs.append(device)
        result.default_input_index = default_in
        result.default_output_index = default_out
    except AudioBackendUnavailable as exc:
        result.error = str(exc)
    except Exception as exc:  # pragma: no cover - driver level failures
        result.error = f"Audio devices could not be enumerated: {exc}"
        log.warning("device enumeration failed", exc_info=True)
    return result


def _default_indices(sd: Any) -> tuple[int | None, int | None]:
    try:
        default_in, default_out = sd.default.device
        return (
            int(default_in) if default_in is not None and int(default_in) >= 0 else None,
            int(default_out) if default_out is not None and int(default_out) >= 0 else None,
        )
    except Exception:  # pragma: no cover
        return None, None


def _host_api_names(sd: Any) -> dict[int, str]:
    names: dict[int, str] = {}
    try:
        for api in sd.query_hostapis():
            names[int(api["index"])] = str(api.get("name", "")).strip()
    except Exception:  # pragma: no cover
        pass
    return names


def resolve_input_device(identifier: str | int | None) -> int | None:
    """Map a stored device identifier to a PortAudio index.

    ``identifier`` may be an index, a device name (exact or substring), or
    ``""``/``None`` for the system default.  Names are preferred because
    Windows renumbers devices when USB headsets are plugged in.
    """
    if identifier in (None, "", "default", -1):
        return None
    devices = list_devices()
    if not devices.available:
        return None
    if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
        index = int(identifier)
        for device in devices.inputs:
            if device.index == index:
                return index
        return devices.default_input_index
    needle = str(identifier).strip().lower()
    for device in devices.inputs:
        if device.name.lower() == needle:
            return device.index
    for device in devices.inputs:
        if needle in device.name.lower():
            return device.index
    return devices.default_input_index


def resolve_output_device(identifier: str | int | None) -> int | None:
    if identifier in (None, "", "default", -1):
        return None
    devices = list_devices()
    if not devices.available:
        return None
    if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
        index = int(identifier)
        for device in devices.outputs:
            if device.index == index:
                return index
        return devices.default_output_index
    needle = str(identifier).strip().lower()
    for device in devices.outputs:
        if device.name.lower() == needle:
            return device.index
    for device in devices.outputs:
        if needle in device.name.lower():
            return device.index
    return devices.default_output_index


def describe_device(index: int | None) -> str:
    if index is None:
        return "System default"
    try:
        info = backend().query_devices(index)
        return str(info.get("name", index)).strip()
    except Exception:  # pragma: no cover
        return f"Device {index}"


def supported_sample_rates(index: int | None, *, channels: int = 1) -> list[int]:
    """Probe the usual sample rates (slow but only used in the settings dialog)."""
    candidates = (8000, 16000, 22050, 24000, 32000, 44100, 48000, 96000, 192000)
    supported: list[int] = []
    try:
        sd = backend()
    except AudioBackendUnavailable:
        return []
    for rate in candidates:
        try:
            sd.check_input_settings(
                device=index, channels=channels, samplerate=rate, dtype="float32"
            )
            supported.append(rate)
        except Exception:
            continue
    return supported


_DEVICE_LOCK = threading.Lock()
_DEVICE_CACHE: DeviceList | None = None


def cached_devices(refresh: bool = False) -> DeviceList:
    """Enumerate devices once and reuse the snapshot until asked to refresh."""
    global _DEVICE_CACHE
    with _DEVICE_LOCK:
        if _DEVICE_CACHE is None or refresh:
            _DEVICE_CACHE = list_devices(refresh=False)
        return _DEVICE_CACHE


def invalidate_device_cache() -> None:
    global _DEVICE_CACHE
    with _DEVICE_LOCK:
        _DEVICE_CACHE = None


def microphone_permission_hint() -> str:
    """Windows-specific guidance shown when recording fails."""
    return (
        "No microphone input was received. Check that:\n"
        "  • Settings ▸ Privacy & security ▸ Microphone is ON for desktop apps,\n"
        "  • Tixi Voice ▸ Settings ▸ Audio shows the microphone you expect,\n"
        "  • the microphone is not muted (hardware switch or Windows volume mixer),\n"
        "  • no other application has the device open in exclusive mode."
    )
