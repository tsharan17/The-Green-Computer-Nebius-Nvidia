"""Read numbers out of vendor tool reports so hardware engineers do not retype them.

Supported (tolerant text parsers, tested on samples written from each tool's documented layout;
they have NOT been run against a large corpus of real reports, so the UI shows what was found
and the user confirms it):
  vivado_power    Vivado `report_power`          -> static / dynamic / total watts, junction temp, θJA
  vivado_util     Vivado `report_utilization`    -> DSP, BRAM, URAM, LUT counts and availability
  stedgeai        ST Edge AI Core `analyze`      -> flash (weights), RAM (activations), MACC
  vela            Arm Vela summary               -> SRAM / flash used, NPU cycles
  power_csv       Joulescope / Otii / PPK2 / generic CSV of time + current or power
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any


def _f(s: str) -> float:
    return float(s.replace(",", "").strip())


def _unit_bytes(val: float, unit: str) -> float:
    u = unit.lower()
    return val * {"b": 1, "kb": 1000, "kib": 1024, "mb": 1e6, "mib": 1048576}.get(u, 1)


def detect(text: str) -> str | None:
    t = text.lower()
    if "total on-chip power" in t or "device static" in t:
        return "vivado_power"
    if "slice luts" in t or "| dsps" in t or "dsp48" in t or "block ram tile" in t:
        return "vivado_util"
    if "stedgeai" in t or "st edge ai" in t or "macc" in t and "activations" in t:
        return "stedgeai"
    if "vela" in t or "npu cycles" in t or "total sram used" in t:
        return "vela"
    return None


def parse_vivado_power(text: str) -> dict[str, Any]:
    def row(label: str) -> float | None:
        m = re.search(rf"\|\s*{label}[^|]*\|\s*([\d.]+)", text, re.I)
        return float(m.group(1)) if m else None

    total = row(r"Total On-Chip Power \(W\)")
    dyn = row(r"Dynamic \(W\)")
    static = row(r"Device Static \(W\)")
    tj = row(r"Junction Temperature \(C\)")
    theta = row(r"Effective\s+\S*JA\s*\(C/W\)")
    fields: dict[str, Any] = {}
    if dyn is not None:
        fields["dynamic_w"] = dyn
    if static is not None:
        fields["static_w"] = static
    if theta is not None:
        fields["theta_ja_k_per_w"] = theta
    found = {k: v for k, v in {"total_w": total, "junction_c": tj}.items() if v is not None}
    if not fields:
        raise ValueError("no power rows found (expected Vivado report_power tables)")
    return {"tool": "vivado_power", "fields": fields, "found": found,
            "note": "Vivado power is an estimate at the activity you gave it; use post-implementation "
                    "with a SAIF/VCD for a trustworthy number."}


def parse_vivado_util(text: str) -> dict[str, Any]:
    found: dict[str, dict[str, float]] = {}
    pats = {
        "dsp": r"\|\s*DSPs?(?: Blocks)?\s*\|\s*([\d,]+)\s*\|[^|]*\|[^|]*\|\s*([\d,]+)\s*\|\s*([\d.<]+)",
        "lut": r"\|\s*(?:CLB|Slice) LUTs\*?\s*\|\s*([\d,]+)\s*\|[^|]*\|[^|]*\|\s*([\d,]+)\s*\|\s*([\d.<]+)",
        "bram_tiles": r"\|\s*Block RAM Tile\s*\|\s*([\d.,]+)\s*\|[^|]*\|[^|]*\|\s*([\d.,]+)\s*\|\s*([\d.<]+)",
        "uram": r"\|\s*URAM\s*\|\s*([\d,]+)\s*\|[^|]*\|[^|]*\|\s*([\d,]+)\s*\|\s*([\d.<]+)",
    }
    for key, p in pats.items():
        m = re.search(p, text, re.I)
        if m:
            found[key] = {"used": _f(m.group(1)), "available": _f(m.group(2))}
    if not found:
        raise ValueError("no utilisation rows found (expected Vivado report_utilization tables)")
    fields: dict[str, Any] = {}
    if "dsp" in found:
        fields["dsp_slices"] = int(found["dsp"]["available"])
    if "bram_tiles" in found:
        fields["bram_mb"] = round(found["bram_tiles"]["available"] * 36 * 1024 / 8 / 1e6, 3)
    if "uram" in found:
        fields["uram_mb"] = round(found["uram"]["available"] * 288 * 1024 / 8 / 1e6, 3)
    return {"tool": "vivado_util", "fields": fields, "found": found,
            "note": "Fields are the device's AVAILABLE resources (what the chip offers). The 'used' "
                    "counts describe your current design; compare them to see how much headroom "
                    "a bigger accelerator would have."}


def parse_stedgeai(text: str) -> dict[str, Any]:
    found: dict[str, float] = {}
    m = re.search(r"weights\s*\(ro\)\s*:\s*([\d,\.]+)\s*(B|KiB|MiB|KB|MB)?", text, re.I)
    if m:
        found["weights_bytes"] = _unit_bytes(_f(m.group(1)), m.group(2) or "B")
    m = re.search(r"activations\s*\(rw\)\s*:\s*([\d,\.]+)\s*(B|KiB|MiB|KB|MB)?", text, re.I)
    if m:
        found["activations_bytes"] = _unit_bytes(_f(m.group(1)), m.group(2) or "B")
    m = re.search(r"(?:macc|MACC)\s*[:=]?\s*([\d,]+)", text)
    if m:
        found["macc"] = _f(m.group(1))
    m = re.search(r"(?:cycles/MACC|cycles per macc)\s*[:=]?\s*([\d.]+)", text, re.I)
    if m:
        found["cycles_per_macc"] = float(m.group(1))
    if not found:
        raise ValueError("no ST Edge AI summary found (weights (ro) / activations (rw) / MACC)")
    return {"tool": "stedgeai", "fields": {}, "found": found,
            "note": "Use weights as the flash need and activations as the SRAM need on your MCU. "
                    "cycles/MACC (if present) is the best input for int8_macs_per_cycle = 1 / value."}


def parse_vela(text: str) -> dict[str, Any]:
    found: dict[str, float] = {}
    for key, pat in {
        "sram_bytes": r"Total SRAM used\s*([\d.,]+)\s*(KiB|MiB|B)",
        "flash_bytes": r"Total (?:Off-chip )?Flash used\s*([\d.,]+)\s*(KiB|MiB|B)",
    }.items():
        m = re.search(pat, text, re.I)
        if m:
            found[key] = _unit_bytes(_f(m.group(1)), m.group(2))
    m = re.search(r"NPU total cycles\s*([\d,]+)", text, re.I) or re.search(
        r"Total NPU cycles\s*([\d,]+)", text, re.I
    )
    if m:
        found["npu_cycles"] = _f(m.group(1))
    m = re.search(r"Batch Inference time\s*([\d.]+)\s*ms", text, re.I)
    if m:
        found["inference_ms"] = float(m.group(1))
    if not found:
        raise ValueError("no Vela summary found (Total SRAM used / NPU cycles)")
    return {"tool": "vela", "fields": {}, "found": found,
            "note": "Vela's cycle count is a performance estimate for the configured NPU; use it as "
                    "a cross-check of this tool's latency, not as a measurement."}


PARSERS = {
    "vivado_power": parse_vivado_power,
    "vivado_util": parse_vivado_util,
    "stedgeai": parse_stedgeai,
    "vela": parse_vela,
}


def parse_report(text: str, tool: str | None = None) -> dict[str, Any]:
    tool = tool or detect(text)
    if tool is None or tool not in PARSERS:
        raise ValueError(
            "could not tell which tool wrote this; choose one of: " + ", ".join(PARSERS)
        )
    return PARSERS[tool](text)


# ---- power-meter CSV ---------------------------------------------------------------------------

_TIME = ("time", "timestamp", "t", "time(s)", "time (s)", "seconds")
_CUR = ("current", "i", "current(a)", "current (a)", "current_a", "current(ma)", "current (ma)",
        "current_ma", "current(ua)", "current (ua)", "current_ua", "amps", "ma")
_POW = ("power", "p", "power(w)", "power (w)", "power_w", "watts", "power(mw)", "power (mw)",
        "power_mw")
_VOLT = ("voltage", "v", "voltage(v)", "voltage (v)", "voltage_v", "volts")


def _col(header: list[str], names: tuple[str, ...], prefix: str) -> int | None:
    low = [h.strip().lower() for h in header]
    for n in names:
        if n in low:
            return low.index(n)
    for i, h in enumerate(low):
        if h.startswith(prefix):
            return i
    return None


def parse_power_csv(text: str, supply_v: float | None = None) -> dict[str, Any]:
    """Average / peak / idle power from a logger export (Joulescope, Otii, PPK2, bench supply...).

    Needs a power column, or current (+ voltage column or `supply_v`). Units are read from the
    header ("mA", "uA", "mW"); a bare current column is assumed amps.
    """
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if len(lines) < 3:
        raise ValueError("need a header and at least two samples")
    rows = list(csv.reader(io.StringIO("\n".join(lines))))
    header, body = rows[0], rows[1:]
    ti = _col(header, _TIME, "time")
    pi = _col(header, _POW, "power")
    ci = _col(header, _CUR, "current")
    vi = _col(header, _VOLT, "voltage")

    def scale(name: str, kind: str) -> float:
        n = name.lower()
        if kind == "p":
            return 1e-3 if "mw" in n else (1e-6 if "uw" in n or "µw" in n else 1.0)
        return 1e-3 if "ma" in n else (1e-6 if "ua" in n or "µa" in n else 1.0)

    watts: list[float] = []
    times: list[float] = []
    for r in body:
        try:
            if pi is not None:
                w = float(r[pi]) * scale(header[pi], "p")
            elif ci is not None:
                v = float(r[vi]) if vi is not None else supply_v
                if v is None:
                    raise ValueError("current without voltage: give the supply voltage")
                w = float(r[ci]) * scale(header[ci], "i") * v
            else:
                raise ValueError("no power or current column found")
            watts.append(w)
            times.append(float(r[ti]) if ti is not None else float(len(times)))
        except (ValueError, IndexError) as e:
            if "supply" in str(e) or "column" in str(e):
                raise
            continue
    if len(watts) < 2:
        raise ValueError("fewer than two usable samples")
    srt = sorted(watts)
    n = len(srt)
    # Active level = mean of the upper half above the midpoint; idle = median of the lower decile.
    mid = (srt[0] + srt[-1]) / 2
    active = [w for w in watts if w >= mid]
    idle = srt[: max(1, n // 10)]
    dur = (times[-1] - times[0]) if times[-1] > times[0] else float(n)
    return {
        "tool": "power_csv",
        "samples": n,
        "duration_s": dur,
        "power_w_avg": sum(watts) / n,
        "power_w_peak": srt[-1],
        "power_w_active": sum(active) / len(active),
        "power_w_idle": sum(idle) / len(idle),
        "note": "Active = mean of samples above the mid-level; idle = lowest decile. Check that "
                "the capture covers steady-state inference, not boot or model load.",
    }
