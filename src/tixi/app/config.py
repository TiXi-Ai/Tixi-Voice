"""Application settings schema, defaults and validation.

The settings model is deliberately dependency-free (no Qt, no SQLite) so it can
be unit-tested, diffed and exported.  Persistence is handled by
:mod:`tixi.storage.settings_repository`.

Layering
--------
``dataclass`` section  ->  :class:`Settings`  ->  :class:`SettingsStore`
   (typed values)          (all sections)        (load/save/validate/notify)
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, get_args

APP_VERSION = "1.0.0"
APP_BUILD = "2026.09"
CONFIG_VERSION = 3

GITHUB_OWNER = "TiXi-Ai"
GITHUB_REPO = "Tixi-Voice"
GITHUB_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"

ThemeMode = Literal["system", "light", "dark"]
AccentName = Literal[
    "ocean_blue",
    "midnight_purple",
    "emerald_green",
    "sunset_orange",
    "rose_pink",
    "arctic_cyan",
    "graphite",
    "custom",
]

THEME_MODES: tuple[str, ...] = get_args(ThemeMode)
ACCENTS: tuple[str, ...] = get_args(AccentName)

#: BCP-47-ish codes offered in the language pickers.  ``auto`` is only valid
#: where the engine can detect the language (Whisper).
STT_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("auto", "Detect automatically"),
    ("fa", "Persian (فارسی)"),
    ("en", "English"),
    ("ar", "Arabic (العربية)"),
    ("tr", "Turkish (Türkçe)"),
    ("de", "German (Deutsch)"),
    ("fr", "French (Français)"),
    ("es", "Spanish (Español)"),
    ("ru", "Russian (Русский)"),
    ("zh", "Chinese (中文)"),
    ("ja", "Japanese (日本語)"),
    ("hi", "Hindi (हिन्दी)"),
    ("ku", "Kurdish (کوردی)"),
    ("ur", "Urdu (اردو)"),
    ("it", "Italian (Italiano)"),
    ("pt", "Portuguese (Português)"),
    ("nl", "Dutch (Nederlands)"),
    ("pl", "Polish (Polski)"),
    ("uk", "Ukrainian (Українська)"),
)

AUDIO_FORMATS: tuple[tuple[str, str, bool], ...] = (
    # (id, label, needs_conversion_backend)
    ("wav", "WAV (uncompressed, always available)", False),
    ("flac", "FLAC (lossless, native)", False),
    ("mp3", "MP3 (native encoder)", False),
    ("ogg", "OGG Vorbis (native)", False),
    ("opus", "Opus (requires FFmpeg or the Speech Recognition engine pack)", True),
    ("m4a", "M4A / AAC (requires FFmpeg)", True),
)

TRANSCRIPTION_FORMATS: tuple[str, ...] = ("txt", "srt", "vtt", "json", "csv")


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
@dataclass
class GeneralSettings:
    ui_language: str = "en"                     # "en" | "fa"
    startup_behavior: str = "dashboard"          # dashboard | tts | stt | voice_typing | tray
    minimize_to_tray: bool = True
    close_to_tray: bool = True
    start_with_windows: bool = False
    start_minimised: bool = False
    export_dir: str = ""
    model_dir: str = ""
    confirm_on_exit: bool = False
    restore_last_view: bool = True
    last_view: str = "dashboard"
    autosave_text: bool = True
    recent_limit: int = 8


@dataclass
class AppearanceSettings:
    theme_mode: ThemeMode = "system"
    accent: AccentName = "ocean_blue"
    custom_accent: str = "#0A84FF"
    animations: bool = True
    animation_speed: float = 1.0               # 0.5 .. 2.0
    ui_scale: float = 1.0                      # 0.85 .. 1.5
    reduce_transparency: bool = False
    window_blur: bool = True
    corner_radius: int = 14
    density: str = "comfortable"                # comfortable | compact
    persian_font: str = ""                      # auto-detected when empty
    editor_font_size: int = 13
    editor_line_spacing: float = 1.6


@dataclass
class AudioSettings:
    input_device: str = ""                      # "" = system default
    output_device: str = ""
    input_sample_rate: int = 48000
    input_channels: int = 1
    mic_gain: float = 1.0                       # 0.1 .. 4.0
    monitor_volume: float = 1.0
    record_format: str = "wav"
    noise_reduction: str = "off"                # off | gate | spectral
    noise_gate_db: float = -45.0
    file_import_preprocess: bool = True


@dataclass
class TTSSettings:
    model_id: str = "piper-fa-amir"
    voice_id: str = ""
    language: str = "fa"
    speed: float = 1.0                          # 0.5 .. 2.0
    pitch: float = 1.0                          # 0.5 .. 1.5 (engine dependent)
    volume: float = 1.0
    sentence_pause_ms: int = 180
    paragraph_pause_ms: int = 400
    chunk_chars: int = 900
    output_format: str = "wav"
    sample_rate: int = 22050
    bit_depth: int = 16
    channels: int = 1
    normalise_persian: bool = True
    diacritize: bool = False
    diacritization_mode: str = "smart"          # smart | full
    apply_pronunciation_dict: bool = True
    auto_play_after_generate: bool = False
    preview_before_export: bool = True
    export_dir: str = ""


@dataclass
class STTSettings:
    model_id: str = "whisper-small"
    language: str = "fa"
    auto_detect_language: bool = False
    task: str = "transcribe"                    # transcribe | translate
    beam_size: int = 5
    best_of: int = 5
    temperature: float = 0.0
    vad_filter: bool = True
    vad_min_silence_ms: int = 500
    vad_threshold: float = 0.5
    word_timestamps: bool = False
    condition_on_previous_text: bool = True
    initial_prompt: str = ""
    no_speech_threshold: float = 0.6
    diarization: bool = False
    compute_type: str = "auto"                  # auto | int8 | int8_float16 | float16 | float32
    preprocess_resample: bool = True
    preprocess_normalise: bool = True
    preprocess_denoise: bool = False
    export_format: str = "txt"
    max_segment_chars: int = 42                 # subtitle line wrapping
    punctuation_from_model: bool = True


@dataclass
class VoiceTypingSettings:
    enabled: bool = True
    shortcut: str = "Ctrl+Shift+Space"
    mode: str = "push_to_talk"                  # push_to_talk | toggle | push_to_talk_punctuation | continuous
    language: str = "fa"
    model_id: str = ""                          # "" = use the default STT model
    auto_punctuation: bool = True
    auto_capitalization: bool = True
    normalize_persian: bool = True
    diacritize: bool = False
    insert_method: str = "auto"                 # auto | clipboard | unicode | type
    restore_clipboard: bool = True
    paste_delay_ms: int = 90
    append_space: bool = True
    submit_key: str = "none"                    # none | enter | ctrl+enter
    min_duration_ms: int = 250
    max_duration_s: int = 120
    overlay_enabled: bool = True
    overlay_position: str = "bottom_center"
    overlay_opacity: float = 0.94
    sound_feedback: bool = True
    show_notifications: bool = True
    blocked_apps: list[str] = field(default_factory=list)
    post_process: list[str] = field(default_factory=list)
    keep_trailing_newline: bool = False


@dataclass
class AISettings:
    device: str = "auto"                        # auto | cpu | cuda | directml
    cpu_threads: int = 0                        # 0 = auto
    gpu_device_index: int = 0
    compute_type: str = "auto"
    unload_after_idle_s: int = 300              # 0 = never
    max_loaded_models: int = 2
    prefer_quantized: bool = True
    allow_cpu_fallback: bool = True
    onnx_threads: int = 0
    log_model_loading: bool = True


@dataclass
class PrivacySettings:
    history_enabled: bool = True
    retention_days: int = 0                     # 0 = keep forever
    auto_delete_audio_days: int = 0
    allow_network: bool = True                  # model downloads / update checks only
    telemetry: bool = False                      # never sent; kept for transparency
    log_content: bool = False                    # do not log recognised text by default
    offline_mode: bool = False                   # hard-disable every network call
    anonymise_paths_in_logs: bool = True


@dataclass
class UpdateSettings:
    auto_check: bool = True
    check_interval_hours: int = 24
    channel: str = "stable"                     # stable | beta
    notify: bool = True
    auto_download: bool = False
    verify_signature: bool = True
    last_check: str = ""
    skipped_version: str = ""
    last_seen_version: str = ""


@dataclass
class WindowSettings:
    geometry: str = ""
    state: str = ""
    splitter_sizes: dict[str, list[int]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------
@dataclass
class Settings:
    version: int = CONFIG_VERSION
    general: GeneralSettings = field(default_factory=GeneralSettings)
    appearance: AppearanceSettings = field(default_factory=AppearanceSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    tts: TTSSettings = field(default_factory=TTSSettings)
    stt: STTSettings = field(default_factory=STTSettings)
    voice_typing: VoiceTypingSettings = field(default_factory=VoiceTypingSettings)
    ai: AISettings = field(default_factory=AISettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)
    updates: UpdateSettings = field(default_factory=UpdateSettings)
    window: WindowSettings = field(default_factory=WindowSettings)

    # -- (de)serialisation ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> Settings:
        """Build settings from a (possibly partial / outdated) mapping."""
        settings = cls()
        if not isinstance(payload, dict):
            return settings
        for section_field in fields(cls):
            if section_field.name == "version":
                continue
            section_type = section_field.type
            raw = payload.get(section_field.name)
            current = getattr(settings, section_field.name)
            if not is_dataclass(current):
                continue
            merged = _merge_dataclass(current, raw)
            setattr(settings, section_field.name, merged)
        try:
            settings.version = int(payload.get("version", CONFIG_VERSION))
        except (TypeError, ValueError):
            settings.version = CONFIG_VERSION
        settings.migrate()
        return settings

    def copy(self) -> Settings:
        return Settings.from_dict(self.to_dict())

    # -- mutation helpers ----------------------------------------------------
    def section(self, name: str) -> Any:
        if not hasattr(self, name):
            raise KeyError(f"unknown settings section: {name}")
        return getattr(self, name)

    def get(self, dotted: str, default: Any = None) -> Any:
        section, _, key = dotted.partition(".")
        if not key:
            section, _, key = "", "", dotted
        target = self if not section else getattr(self, section, None)
        if target is None:
            return default
        return getattr(target, key, default)

    def set(self, dotted: str, value: Any) -> bool:
        """Set one value by dotted path; returns ``True`` when it changed."""
        parts = dotted.split(".")
        if len(parts) != 2:
            raise KeyError(f"expected 'section.key', got {dotted!r}")
        section, key = parts
        target = getattr(self, section, None)
        if target is None or not hasattr(target, key):
            raise KeyError(f"unknown setting: {dotted}")
        if getattr(target, key) == value:
            return False
        setattr(target, key, value)
        return True

    def update(self, values: dict[str, Any]) -> list[str]:
        """Apply ``{"section.key": value}`` pairs; return the changed keys."""
        changed: list[str] = []
        for dotted, value in values.items():
            try:
                if self.set(dotted, value):
                    changed.append(dotted)
            except KeyError:
                continue
        return changed

    def diff(self, other: Settings) -> dict[str, Any]:
        """Dotted-path diff against ``other`` (``self`` is the newer state)."""
        changes: dict[str, Any] = {}
        mine, theirs = self.to_dict(), other.to_dict()
        for section, values in mine.items():
            if section == "version":
                continue
            for key, value in (values or {}).items():
                if (theirs.get(section) or {}).get(key) != value:
                    changes[f"{section}.{key}"] = value
        return changes

    # -- migration & validation ---------------------------------------------
    def migrate(self) -> None:
        """Upgrade settings written by older builds in place."""
        if self.version >= CONFIG_VERSION:
            return
        # v1 -> v2: the voice-typing shortcut gained a canonical spelling.
        if self.version < 2:
            self.voice_typing.shortcut = normalise_shortcut(self.voice_typing.shortcut)
        # v2 -> v3: synthesis grew the chunk size + preview switches.
        if self.version < 3:
            self.tts.chunk_chars = max(300, min(4000, int(self.tts.chunk_chars or 900)))
        self.version = CONFIG_VERSION

    def validate(self) -> list[str]:
        """Clamp every value into a sane range.  Returns human-readable notes."""
        notes: list[str] = []

        def clamp(section: str, key: str, low: float, high: float, note: str) -> None:
            target = getattr(self, section)
            value = getattr(target, key)
            try:
                value = type(value)(min(max(value, low), high))
            except (TypeError, ValueError):
                value = getattr(type(target)(), key)
            if getattr(target, key) != value:
                setattr(target, key, value)
                notes.append(note)

        # Appearance
        if self.appearance.theme_mode not in THEME_MODES:
            self.appearance.theme_mode = "system"
            notes.append("theme mode reset to 'system'")
        if self.appearance.accent not in ACCENTS:
            self.appearance.accent = "ocean_blue"
            notes.append("accent reset to 'ocean_blue'")
        custom = str(self.appearance.custom_accent or "")
        if self.appearance.accent == "custom" and not is_hex_colour(custom):
            self.appearance.custom_accent = "#0A84FF"
            notes.append("custom accent colour was invalid and has been reset")
        clamp("appearance", "ui_scale", 0.8, 1.6, "UI scale clamped to 0.8-1.6")
        clamp("appearance", "animation_speed", 0.5, 2.0, "animation speed clamped")
        clamp("appearance", "corner_radius", 0, 24, "corner radius clamped")
        clamp("appearance", "editor_font_size", 9, 28, "editor font size clamped")
        clamp("appearance", "editor_line_spacing", 1.0, 2.6, "editor line spacing clamped")

        # Audio
        clamp("audio", "input_sample_rate", 8000, 192000, "input sample rate clamped")
        clamp("audio", "mic_gain", 0.05, 8.0, "microphone gain clamped")
        clamp("audio", "monitor_volume", 0.0, 1.0, "playback volume clamped")
        clamp("audio", "noise_gate_db", -90.0, -6.0, "noise gate level clamped")

        # TTS
        clamp("tts", "speed", 0.4, 2.5, "speech speed clamped")
        clamp("tts", "pitch", 0.5, 1.6, "pitch clamped")
        clamp("tts", "volume", 0.0, 1.5, "volume clamped")
        clamp("tts", "sentence_pause_ms", 0, 3000, "sentence pause clamped")
        clamp("tts", "paragraph_pause_ms", 0, 6000, "paragraph pause clamped")
        clamp("tts", "chunk_chars", 200, 4000, "chunk size clamped")
        clamp("tts", "channels", 1, 2, "channel count clamped")
        if self.tts.sample_rate not in (16000, 22050, 24000, 44100, 48000):
            self.tts.sample_rate = 22050
        if self.tts.bit_depth not in (16, 24, 32):
            self.tts.bit_depth = 16
        valid_formats = {fmt for fmt, _, _ in AUDIO_FORMATS}
        if self.tts.output_format not in valid_formats:
            self.tts.output_format = "wav"
        if self.tts.diacritization_mode not in ("smart", "full"):
            self.tts.diacritization_mode = "smart"

        # STT
        clamp("stt", "beam_size", 1, 10, "beam size clamped")
        clamp("stt", "temperature", 0.0, 1.0, "temperature clamped")
        clamp("stt", "vad_min_silence_ms", 100, 3000, "VAD silence window clamped")
        clamp("stt", "vad_threshold", 0.05, 0.95, "VAD threshold clamped")
        clamp("stt", "max_segment_chars", 20, 120, "subtitle line width clamped")
        if self.stt.export_format not in TRANSCRIPTION_FORMATS:
            self.stt.export_format = "txt"
        if self.stt.task not in ("transcribe", "translate"):
            self.stt.task = "transcribe"
        if self.stt.compute_type not in ("auto", "int8", "int8_float16", "float16", "float32"):
            self.stt.compute_type = "auto"

        # Voice typing
        mode = self.voice_typing.mode
        if mode not in ("push_to_talk", "toggle", "push_to_talk_punctuation", "continuous"):
            self.voice_typing.mode = "push_to_talk"
            notes.append("voice typing mode reset to push-to-talk")
        normalised = normalise_shortcut(self.voice_typing.shortcut)
        if normalised != self.voice_typing.shortcut:
            self.voice_typing.shortcut = normalised
        if self.voice_typing.insert_method not in ("auto", "clipboard", "unicode", "type"):
            self.voice_typing.insert_method = "auto"
        clamp("voice_typing", "min_duration_ms", 50, 5000, "minimum recording clamped")
        clamp("voice_typing", "max_duration_s", 5, 3600, "maximum recording clamped")
        clamp("voice_typing", "paste_delay_ms", 0, 1000, "paste delay clamped")
        clamp("voice_typing", "overlay_opacity", 0.4, 1.0, "overlay opacity clamped")

        # AI
        if self.ai.device not in ("auto", "cpu", "cuda", "directml"):
            self.ai.device = "auto"
        clamp("ai", "cpu_threads", 0, 256, "CPU thread count clamped")
        clamp("ai", "unload_after_idle_s", 0, 86400, "model idle timeout clamped")
        clamp("ai", "max_loaded_models", 1, 8, "loaded-model limit clamped")

        # Privacy / updates
        clamp("privacy", "retention_days", 0, 3650, "history retention clamped")
        clamp("privacy", "auto_delete_audio_days", 0, 3650, "audio retention clamped")
        clamp("updates", "check_interval_hours", 1, 720, "update interval clamped")
        if self.updates.channel not in ("stable", "beta"):
            self.updates.channel = "stable"

        # General
        clamp("general", "recent_limit", 2, 50, "recent item limit clamped")
        if self.general.ui_language not in ("en", "fa"):
            self.general.ui_language = "en"
        return notes


def _merge_dataclass(current: Any, raw: Any) -> Any:
    """Merge ``raw`` into a fresh copy of ``current``, ignoring bad keys/types."""
    fresh = copy.deepcopy(current)
    if not isinstance(raw, dict):
        return fresh
    for key, value in raw.items():
        if not hasattr(fresh, key):
            continue
        default_value = getattr(fresh, key)
        if isinstance(default_value, bool):
            setattr(fresh, key, bool(value))
        elif isinstance(default_value, int) and not isinstance(value, bool):
            try:
                setattr(fresh, key, int(value))
            except (TypeError, ValueError):
                pass
        elif isinstance(default_value, float) and not isinstance(value, bool):
            try:
                setattr(fresh, key, float(value))
            except (TypeError, ValueError):
                pass
        elif isinstance(default_value, list):
            if isinstance(value, list):
                setattr(fresh, key, list(value))
        elif isinstance(default_value, dict):
            if isinstance(value, dict):
                setattr(fresh, key, dict(value))
        elif isinstance(default_value, str):
            if isinstance(value, str):
                setattr(fresh, key, value)
            elif isinstance(value, (int, float)):
                setattr(fresh, key, str(value))
        else:  # pragma: no cover - no other field types exist today
            setattr(fresh, key, value)
    return fresh


# ---------------------------------------------------------------------------
# Shortcut / colour helpers
# ---------------------------------------------------------------------------
_MODIFIER_ALIASES = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "ctl": "Ctrl",
    "shift": "Shift",
    "alt": "Alt",
    "meta": "Win",
    "win": "Win",
    "super": "Win",
    "cmd": "Win",
    "option": "Alt",
}

_KEY_ALIASES = {
    "space": "Space",
    "spacebar": "Space",
    "esc": "Escape",
    "escape": "Escape",
    "return": "Enter",
    "enter": "Enter",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "grave": "`",
    "backquote": "`",
    "minus": "-",
    "equal": "=",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "semicolon": ";",
    "quote": "'",
    "backslash": "\\",
    "bracketleft": "[",
    "bracketright": "]",
}

_MODIFIER_ORDER = ("Ctrl", "Shift", "Alt", "Win")


def normalise_shortcut(value: str) -> str:
    """Canonicalise a shortcut such as ``ctrl+shift+space`` -> ``Ctrl+Shift+Space``.

    Raises :class:`ValueError` for values that cannot be represented.
    """
    raw = (value or "").replace(" ", "").replace("+", " ").strip()
    if not raw:
        raise ValueError("shortcut is empty")
    parts = [part for part in raw.split() if part]
    modifiers: list[str] = []
    keys: list[str] = []
    for part in parts:
        lowered = part.lower()
        if lowered in _MODIFIER_ALIASES:
            mod = _MODIFIER_ALIASES[lowered]
            if mod not in modifiers:
                modifiers.append(mod)
            continue
        if lowered.startswith("f") and lowered[1:].isdigit():
            keys.append(f"F{int(lowered[1:])}")
        elif lowered in _KEY_ALIASES:
            keys.append(_KEY_ALIASES[lowered])
        elif len(part) == 1:
            keys.append(part.upper())
        else:
            keys.append(part.capitalize())
    if len(keys) != 1:
        raise ValueError(f"a shortcut needs exactly one non-modifier key: {value!r}")
    ordered = [mod for mod in _MODIFIER_ORDER if mod in modifiers]
    return "+".join([*ordered, keys[0]])


def shortcut_parts(value: str) -> tuple[list[str], str]:
    """Split a canonical shortcut into ``(["Ctrl", "Shift"], "Space")``."""
    canonical = normalise_shortcut(value)
    parts = canonical.split("+")
    return parts[:-1], parts[-1]


def is_hex_colour(value: str) -> bool:
    text = (value or "").strip()
    if not text.startswith("#"):
        return False
    body = text[1:]
    if len(body) not in (3, 6, 8):
        return False
    try:
        int(body, 16)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
ChangeListener = Callable[[Settings, list[str]], None]


class SettingsStore:
    """Mutable settings holder with deferred change notification.

    Qt signals are attached by the UI layer through :meth:`subscribe` so this
    module stays importable without PySide6 (tests, CLI tools, scripts).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._listeners: list[ChangeListener] = []
        self._suppress = 0
        self._pending: set[str] = set()

    # -- access --------------------------------------------------------------
    @property
    def settings(self) -> Settings:
        return self._settings

    def __getattr__(self, item: str) -> Any:
        try:
            return getattr(self._settings, item)
        except AttributeError as exc:  # pragma: no cover
            raise AttributeError(item) from exc

    def get(self, dotted: str, default: Any = None) -> Any:
        return self._settings.get(dotted, default)

    def subscribe(self, listener: ChangeListener) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: ChangeListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    # -- mutation ------------------------------------------------------------
    def set(self, dotted: str, value: Any) -> bool:
        if not self._settings.set(dotted, value):
            return False
        self._pending.add(dotted.split(".")[0])
        self._flush()
        return True

    def update(self, values: dict[str, Any]) -> list[str]:
        changed = self._settings.update(values)
        self._pending.update(key.split(".")[0] for key in changed)
        self._flush()
        return changed

    def replace(self, settings: Settings) -> None:
        """Swap in a whole settings object (import / reset) and notify."""
        before = self._settings.to_dict()
        self._settings = settings
        self._settings.validate()
        after = self._settings.to_dict()
        sections = [key for key in after if before.get(key) != after[key]]
        self._pending.clear()
        if sections:
            self._notify(sections)

    def reset_section(self, section: str) -> bool:
        defaults = Settings()
        if not hasattr(defaults, section):
            return False
        setattr(self._settings, section, getattr(defaults, section))
        self._notify([section])
        return True

    def reset_all(self) -> None:
        self._settings = Settings()
        self._notify(sorted(self._settings.to_dict()))

    def validate(self) -> list[str]:
        notes = self._settings.validate()
        if notes:
            self._notify(sorted(self._settings.to_dict()))
        return notes

    @property
    def batch(self) -> _Batch:
        """``with store.batch:`` defers change notifications until the end."""
        return _Batch(self)

    def _flush(self) -> None:
        if not self._pending or self._suppress:
            return
        sections = sorted(self._pending)
        self._pending.clear()
        self._notify(sections)

    def _notify(self, sections: list[str]) -> None:
        for listener in list(self._listeners):
            try:
                listener(self._settings, sections)
            except Exception:  # pragma: no cover - a broken listener must not kill the app
                import logging

                logging.getLogger("tixi.settings").exception("settings listener failed")

    # -- persistence ---------------------------------------------------------
    def to_json(self, *, indent: int = 2) -> str:
        payload = {"version": self._settings.version, **self._settings.to_dict()}
        return json.dumps(payload, indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> SettingsStore:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("settings must be a JSON object")
        return cls(Settings.from_dict(payload))


class _Batch:
    def __init__(self, store: SettingsStore) -> None:
        self._store = store

    def __enter__(self) -> SettingsStore:
        self._store._suppress += 1
        return self._store

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._store._suppress = max(0, self._store._suppress - 1)
        self._store._flush()
        return False


def section_names() -> Iterable[str]:
    return (f.name for f in fields(Settings) if is_dataclass(getattr(Settings(), f.name)))


def default_export_dir() -> str:
    from .paths import paths

    return str(paths().exports)


def default_model_dir() -> str:
    from .paths import paths

    return str(paths().models)


def effective_export_dir(settings: Settings) -> Path:
    value = (settings.general.export_dir or "").strip()
    return Path(value).expanduser() if value else Path(default_export_dir())


def effective_tts_export_dir(settings: Settings) -> Path:
    value = (settings.tts.export_dir or "").strip()
    if value:
        return Path(value).expanduser()
    return effective_export_dir(settings)


def effective_model_dir(settings: Settings) -> Path:
    value = (settings.general.model_dir or "").strip()
    return Path(value).expanduser() if value else Path(default_model_dir())
