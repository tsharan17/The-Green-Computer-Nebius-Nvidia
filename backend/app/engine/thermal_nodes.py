"""Optional multi-node thermal view: where the heat of the lumped model comes from and settles.

The platform decides pass/fail with the lumped model (one heat source, one thermal resistance;
`engine/thermal.py`). This module splits that result over a small resistor network so a user can
see which part warms which, and it labels every number:

  lumped       the decision model's temperature (unchanged here)
  attribution  a node temperature from splitting the lumped heat over the network below; the
               resistances inside the package are assumptions unless the profile states them
  measured     a temperature read from a real run of this setup
  validated    the lumped prediction agrees with a measured temperature within the band

Network (steady state, nodal analysis):
  compute --R_jc--> package --R_cs--> sink --R_sa--> ambient
  memory  --R_mp--> package
  power   --R_vs--> sink
R_sa = R_th(cooling) - R_jc - R_cs, so all heat entering at the compute node reproduces the lumped
temperature exactly. Heat inputs are scaled so their sum equals the lumped model's power.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from app.schemas.hardware import HardwareProfile

# Assumed shares of the cooling's thermal resistance inside the package when the profile is silent.
JC_SHARE = 0.12  # junction to case
CS_SHARE = 0.05  # case to sink (thermal interface)
MP_FACTOR = 3.0  # memory to package, relative to R_jc (weaker coupling)
VS_FACTOR = 1.0  # regulators to sink, relative to R_th (on the board, not under the lid)
VALIDATE_C = 3.0  # minimum agreement band for "validated", °C

NODES = [
    ("compute", "Compute die"), ("memory", "Memory"), ("power", "Voltage regulators"),
    ("package", "Package / lid"), ("sink", "Heatsink / board"),
]  # fmt: skip


@dataclass(frozen=True)
class Resistor:
    a: str
    b: str
    k_per_w: float
    source: str  # "profile" | "assumed" | "derived"
    note: str


def network(hw: HardwareProfile, r_th: float) -> list[Resistor]:
    ex = hw.extras or {}

    def given(key: str) -> float | None:
        v = ex.get(key)
        try:
            return float(v) if v is not None and float(v) > 0 else None
        except (TypeError, ValueError):
            return None

    r_jc = given("r_jc_k_per_w")
    r_cs = given("r_cs_k_per_w")
    jc = Resistor("compute", "package", r_jc or JC_SHARE * r_th, "profile" if r_jc else "assumed",
                  "junction to case" + ("" if r_jc else f": {JC_SHARE:.0%} of the cooling's resistance assumed"))  # fmt: skip
    cs = Resistor("package", "sink", r_cs or CS_SHARE * r_th, "profile" if r_cs else "assumed",
                  "thermal interface" + ("" if r_cs else f": {CS_SHARE:.0%} assumed"))  # fmt: skip
    sa = Resistor("sink", "ambient", max(r_th - jc.k_per_w - cs.k_per_w, 0.05 * r_th), "derived",
                  "the rest of the cooling's thermal resistance")  # fmt: skip
    mp = Resistor("memory", "package", MP_FACTOR * jc.k_per_w, "assumed", f"memory coupling: {MP_FACTOR:g}× R_jc assumed")
    vs = Resistor("power", "sink", VS_FACTOR * r_th, "assumed", f"regulators on the board: {VS_FACTOR:g}× R_th assumed")
    return [jc, cs, sa, mp, vs]


def solve(resistors: list[Resistor], heat_w: dict[str, float], ambient_c: float) -> dict[str, float]:
    """Steady-state node temperatures by nodal analysis (ambient is the fixed reference)."""
    ids = [n for n, _ in NODES]
    ix = {n: i for i, n in enumerate(ids)}
    g = np.zeros((len(ids), len(ids)))
    p = np.array([heat_w.get(n, 0.0) for n in ids], dtype=float)
    for r in resistors:
        c = 1.0 / r.k_per_w
        for u, v in ((r.a, r.b), (r.b, r.a)):
            if u in ix:
                g[ix[u], ix[u]] += c
                if v in ix:
                    g[ix[u], ix[v]] -= c
    rise = np.linalg.solve(g, p)
    return {n: float(ambient_c + rise[ix[n]]) for n in ids}


def thermal_view(
    hw: HardwareProfile,
    trace_components: list[dict[str, Any]],
    ambient_c: float,
    lumped_c: float,
    r_th: float,
    measured_c: float | None = None,
    measured_label: str = "",
) -> dict[str, Any]:
    """Labelled multi-node view consistent with the lumped temperature `lumped_c`."""
    raw = {c["id"]: float(c.get("heat_w", 0.0)) for c in trace_components}
    heat = {"compute": raw.get("compute", 0.0) + raw.get("onchip", 0.0),
            "memory": raw.get("ram", 0.0) + raw.get("flash", 0.0), "power": raw.get("power", 0.0)}  # fmt: skip
    p_lumped = max(lumped_c - ambient_c, 0.0) / r_th
    total = sum(heat.values())
    scale = p_lumped / total if total > 0 else 0.0
    if total <= 0:
        heat = {"compute": p_lumped, "memory": 0.0, "power": 0.0}
        scale = 1.0
    heat = {k: v * scale for k, v in heat.items()}
    rs = network(hw, r_th)
    temps = solve(rs, heat, ambient_c)
    nodes: list[dict[str, Any]] = [{"id": n, "name": name, "temperature_c": round(temps[n], 2), "heat_w": round(heat.get(n, 0.0), 4),
              "label": "attribution"} for n, name in NODES]  # fmt: skip
    # The headline names the warmer of die and memory; the regulators' temperature rests on an assumed coupling.
    hottest = max(nodes[:2], key=lambda x: x["temperature_c"])
    out: dict[str, Any] = {
        "ambient_c": ambient_c, "lumped_c": round(lumped_c, 2), "lumped_power_w": round(p_lumped, 4),
        "heat_scale": round(scale, 4), "nodes": nodes,
        "resistors": [r.__dict__ for r in rs], "hottest": hottest["id"],
        "labels": {
            "lumped": "The decision model: one heat source and the cooling's thermal resistance.",
            "attribution": "The lumped heat split over a small network; resistances inside the package are "
                           "assumptions unless the profile states them. Not used for pass/fail.",
            "measured": "A temperature read from a real run of this setup.",
            "validated": f"The lumped prediction is within {VALIDATE_C:g} °C (or its band) of a measured temperature.",
        },
        "measured": None, "status": "lumped only",
    }  # fmt: skip
    if measured_c is not None:
        diff = lumped_c - measured_c
        ok = abs(diff) <= VALIDATE_C
        out["measured"] = {"temperature_c": measured_c, "source": measured_label, "label": "measured",
                           "difference_c": round(diff, 2), "validated": ok}  # fmt: skip
        out["status"] = "validated" if ok else "not validated"
    return out
