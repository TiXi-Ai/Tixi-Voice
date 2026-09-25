"""Accent palettes.

Each accent is defined once, as a base colour plus optional gradient partner.
Everything else (hover, pressed, subtle backgrounds, focus rings) is derived
programmatically so every theme stays internally consistent and accessible: the
foreground colour that sits on top of an accent is chosen from the accent's
relative luminance, never hard-coded.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...app.config import is_hex_colour


@dataclass(frozen=True)
class Accent:
    """One accent theme."""

    id: str
    name: str
    primary: str
    secondary: str = ""
    description: str = ""

    def gradient(self) -> tuple[str, str]:
        return (self.primary, self.secondary or self.primary)


ACCENTS: tuple[Accent, ...] = (
    Accent("ocean_blue", "Ocean Blue", "#0A84FF", "#4FC3F7", "Apple-style system blue"),
    Accent("midnight_purple", "Midnight Purple", "#7C5CFF", "#B388FF", "Deep purple with a violet gradient"),
    Accent("emerald_green", "Emerald Green", "#00A97F", "#4ADE80", "Calm, high-contrast green"),
    Accent("sunset_orange", "Sunset Orange", "#F2740B", "#FFB74D", "Warm orange for creative work"),
    Accent("rose_pink", "Rose Pink", "#E5487F", "#FF8FB1", "Soft rose, good for long sessions"),
    Accent("arctic_cyan", "Arctic Cyan", "#00B8D4", "#5CE1E6", "Cool cyan with high legibility"),
    Accent("graphite", "Graphite", "#5B6472", "#8A94A6", "Neutral, distraction-free grey"),
)

ACCENT_IDS: tuple[str, ...] = tuple(accent.id for accent in ACCENTS) + ("custom",)


def accent_by_id(accent_id: str) -> Accent:
    for accent in ACCENTS:
        if accent.id == accent_id:
            return accent
    return ACCENTS[0]


def custom_accent(colour: str) -> Accent:
    """Build an accent from a user-chosen hex colour."""
    if not is_hex_colour(colour):
        colour = ACCENTS[0].primary
    normalised = normalise_hex(colour)
    return Accent("custom", "Custom", normalised, lighten(normalised, 0.22), "Your own accent colour")


def resolve_accent(accent_id: str, custom_colour: str = "") -> Accent:
    if accent_id == "custom":
        return custom_accent(custom_colour)
    return accent_by_id(accent_id)


# ---------------------------------------------------------------------------
# Colour maths
# ---------------------------------------------------------------------------
def hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) == 8:
        text = text[:6]
    if len(text) != 6:
        return (10, 132, 255)
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    red, green, blue = (max(0, min(255, int(round(channel)))) for channel in rgb)
    return f"#{red:02X}{green:02X}{blue:02X}"


def normalise_hex(value: str) -> str:
    return rgb_to_hex(hex_to_rgb(value))


def lighten(colour: str, amount: float) -> str:
    red, green, blue = hex_to_rgb(colour)
    return rgb_to_hex(
        (
            red + (255 - red) * amount,
            green + (255 - green) * amount,
            blue + (255 - blue) * amount,
        )
    )


def darken(colour: str, amount: float) -> str:
    red, green, blue = hex_to_rgb(colour)
    return rgb_to_hex((red * (1 - amount), green * (1 - amount), blue * (1 - amount)))


def mix(colour_a: str, colour_b: str, ratio: float = 0.5) -> str:
    red_a, green_a, blue_a = hex_to_rgb(colour_a)
    red_b, green_b, blue_b = hex_to_rgb(colour_b)
    ratio = max(0.0, min(1.0, ratio))
    return rgb_to_hex(
        (
            red_a + (red_b - red_a) * ratio,
            green_a + (green_b - green_a) * ratio,
            blue_a + (blue_b - blue_a) * ratio,
        )
    )


def relative_luminance(colour: str) -> float:
    """WCAG relative luminance (0 = black, 1 = white)."""
    def channel(value: int) -> float:
        srgb = value / 255.0
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = hex_to_rgb(colour)
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(colour_a: str, colour_b: str) -> float:
    """WCAG contrast ratio between two colours (1..21)."""
    luminance_a = relative_luminance(colour_a)
    luminance_b = relative_luminance(colour_b)
    lighter, darker = max(luminance_a, luminance_b), min(luminance_a, luminance_b)
    return (lighter + 0.05) / (darker + 0.05)


def readable_foreground(background: str, *, light: str = "#FFFFFF", dark: str = "#101114") -> str:
    """Pick the text colour with the better contrast on ``background``."""
    return light if contrast_ratio(background, light) >= contrast_ratio(background, dark) else dark


def with_alpha(colour: str, alpha: float) -> str:
    """``rgba(...)`` string for QSS (Qt supports rgba in stylesheets)."""
    red, green, blue = hex_to_rgb(colour)
    return f"rgba({red}, {green}, {blue}, {max(0.0, min(1.0, alpha)):.3f})"


def alpha_hex(colour: str, alpha: float) -> str:
    """8-digit hex colour (``#RRGGBBAA``) for Qt's QColor parsing."""
    red, green, blue = hex_to_rgb(colour)
    value = max(0, min(255, int(round(alpha * 255))))
    return f"#{red:02X}{green:02X}{blue:02X}{value:02X}"


def is_dark_colour(colour: str) -> bool:
    return relative_luminance(colour) < 0.42


def ensure_contrast(colour: str, background: str, minimum: float = 4.5) -> str:
    """Nudge ``colour`` towards black/white until it is legible on ``background``."""
    candidate = colour
    if contrast_ratio(candidate, background) >= minimum:
        return candidate
    toward = "#FFFFFF" if is_dark_colour(background) else "#000000"
    for step in range(1, 11):
        candidate = mix(colour, toward, step / 10.0)
        if contrast_ratio(candidate, background) >= minimum:
            return candidate
    return toward
