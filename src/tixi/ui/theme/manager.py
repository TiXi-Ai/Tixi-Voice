"""Theme manager: resolves the appearance settings and applies them app-wide.

Responsibilities
----------------
* resolve the effective mode (``system`` follows Windows' apps theme),
* build the :class:`ThemeTokens` for that mode,
* install the generated stylesheet + a matching QPalette on the QApplication,
* keep custom-painted widgets in sync by emitting :attr:`ThemeManager.tokens_changed`,
* persist changes through a callback so the Settings page always matches disk.
"""

from __future__ import annotations

import contextlib
import sys
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from ...app.logging_config import get_logger
from ..theme.accents import ACCENT_IDS, resolve_accent
from ..theme.styles import build_stylesheet, register_tokens
from ..theme.tokens import ThemeTokens, build_tokens

log = get_logger("tixi.ui.theme")

THEME_MODES = ("system", "light", "dark")
DENSITIES = ("comfortable", "compact")

UI_FONTS = (
    "Segoe UI Variable Text",
    "Segoe UI",
    "Inter",
    "Noto Sans",
    "Vazirmatn",
    "Tahoma",
    "DejaVu Sans",
    "Sans Serif",
)

PERSIAN_FONTS = (
    "Vazirmatn",
    "Sahel",
    "Noto Naskh Arabic",
    "Iranian Sans",
    "Tahoma",
    "Segoe UI",
    "DejaVu Sans",
)

MONO_FONTS = (
    "Cascadia Mono",
    "Consolas",
    "JetBrains Mono",
    "Courier New",
    "DejaVu Sans Mono",
    "Monospace",
)


def system_is_dark() -> bool:
    """Best-effort detection of the OS colour scheme (never raises)."""
    if sys.platform == "win32":  # pragma: no cover - Windows only
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return int(value) == 0
        except Exception:  # noqa: BLE001
            pass
    app = QApplication.instance()
    if app is not None:
        try:
            scheme = app.styleHints().colorScheme()  # type: ignore[attr-defined]
            if scheme == Qt.ColorScheme.Dark:
                return True
            if scheme == Qt.ColorScheme.Light:
                return False
        except Exception:  # noqa: BLE001
            pass
        try:
            window = app.palette().color(QPalette.ColorRole.Window)
            return window.lightness() < 128
        except Exception:  # noqa: BLE001
            pass
    return True


class ThemeManager(QObject):
    """Single owner of the application's look."""

    tokens_changed = Signal(object)   # ThemeTokens
    accent_changed = Signal(str)      # accent id

    def __init__(
        self,
        app: QApplication,
        *,
        mode: str = "system",
        accent_id: str = "ocean_blue",
        custom_accent: str = "#0A84FF",
        radius: int = 14,
        density: str = "comfortable",
        scale: float = 1.0,
        animations: bool = True,
        reduce_transparency: bool = False,
        interface_font: str = "",
        persian_font: str = "",
        on_change: Callable[[dict[str, Any]], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._app = app
        self._mode = mode if mode in THEME_MODES else "system"
        self._accent_id = accent_id if accent_id in ACCENT_IDS else "ocean_blue"
        self._custom_accent = custom_accent
        self._radius = int(radius)
        self._density = density if density in DENSITIES else "comfortable"
        self._scale = float(scale or 1.0)
        self._animations = bool(animations)
        self._reduce_transparency = bool(reduce_transparency)
        self._interface_font = interface_font
        self._persian_font = persian_font
        self._on_change = on_change
        self._tokens = self._build_tokens()
        self._follow_system = True

    # -- properties ---------------------------------------------------------
    @property
    def tokens(self) -> ThemeTokens:
        return self._tokens

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def resolved_mode(self) -> str:
        return self._tokens.mode

    @property
    def accent_id(self) -> str:
        return self._accent_id

    @property
    def density(self) -> str:
        return self._density

    @property
    def radius(self) -> int:
        return self._radius

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def animations(self) -> bool:
        return self._animations

    @property
    def accent(self) -> Any:
        return self._tokens.accent

    def animation_duration(self, base_ms: int) -> int:
        if not self._animations:
            return 0
        return int(base_ms / max(0.25, self._scale))

    # -- construction -------------------------------------------------------
    def _build_tokens(self) -> ThemeTokens:
        mode = self._mode
        if mode == "system":
            mode = "dark" if system_is_dark() else "light"
        return build_tokens(
            mode=mode,
            accent_id=self._accent_id,
            custom_accent=self._custom_accent,
            radius=self._radius,
            scale=self._scale,
            density=self._density,
            animations=self._animations,
            reduce_transparency=self._reduce_transparency,
        )

    # -- application --------------------------------------------------------
    def apply(self, *, repaint: bool = True) -> ThemeTokens:
        """Rebuild and install the stylesheet/palette. Returns the new tokens."""
        self._tokens = self._build_tokens()
        tokens = self._tokens
        register_tokens(tokens.mode, tokens)
        self._app.setStyleSheet(build_stylesheet(tokens))
        self._apply_palette(tokens)
        self._apply_fonts(tokens)
        self.tokens_changed.emit(tokens)
        if repaint:
            for widget in self._app.topLevelWidgets():
                with contextlib.suppress(Exception):
                    widget.update()
        log.debug(
            "theme applied",
            extra={"event": "theme_applied", "mode": tokens.mode, "accent": tokens.accent_id},
        )
        return tokens

    def _apply_palette(self, tokens: ThemeTokens) -> None:
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(tokens.background))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(tokens.text))
        palette.setColor(QPalette.ColorRole.Base, QColor(tokens.background_elevated if tokens.is_dark else "#FFFFFF"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(tokens.surface_alt))
        palette.setColor(QPalette.ColorRole.Text, QColor(tokens.text))
        palette.setColor(QPalette.ColorRole.Button, QColor(tokens.surface_alt))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(tokens.text))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(tokens.accent_primary))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(tokens.accent_text))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(tokens.background_elevated))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(tokens.text))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(tokens.text_faint))
        palette.setColor(QPalette.ColorRole.Link, QColor(tokens.accent_primary))
        palette.setColor(QPalette.ColorRole.Mid, QColor(tokens.border_strong))
        palette.setColor(QPalette.ColorRole.Dark, QColor(tokens.shadow))
        disabled = QPalette.ColorGroup.Disabled
        palette.setColor(disabled, QPalette.ColorRole.Text, QColor(tokens.text_faint))
        palette.setColor(disabled, QPalette.ColorRole.ButtonText, QColor(tokens.text_faint))
        palette.setColor(disabled, QPalette.ColorRole.WindowText, QColor(tokens.text_faint))
        self._app.setPalette(palette)

    def _apply_fonts(self, tokens: ThemeTokens) -> None:
        family = self._interface_font or self._first_available(UI_FONTS)
        font = QFont(family)
        base = 10.5 if self._density == "comfortable" else 10.0
        font.setPointSizeF(round(base * max(0.75, min(2.0, self._scale)), 2))
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self._app.setFont(font)

    @staticmethod
    def _first_available(candidates: tuple[str, ...]) -> str:
        families = set(QFontDatabase.families())
        for candidate in candidates:
            if candidate in families:
                return candidate
        return candidates[-1]

    def persian_font_family(self) -> str:
        return self._persian_font or self._first_available(PERSIAN_FONTS)

    def mono_font_family(self) -> str:
        return self._first_available(MONO_FONTS)

    def editor_font(self, *, persian: bool = True, size_delta: float = 0.0) -> QFont:
        family = self.persian_font_family() if persian else self._first_available(UI_FONTS)
        font = QFont(family)
        base = 11.0 if self._density == "comfortable" else 10.5
        font.setPointSizeF(round((base + size_delta) * max(0.75, min(2.0, self._scale)), 2))
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font

    def code_font(self, *, size_delta: float = 0.0) -> QFont:
        font = QFont(self.mono_font_family())
        font.setPointSizeF(round((10.5 + size_delta) * max(0.75, min(2.0, self._scale)), 2))
        return font

    # -- mutations ----------------------------------------------------------
    def set_mode(self, mode: str, *, persist: bool = True) -> None:
        if mode not in THEME_MODES:
            raise ValueError(f"Unknown theme mode: {mode!r}")
        if mode == self._mode:
            return
        self._mode = mode
        self.apply()
        self._persist(persist, {"theme_mode": self._mode})

    def toggle_light_dark(self, *, persist: bool = True) -> None:
        self.set_mode("light" if self._tokens.is_dark else "dark", persist=persist)

    def set_accent(self, accent_id: str, *, custom: str = "", persist: bool = True) -> None:
        if accent_id not in ACCENT_IDS:
            raise ValueError(f"Unknown accent: {accent_id!r}")
        self._accent_id = accent_id
        if custom:
            self._custom_accent = custom
        self.apply()
        self.accent_changed.emit(self._accent_id)
        self._persist(persist, {"accent_theme": self._accent_id, "accent_custom_colour": self._custom_accent})

    def set_custom_accent(self, colour: str, *, persist: bool = True) -> None:
        self._accent_id = "custom"
        self._custom_accent = colour
        self.apply()
        self.accent_changed.emit("custom")
        self._persist(persist, {"accent_theme": "custom", "accent_custom_colour": colour})

    def set_radius(self, radius: int, *, persist: bool = True) -> None:
        radius = max(4, min(28, int(radius)))
        if radius == self._radius:
            return
        self._radius = radius
        self.apply()
        self._persist(persist, {"corner_radius": self._radius})

    def set_density(self, density: str, *, persist: bool = True) -> None:
        if density not in DENSITIES:
            return
        if density == self._density:
            return
        self._density = density
        self.apply()
        self._persist(persist, {"ui_density": self._density})

    def set_scale(self, scale: float, *, persist: bool = True) -> None:
        scale = max(0.8, min(1.6, float(scale)))
        if abs(scale - self._scale) < 0.001:
            return
        self._scale = scale
        self.apply()
        self._persist(persist, {"ui_scale": self._scale})

    def set_animations(self, enabled: bool, *, persist: bool = True) -> None:
        if bool(enabled) == self._animations:
            return
        self._animations = bool(enabled)
        self.apply()
        self._persist(persist, {"animations": self._animations})

    def set_reduce_transparency(self, enabled: bool, *, persist: bool = True) -> None:
        if bool(enabled) == self._reduce_transparency:
            return
        self._reduce_transparency = bool(enabled)
        self.apply()
        self._persist(persist, {"reduce_transparency": self._reduce_transparency})

    def set_fonts(self, *, interface_font: str = "", persian_font: str = "") -> None:
        self._interface_font = interface_font
        self._persian_font = persian_font
        self.apply()

    def refresh_system(self) -> None:
        """Re-resolve ``system`` mode (called when Windows changes its theme)."""
        if self._mode != "system":
            return
        before = self._tokens.mode
        self._tokens = self._build_tokens()
        if self._tokens.mode != before:
            self.apply()

    def _persist(self, persist: bool, values: dict[str, Any]) -> None:
        if persist and self._on_change is not None:
            with contextlib.suppress(Exception):
                self._on_change(values)

    # -- helpers ------------------------------------------------------------
    def apply_to_widget(self, widget: QWidget) -> None:
        """Style a single widget (used by standalone dialogs/overlays)."""
        widget.setStyleSheet(build_stylesheet(self._tokens))

    def preview_tokens(self, mode: str, accent_id: str, custom: str = "") -> ThemeTokens:
        return build_tokens(
            mode="light" if mode == "light" else "dark",
            accent_id=accent_id,
            custom_accent=custom,
            radius=self._radius,
            density=self._density,
            scale=self._scale,
        )

    def accent_colours(self) -> dict[str, str]:
        return {
            accent_id: resolve_accent(accent_id, self._custom_accent).primary
            for accent_id in ACCENT_IDS
        }
