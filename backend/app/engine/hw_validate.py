"""Validate an uploaded device profile before it can enter an analysis.

Checks, in order: structure (an object with the required fields), units (numbers may be written
with units, e.g. "68 GB/s", "8 GiB", "15 W", "250 mW", "85 °C"; they are converted to the engine's
units and every conversion is reported), ranges (physically plausible values), and consistency
(throttle below shutdown, modes within the power envelope, the default cooling exists...).

Errors block the profile; warnings are shown and kept with the artifact. Pure function.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from app.schemas.hardware import HardwareProfile

# field -> (engine unit, accepted unit -> factor to engine unit)
UNITS: dict[str, tuple[str, dict[str, float]]] = {
    "mem_bw_gbs": ("GB/s", {"gb/s": 1, "gbps": 1, "gbyte/s": 1, "mb/s": 1e-3, "tb/s": 1e3, "gib/s": 1.073741824}),
    "mem_gb": ("GB", {"gb": 1, "gib": 1.073741824, "mb": 1e-3, "mib": 1.048576e-3, "tb": 1e3, "kb": 1e-6}),
    "weights_mem_gb": ("GB", {"gb": 1, "mb": 1e-3, "kb": 1e-6, "mib": 1.048576e-3, "kib": 1.024e-6}),
    "onchip_weights_mb": ("MB", {"mb": 1, "kb": 1e-3, "mib": 1.048576, "kib": 1.024e-3, "gb": 1e3}),
    "onchip_bw_gbs": ("GB/s", {"gb/s": 1, "tb/s": 1e3, "mb/s": 1e-3}),
    "idle_w": ("W", {"w": 1, "mw": 1e-3, "uw": 1e-6, "µw": 1e-6}),
    "dyn_w_max": ("W", {"w": 1, "mw": 1e-3, "uw": 1e-6, "µw": 1e-6}),
    "mem_w_max": ("W", {"w": 1, "mw": 1e-3, "uw": 1e-6, "µw": 1e-6}),
    "leak_w_25c": ("W", {"w": 1, "mw": 1e-3, "uw": 1e-6, "µw": 1e-6}),
    "t_throttle_c": ("°C", {"c": 1, "°c": 1, "degc": 1}),
    "t_shutdown_c": ("°C", {"c": 1, "°c": 1, "degc": 1}),
}
# Plausible ranges in engine units (beyond them: an error, usually a unit mistake).
RANGES: dict[str, tuple[float, float]] = {
    "mem_bw_gbs": (1e-4, 2e4), "mem_gb": (1e-6, 4096), "idle_w": (0, 2000), "dyn_w_max": (0, 3000),
    "mem_w_max": (0, 1000), "leak_w_25c": (0, 500), "t_throttle_c": (40, 130), "t_shutdown_c": (45, 150),
    "weights_mem_gb": (1e-7, 4096), "onchip_weights_mb": (1e-3, 1e5), "onchip_bw_gbs": (1e-3, 1e5),
}  # fmt: skip
REQUIRED = ("id", "name", "vendor", "kind", "peak_tflops", "mem_bw_gbs", "mem_gb", "idle_w",
            "dyn_w_max", "mem_w_max", "leak_w_25c", "t_throttle_c", "t_shutdown_c", "power_modes",
            "cooling_options", "default_cooling")  # fmt: skip
_NUM = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?)\s*([^\d\s].*)?$", re.I)


def parse_quantity(value: Any, field: str) -> tuple[float, str | None]:
    """(value in engine units, note) ; raises ValueError with a readable message."""
    if isinstance(value, bool):
        raise ValueError(f"{field}: expected a number, got {value!r}")
    if isinstance(value, (int, float)):
        return float(value), None
    if not isinstance(value, str):
        raise ValueError(f"{field}: expected a number, got {type(value).__name__}")
    m = _NUM.match(value.replace(",", ""))
    if not m:
        raise ValueError(f"{field}: '{value}' is not a number with an optional unit")
    num, unit = float(m.group(1)), (m.group(2) or "").strip().lower().replace(" ", "")
    if not unit:
        return num, None
    target, table = UNITS.get(field, ("", {}))
    if unit not in table:
        allowed = ", ".join(sorted(table)) or "none (plain number)"
        raise ValueError(f"{field}: unit '{m.group(2).strip()}' not understood (use {allowed})")
    out = num * table[unit]
    return out, (None if table[unit] == 1 else f"{field}: {value} converted to {out:g} {target}")


def _convert(doc: dict[str, Any], errors: list[str], notes: list[str]) -> dict[str, Any]:
    out = dict(doc)
    for field in UNITS:
        if field in out and out[field] is not None:
            try:
                out[field], note = parse_quantity(out[field], field)
                if note:
                    notes.append(note)
            except ValueError as e:
                errors.append(str(e))
    if isinstance(out.get("peak_tflops"), dict):
        peaks = {}
        for prec, v in out["peak_tflops"].items():
            try:
                if isinstance(v, str):
                    m = _NUM.match(v)
                    if not m:
                        raise ValueError(f"peak_tflops.{prec}: '{v}' is not a number")
                    unit = (m.group(2) or "").strip().lower()
                    scale = {"": 1, "tflops": 1, "tops": 1, "gflops": 1e-3, "gops": 1e-3, "pflops": 1e3}.get(unit)
                    if scale is None:
                        raise ValueError(f"peak_tflops.{prec}: unit '{unit}' not understood (TFLOPS, TOPS, GFLOPS, GOPS)")
                    peaks[prec] = float(m.group(1)) * scale
                else:
                    peaks[prec] = float(v)
            except (ValueError, TypeError) as e:
                errors.append(str(e))
        out["peak_tflops"] = peaks
    return out


def validate_profile(doc: Any) -> dict[str, Any]:
    """{ok, errors, warnings, notes, profile (normalised, when ok)}."""
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    if not isinstance(doc, dict):
        return {"ok": False, "errors": ["Expected an object (a mapping of field: value) at the top level."],
                "warnings": [], "notes": [], "profile": None}  # fmt: skip
    missing = [f for f in REQUIRED if f not in doc]
    if missing:
        errors.append("Missing required field(s): " + ", ".join(missing))
    d = _convert(doc, errors, notes)
    for f, (lo, hi) in RANGES.items():
        v = d.get(f)
        if isinstance(v, (int, float)) and not lo <= v <= hi:
            errors.append(f"{f} = {v:g} {UNITS.get(f, ('',))[0]} is outside the plausible range "
                          f"{lo:g}..{hi:g}; check the unit.")  # fmt: skip
    peaks = d.get("peak_tflops") or {}
    if isinstance(peaks, dict):
        bad = [p for p in peaks if p not in ("fp32", "fp16", "int8")]
        if bad:
            errors.append(f"peak_tflops: unknown precision(s) {', '.join(bad)} (use fp32, fp16, int8)")
        if not any(isinstance(v, (int, float)) and v > 0 for v in peaks.values()):
            errors.append("peak_tflops: give a positive peak for at least one precision")
        if isinstance(peaks.get("fp16"), (int, float)) and isinstance(peaks.get("fp32"), (int, float)) \
                and peaks["fp32"] > 0 and peaks["fp16"] < peaks["fp32"] * 0.5:  # fmt: skip
            warnings.append("fp16 peak is less than half of fp32: unusual (check the numbers).")
    t_thr, t_sd = d.get("t_throttle_c"), d.get("t_shutdown_c")
    if isinstance(t_thr, (int, float)) and isinstance(t_sd, (int, float)) and t_thr >= t_sd:
        errors.append(f"t_throttle_c ({t_thr:g} °C) must be below t_shutdown_c ({t_sd:g} °C).")
    modes = d.get("power_modes")
    if isinstance(modes, list):
        if not modes:
            errors.append("power_modes: give at least one power mode")
        ids = [m.get("id") for m in modes if isinstance(m, dict)]
        if len(ids) != len(set(ids)):
            errors.append("power_modes: ids must be unique")
        idle = d.get("idle_w")
        for m in modes:
            if isinstance(m, dict) and isinstance(idle, (int, float)) and isinstance(m.get("cap_w"), (int, float)) \
                    and m["cap_w"] <= idle:  # fmt: skip
                errors.append(f"power mode '{m.get('id')}': cap {m['cap_w']} W is not above idle power {idle:g} W")
        caps: list[float] = [float(m["cap_w"]) for m in modes
                             if isinstance(m, dict) and isinstance(m.get("cap_w"), (int, float))]
        total = sum(d.get(k, 0) or 0 for k in ("idle_w", "dyn_w_max", "mem_w_max", "leak_w_25c")
                    if isinstance(d.get(k), (int, float)))  # fmt: skip
        if caps and total > 2.5 * max(caps):
            warnings.append(f"idle + dynamic + memory + leakage power ({total:g} W) is far above the highest "
                            f"power-mode cap ({max(caps):g} W); the cap will dominate.")  # fmt: skip
    cools = d.get("cooling_options")
    if isinstance(cools, list):
        if not cools:
            errors.append("cooling_options: give at least one cooling option")
        cids = [c.get("id") for c in cools if isinstance(c, dict)]
        if d.get("default_cooling") not in cids:
            errors.append(f"default_cooling '{d.get('default_cooling')}' is not one of the cooling options {cids}")
        for c in cools:
            r = c.get("r_th_k_per_w") if isinstance(c, dict) else None
            if isinstance(r, (int, float)) and not 0.005 <= r <= 500:
                errors.append(f"cooling '{c.get('id')}': r_th_k_per_w {r} K/W is implausible (0.005..500)")
    if not d.get("sources"):
        warnings.append("No sources given: every value will be labelled 'assumed' until you add where it "
                        "came from (sources: {compute: vendor_spec, ...}).")  # fmt: skip
    profile = None
    if not errors:
        try:
            profile = HardwareProfile.model_validate(d).model_dump(mode="json")
        except ValidationError as e:
            for x in e.errors()[:8]:
                errors.append(f"{'.'.join(str(p) for p in x['loc'])}: {x['msg']}")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "notes": notes, "profile": profile}


def template() -> dict[str, Any]:
    """A complete, valid example profile to start from (a small edge SoC)."""
    return {
        "id": "my-board", "name": "My board", "vendor": "Me", "kind": "edge_soc",
        "role": "Prototype edge computer",
        "peak_tflops": {"fp32": "1.2 TFLOPS", "fp16": "2.4 TFLOPS", "int8": "9.6 TOPS"},
        "mem_bw_gbs": "34 GB/s", "mem_gb": "4 GB",
        "idle_w": "2.5 W", "dyn_w_max": "9 W", "mem_w_max": "2 W", "leak_w_25c": "0.6 W",
        "t_throttle_c": "85 °C", "t_shutdown_c": "100 °C",
        "power_modes": [
            {"id": "max", "name": "Maximum", "cap_w": 15, "freq_scale": 1.0, "mem_scale": 1.0},
            {"id": "eco", "name": "Eco", "cap_w": 7, "freq_scale": 0.6, "mem_scale": 0.8},
        ],
        "cooling_options": [
            {"id": "passive", "name": "Passive heatsink", "r_th_k_per_w": 4.5, "fan_w": 0, "c_th_j_per_k": 60},
            {"id": "fan", "name": "Heatsink with fan", "r_th_k_per_w": 2.0, "fan_w": 0.8, "c_th_j_per_k": 80},
        ],
        "default_cooling": "passive",
        "sources": {"compute": "vendor_spec", "memory": "vendor_spec", "power": "assumed", "thermal": "assumed"},
        "notes": "Replace every value with your board's datasheet figures.",
    }
