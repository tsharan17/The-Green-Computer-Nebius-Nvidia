"""Figure palettes built from standard scientific colormaps.

Thermal fields use turbo (the rainbow-style map of commercial thermal and CFD tools, cold blue to
hot red); other heatmaps use viridis (perceptually uniform, readable in greyscale and by most
colour-blind readers); categorical series use seaborn's "deep" palette. Neutral ink and grid, so
the data carries the colour.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def _sample(cmap: str, n: int = 64) -> list[str]:
    from matplotlib import colormaps
    from matplotlib.colors import to_hex

    cm = colormaps[cmap]
    return [to_hex(cm(v)) for v in np.linspace(0, 1, n)]


def _lum(color: str) -> float:
    from matplotlib.colors import to_rgb

    r, g, b = to_rgb(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _on(colors: list[str], dark: str = "#111111", light: str = "#ffffff") -> list[str]:
    return [dark if _lum(c) > 0.5 else light for c in colors]


def _bars(n: int) -> list[str]:
    import seaborn as sns

    return [str(c) for c in sns.color_palette("flare_r", max(n, 1)).as_hex()][:n]


def _on_heat(dark_text: str, light_text: str, cmap_ramp: list[str]) -> Any:
    from matplotlib.colors import LinearSegmentedColormap, to_rgb

    cm = LinearSegmentedColormap.from_list("h", cmap_ramp)

    def pick(temp: np.ndarray, lay: dict[str, float]) -> str:
        n = temp.shape[0]
        cy, cx = int((lay["y"] + lay["h"] / 2) * (n - 1)), int((lay["x"] + lay["w"] / 2) * (n - 1))
        lo, hi = float(temp.min()), float(temp.max())
        r, g, b = to_rgb(cm((temp[cy, cx] - lo) / max(hi - lo, 1e-9)))
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return dark_text if lum > 0.45 else light_text

    return pick


_HEAT = _sample("turbo")
_SEQ = _sample("viridis")
_DEEP = ["#4c72b0", "#dd8452", "#55a868", "#c44e52", "#8172b3", "#937860", "#da8bc3", "#8c8c8c"]

PALETTES: dict[str, dict[str, Any]] = {
    "light": {
        "bg": "#ffffff", "ink": "#1a1a1a", "muted": "#555555", "line": "#c8c8c8", "grid": "#e6e6e6", "contour": "#ffffff",
        "series": _DEEP, "on_series": _on(_DEEP), "accent": "#c44e52",
        "heat_ramp": _HEAT, "seq_ramp": _SEQ, "bar_ramp": _bars, "on_heat": _on_heat("#111111", "#ffffff", _HEAT),
    },
    "dark": {
        "bg": "#121417", "ink": "#eeeeee", "muted": "#b0b0b0", "line": "#3a3d42", "grid": "#2a2d31", "contour": "#ffffff",
        "series": _DEEP, "on_series": _on(_DEEP), "accent": "#ff7f6e",
        "heat_ramp": _HEAT, "seq_ramp": _SEQ, "bar_ramp": _bars, "on_heat": _on_heat("#111111", "#ffffff", _HEAT),
    },
}  # fmt: skip
