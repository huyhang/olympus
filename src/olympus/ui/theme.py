"""Olympus's colours: its own theme, and the palette its Rich text draws from.

Widgets are styled with Textual's theme variables in CSS, so every theme in
the Ctrl+K palette applies to them. Rich text, such as the review board's rows
and details, cannot read CSS variables, so it takes a `Palette` resolved from
the active theme and is drawn again when the theme changes.

This is the only module that names colours.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from textual.app import App
from textual.color import Color
from textual.theme import Theme

# The default: the look Olympus had before it followed themes.
OLYMPUS = Theme(
    name="olympus",
    primary="#2f81f7",
    secondary="#56d4dd",
    accent="#d2a8ff",
    warning="#d29922",
    error="#f85149",
    success="#3fb950",
    foreground="#e6edf3",
    background="#0b1017",
    surface="#0e1621",
    panel="#111a24",
    dark=True,
    variables={
        "border-blurred": "#243447",
        "primary-muted": "#1d3044",
        "text-primary": "#8bd5ff",
        "text-secondary": "#56d4dd",
        "text-accent": "#d2a8ff",
        "text-success": "#3fb950",
        "text-warning": "#d29922",
        "text-error": "#f85149",
    },
)

# What each Palette field is for; style strings name these as words.
ROLES = frozenset(
    {
        "text",
        "muted",
        "faint",
        "info",
        "success",
        "warning",
        "error",
        "choice",
        "carried",
    }
)


@dataclass(frozen=True, slots=True)
class Palette:
    """Solid colours, for Rich, from one theme.

    `text` is for headings, `muted` for notes and labels, `faint` for what is
    set aside, `info` for work in progress, `choice` for what needs the user,
    and `carried` for what predates the current review.
    """

    text: str
    muted: str
    faint: str
    info: str
    success: str
    warning: str
    error: str
    choice: str
    carried: str

    @classmethod
    def from_variables(cls, variables: Mapping[str, str]) -> Palette:
        foreground = Color.parse(variables["foreground"])
        background = Color.parse(variables["background"])

        def solid(name: str) -> str:
            return Color.parse(variables[name]).hex6

        return cls(
            text=foreground.hex6,
            muted=foreground.blend(background, 0.4).hex6,
            faint=foreground.blend(background, 0.6).hex6,
            info=solid("text-primary"),
            success=solid("text-success"),
            warning=solid("text-warning"),
            error=solid("text-error"),
            choice=solid("text-accent"),
            carried=solid("text-secondary"),
        )

    def style(self, style: str) -> str:
        """A Rich style with role words, `"bold success"`, made concrete."""
        return " ".join(
            getattr(self, word) if word in ROLES else word for word in style.split()
        )


def palette_of(app: App[object]) -> Palette:
    """The palette of the app's current theme."""
    return Palette.from_variables(app.get_css_variables())


def theme_variables(theme: Theme) -> dict[str, str]:
    """A theme's CSS variables as an app resolves them, overrides included."""
    return {**theme.to_color_system().generate(), **theme.variables}


DEFAULT_PALETTE = Palette.from_variables(theme_variables(OLYMPUS))
