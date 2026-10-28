"""Numbers-must-come-from-tools validator.

The Green Agent may reason and explain, but a figure in its reply must be traceable to a tool
result from the same turn (or to something the user said). The check is deliberately mechanical:
extract every number in the reply, accept it if it matches a number in the tool results (allowing
the rounding a human would apply (to the figure's own precision, with no relative slack) and fraction<->percent / ms<->s scaling), and flag the rest.
Small whole numbers are tolerated only when no unit follows (so "three options" is fine but
"5 W" must be real).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

_NUM = re.compile(r"(?<![A-Za-z0-9_.\-])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?")
_UNIT_AFTER = re.compile(
    r"^\s?(?:%|°|×|x\b|W\b|mW|kW|ms\b|s\b|sec|min|h\b|hr|fps|FPS|GB|MB|mJ|J\b|kWh|kg|g\b|"
    r"mAP|top|TOPS|GFLOP|TFLOP|M\b|B\b|K\b|Hz|MHz|GHz)",
)
_LIST_MARKER = re.compile(r"[ \t]*(?:[-*][ \t]*)?(\d+)[.)]\s")
_SCALES = (1.0, 100.0, 0.01, 1000.0, 0.001)
_SMALL_INT_MAX = 12


@dataclass
class Figure:
    raw: str
    value: float
    decimals: int
    start: int
    end: int


@dataclass
class GuardrailResult:
    ok: bool
    unverified: list[str] = field(default_factory=list)
    text: str = ""  # the reply, with unverified figures redacted when not ok


def figures_in(text: str) -> list[Figure]:
    out: list[Figure] = []
    for m in _NUM.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        marker = _LIST_MARKER.match(text, line_start)
        if marker and marker.start(1) == m.start():
            continue  # "1. Option" style list markers are not data
        whole = m.group(1).replace(",", "")
        frac = m.group(2) or ""
        out.append(
            Figure(
                raw=m.group(0),
                value=float(whole + frac),
                decimals=len(frac) - 1 if frac else 0,
                start=m.start(),
                end=m.end(),
            )
        )
    return out


def numbers_from(obj: Any) -> list[float]:
    """Every number in a JSON-like structure, including those embedded in strings (e.g. "15W")."""
    found: list[float] = []

    def walk(o: Any) -> None:
        if isinstance(o, bool) or o is None:
            return
        if isinstance(o, (int, float)):
            if math.isfinite(o):
                found.append(abs(float(o)))
        elif isinstance(o, str):
            found.extend(f.value for f in figures_in(o))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(obj)
    return found


def _matches(fig: Figure, allowed: list[float]) -> bool:
    """True if the figure is a source number rounded to the figure's own precision.

    No relative tolerance: "99.5" must not pass just because 100 is in the results.
    """
    t = fig.value
    half_unit = 0.5 * 10 ** (-fig.decimals) + 1e-9
    return any(abs(t - r * s) <= half_unit for r in allowed for s in _SCALES)


def check(
    reply: str, tool_results: list[Any], user_texts: list[str] | None = None
) -> GuardrailResult:
    allowed = numbers_from(tool_results)
    for t in user_texts or []:
        allowed.extend(f.value for f in figures_in(t))
    bad: list[Figure] = []
    for fig in figures_in(reply):
        followed_by_unit = bool(_UNIT_AFTER.match(reply[fig.end : fig.end + 8]))
        if (
            fig.decimals == 0
            and fig.value <= _SMALL_INT_MAX
            and not followed_by_unit
            and "," not in fig.raw
        ):
            continue
        if not _matches(fig, allowed):
            bad.append(fig)
    if not bad:
        return GuardrailResult(True, [], reply)
    redacted = reply
    for fig in sorted(bad, key=lambda f: f.start, reverse=True):
        redacted = redacted[: fig.start] + "[unverified figure]" + redacted[fig.end :]
    return GuardrailResult(False, [f.raw for f in bad], redacted)
