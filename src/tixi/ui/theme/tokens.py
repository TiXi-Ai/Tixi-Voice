"""Design tokens: the single source of truth for colours, spacing and radii.

Views never hard-code colours.  They ask the :class:`~tixi.ui.theme.manager.ThemeManager`
for the current :class:`ThemeTokens`, which is built from the appearance
settings (light/dark/system + accent + transparency + scaling + density), so
switching a theme instantly re-styles every widget — including the ones drawn
with QPainter, which read the same tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .accents import (
    Accent,
    alpha_hex,
    darken,
    lighten,
    mix,
    readable_foreground,
    resolve_accent,
    with_alpha,
)


@dataclass(frozen=True)
class GlassTokens:
    """Material values for frosted panels."""

    surface_alpha: float = 0.72
    panel_alpha: float = 0.55
    border_alpha: float = 0.35
    highlight_alpha: float = 0.08
    blur_enabled: bool = True


@dataclass(frozen=True)
class ThemeTokens:
    """Every value the UI needs to paint itself."""

    mode: str = "dark"                     # "light" | "dark" (resolved, never "system")
    accent_id: str = "ocean_blue"
    radius: int = 14
    radius_small: int = 9
    radius_large: int = 20
    spacing: int = 12
    spacing_small: int = 6
    spacing_large: int = 20
    scale: float = 1.0
    density: str = "comfortable"
    animations: bool = True
    animation_speed: float = 1.0
    reduce_transparency: bool = False

    background: str = "#0F1116"
    background_alt: str = "#151821"
    background_elevated: str = "#1B1F2A"
    sidebar: str = "#12141B"
    surface: str = "#1E2230"
    surface_alt: str = "#262B3A"
    surface_hover: str = "#2C3243"
    text: str = "#F2F4F8"
    text_muted: str = "#9AA3B2"
    text_faint: str = "#6C7486"
    text_inverted: str = "#0B0D12"
    border: str = "#2A3040"
    border_strong: str = "#3A4356"
    shadow: str = "#000000"
    success: str = "#34C759"
    warning: str = "#FFB020"
    danger: str = "#FF453A"
    info: str = "#5AC8FA"

    accent: Accent = field(default_factory=lambda: resolve_accent("ocean_blue"))
    glass: GlassTokens = field(default_factory=GlassTokens)

    # -- derived ------------------------------------------------------------
    @property
    def accent_primary(self) -> str:
        return self.accent.primary

    @property
    def accent_secondary(self) -> str:
        return self.accent.secondary or self.accent.primary

    @property
    def accent_hover(self) -> str:
        return lighten(self.accent.primary, 0.12) if self.mode == "dark" else darken(self.accent.primary, 0.08)

    @property
    def accent_pressed(self) -> str:
        return darken(self.accent.primary, 0.15) if self.mode == "dark" else darken(self.accent.primary, 0.2)

    @property
    def accent_text(self) -> str:
        """Text colour that stays legible on top of the accent colour."""
        return readable_foreground(self.accent.primary)

    @property
    def accent_soft(self) -> str:
        return with_alpha(self.accent.primary, 0.16 if self.mode == "dark" else 0.12)

    @property
    def accent_softer(self) -> str:
        return with_alpha(self.accent.primary, 0.09 if self.mode == "dark" else 0.07)

    @property
    def accent_border(self) -> str:
        return with_alpha(self.accent.primary, 0.45)

    @property
    def accent_focus(self) -> str:
        return with_alpha(self.accent.primary, 0.55)

    @property
    def is_dark(self) -> bool:
        return self.mode == "dark"

    @property
    def is_light(self) -> bool:
        return self.mode == "light"

    @property
    def selection(self) -> str:
        return with_alpha(self.accent.primary, 0.35)

    @property
    def overlay_background(self) -> str:
        return with_alpha(self.background, 0.93)

    def alpha(self, colour: str, value: float) -> str:
        return with_alpha(colour, value)

    def hex_alpha(self, colour: str, value: float) -> str:
        return alpha_hex(colour, value)

    def subtle(self, index: int = 1) -> str:
        """Layered "depth" surfaces used by cards and the sidebar."""
        steps = (
            self.background,
            self.background_alt,
            self.surface,
            self.surface_alt,
        )
        return steps[max(0, min(len(steps) - 1, index))]

    def value(self, name: str, default: str = "") -> str:
        return getattr(self, name, default)


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------
_DARK_BASE = dict(
    background="#0E1015",
    background_alt="#141822",
    background_elevated="#1A1F2B",
    sidebar="#11141C",
    surface="#1D2230",
    surface_alt="#252B3B",
    surface_hover="#2C3345",
    text="#F3F5F9",
    text_muted="#9BA4B5",
    text_faint="#6B7488",
    text_inverted="#0B0D12",
    border="#252B39",
    border_strong="#333B4D",
    shadow="#05060A",
)

_LIGHT_BASE = dict(
    background="#F4F6FA",
    background_alt="#EDF0F6",
    background_elevated="#FFFFFF",
    sidebar="#E9EDF4",
    surface="#FFFFFF",
    surface_alt="#F4F6FA",
    surface_hover="#E8ECF4",
    text="#16181D",
    text_muted="#5A6376",
    text_faint="#8A93A5",
    text_inverted="#FFFFFF",
    border="#D7DCE6",
    border_strong="#C2C9D6",
    shadow="#8A93A5",
)


def build_tokens(
    *,
    mode: str,
    accent_id: str = "ocean_blue",
    custom_accent: str = "",
    radius: int = 14,
    scale: float = 1.0,
    density: str = "comfortable",
    animations: bool = True,
    animation_speed: float = 1.0,
    reduce_transparency: bool = False,
) -> ThemeTokens:
    """Create the token set for one resolved theme mode."""
    resolved_mode = "light" if mode == "light" else "dark"
    palette = dict(_LIGHT_BASE if resolved_mode == "light" else _DARK_BASE)
    accent = resolve_accent(accent_id, custom_accent)

    # Keep the accent legible on the background it will be shown against.
    if resolved_mode == "light":
        primary = accent.primary
        # Slightly darken very light accents so text on buttons stays readable.
        from .accents import contrast_ratio

        if contrast_ratio(primary, "#FFFFFF") < 3.0:
            primary = darken(primary, 0.18)
        accent = Accent(accent.id, accent.name, primary, accent.secondary or primary, accent.description)

    spacing = 12 if density != "compact" else 9
    radius_scale = 0.85 if density == "compact" else 1.0
    glass = GlassTokens(
        surface_alpha=0.92 if reduce_transparency else (0.78 if resolved_mode == "dark" else 0.86),
        panel_alpha=0.86 if reduce_transparency else (0.62 if resolved_mode == "dark" else 0.72),
        border_alpha=0.30 if resolved_mode == "dark" else 0.55,
        highlight_alpha=0.06 if resolved_mode == "dark" else 0.5,
        blur_enabled=not reduce_transparency,
    )
    return ThemeTokens(
        mode=resolved_mode,
        accent_id=accent.id,
        radius=int(radius * radius_scale),
        radius_small=max(6, int(radius * 0.62 * radius_scale)),
        radius_large=int(radius * 1.45 * radius_scale),
        spacing=spacing,
        spacing_small=max(4, int(spacing * 0.5)),
        spacing_large=int(spacing * 1.7),
        scale=scale,
        density=density,
        animations=animations,
        animation_speed=animation_speed,
        reduce_transparency=reduce_transparency,
        glass=glass,
        accent=accent,
        **palette,
    )


def light_tokens(**kwargs) -> ThemeTokens:
    return build_tokens(mode="light", **kwargs)


def dark_tokens(**kwargs) -> ThemeTokens:
    return build_tokens(mode="dark", **kwargs)
