"""Cooling options from the catalogue, evaluated for one project, ranked by stated criteria.

"Lower impact" is never asserted without numbers. The criteria, in order:
  1. Requirements: the option must keep the project's thermal requirement (no sustained throttling
     when that is required) and every other requirement the evaluation checks.
  2. Lifetime emissions per year = electricity of device + fan/pump at the project's duty and grid
     intensity, plus the cooling hardware's embodied carbon spread over its service life.
  3. Tie-breakers shown, not scored: maintainability (moving parts), recyclability, noise, cost.
An option is labelled "lower impact" only if it meets requirement 1 and is within 10% of the lowest
lifetime emissions among options that do. Embodied figures are low-confidence estimates and are
labelled so.

Catalogue v2 (additive) adds, per class: the heat sources it reaches (die, memory, regulators), the
heat load it is sized for, what it needs from the place it runs (airflow, vents, a data centre,
facility water), height, repairability, hazardous materials and water use. Options that cannot be
used where the device runs are listed with the reason instead of silently dropped, and each heat
source gets a rationale naming the options that reach it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.schemas.hardware import CoolingOption, HardwareProfile
from app.schemas.metrics import Feasibility
from app.schemas.project import Environment, Workload
from app.schemas.results import Config, EvaluationResult

CRITERIA = [
    "Can be used where the device runs (enclosure, dust, noise, height, data centre, facility water) "
    "and is sized for the heat load.",
    "Meets your requirements with this cooling (no sustained throttling if you require that).",
    "Lowest lifetime emissions per year: device and fan/pump electricity at your hours and grid "
    "intensity, plus the cooling hardware's embodied carbon over its service life.",
    "Shown for judgement, not scored: repairability, recyclability, hazardous materials, water use, "
    "maintainability, noise, cost.",
]
SOURCE_NAME = {"compute": "Compute die", "memory": "Memory", "power": "Voltage regulators"}
NEED_TEXT = {
    "free_air": "open fins in moving or free air", "airflow": "an air path for a fan", "vents": "vents in the enclosure",
    "radiator_airflow": "room and airflow for a radiator", "board_change": "a board layout change",
    "datacenter": "a data-centre rack", "facility_water": "a facility water loop",
}  # fmt: skip
AIR_NEEDS = {"free_air", "airflow", "vents", "radiator_airflow"}
LOWER_IMPACT_MARGIN = 0.10


class Range(BaseModel):
    low: float
    typical: float
    high: float


class CostRange(BaseModel):
    low: float
    high: float


class CoolingEntry(BaseModel):
    id: str
    name: str
    type: str
    applies_to: list[str]
    r_th_k_per_w: Range | None = None
    r_th_factor: Range | None = None
    active_w: float
    c_th_j_per_k: float
    cost_usd: CostRange
    noise_dba: float
    materials: str
    embodied_kgco2e: float
    recyclability: str
    maintainability: str
    lifetime_years: float
    confidence: str
    source: str
    # v2 (optional so older catalogues still load)
    targets: list[str] = ["compute"]
    heat_w_max: float | None = None
    needs: list[str] = []
    height_mm: float = 0.0
    repairability: str = "unknown"
    hazardous: str = "none"
    water_l_per_year: float | None = 0.0


def blockers(entry: CoolingEntry, env: Environment, heat_w: float | None) -> list[str]:
    """Why this class cannot be used where the device runs (empty = usable)."""
    out: list[str] = []
    needs = set(entry.needs)
    air = sorted(needs & AIR_NEEDS)
    if env.sealed_enclosure and air:
        out.append("needs " + ", ".join(NEED_TEXT[n] for n in air) + ", but the enclosure is sealed")
    if "datacenter" in needs and not env.datacenter:
        out.append("needs a data-centre rack")
    if "facility_water" in needs and not env.facility_water:
        out.append("needs a facility water loop")
    if env.max_noise_dba is not None and entry.noise_dba > env.max_noise_dba:
        out.append(f"about {entry.noise_dba:g} dBA, above your {env.max_noise_dba:g} dBA limit")
    if env.max_height_mm is not None and entry.height_mm > env.max_height_mm:
        out.append(f"about {entry.height_mm:g} mm tall, above the {env.max_height_mm:g} mm available")
    if heat_w is not None and entry.heat_w_max is not None and heat_w > entry.heat_w_max:
        out.append(f"sized for up to {entry.heat_w_max:g} W; this setup makes about {heat_w:.3g} W")
    return out


def cautions(entry: CoolingEntry, env: Environment) -> list[str]:
    """Usable, but worth knowing here."""
    out: list[str] = []
    if env.dusty and (set(entry.needs) & {"airflow", "vents", "radiator_airflow"} or entry.active_w > 0):
        out.append("dusty site: fans and vents need filters and regular cleaning")
    if entry.hazardous != "none":
        out.append(f"contains {entry.hazardous}: plan for handling and end-of-life disposal")
    return out


def sustainability(entry: CoolingEntry | None) -> dict[str, Any]:
    if entry is None:
        return {"repairability": None, "recyclability": None, "hazardous": None, "water_l_per_year": 0.0,
                "water_note": "", "embodied_note": "Already fitted: no new materials."}  # fmt: skip
    return {"repairability": entry.repairability, "recyclability": entry.recyclability, "hazardous": entry.hazardous,
            "water_l_per_year": entry.water_l_per_year,
            "water_note": "depends on the facility's cooling plant; not estimated" if entry.water_l_per_year is None else "",
            "embodied_note": f"{entry.embodied_kgco2e:g} kgCO2e over {entry.lifetime_years:g} years ({entry.confidence} confidence)"}  # fmt: skip


def heat_rationale(heat: dict[str, float], usable: list[CoolingEntry]) -> list[dict[str, Any]]:
    """Per heat source: its share of the heat and which usable options reach it."""
    total = sum(heat.values()) or 1.0
    out = []
    for src in ("compute", "memory", "power"):
        w = heat.get(src, 0.0)
        share = w / total
        if src == "compute":
            why = ("Most of the heat starts here, so the die's path to air sets the temperature that throttles."
                   if share >= 0.5 else "The die is the part that throttles, even though it is not the largest source here.")  # fmt: skip
        elif src == "memory":
            why = ("Memory shares the package lid on most SoCs, so die-side cooling usually carries it too."
                   if share < 0.15 else "Memory makes a large share of the heat: prefer options that cover the whole package.")  # fmt: skip
        else:
            why = ("Regulators sit on the board, not under the heatsink: only airflow over the board or venting reaches them."
                   if share >= 0.1 else "Small share; board airflow is enough.")  # fmt: skip
        out.append({"source": src, "name": SOURCE_NAME[src], "heat_w": round(w, 4), "share": round(share, 4),
                    "why": why, "reached_by": [e.name for e in usable if src in e.targets],
                    "label": "attribution (from the multi-node thermal view)"})  # fmt: skip
    return out


def applicable(entries: list[CoolingEntry], hw: HardwareProfile) -> list[CoolingEntry]:
    return [e for e in entries if hw.kind in e.applies_to]


def to_option(entry: CoolingEntry, current: CoolingOption) -> CoolingOption:
    """A device cooling option for this entry. Modifiers scale the cooling already fitted."""
    if entry.r_th_factor is not None:
        r = current.r_th_k_per_w * entry.r_th_factor.typical
        return CoolingOption(id=f"cat-{entry.id}", name=f"{current.name} + {entry.name.lower()}",
                             r_th_k_per_w=r, fan_w=current.fan_w + entry.active_w,
                             c_th_j_per_k=current.c_th_j_per_k)  # fmt: skip
    assert entry.r_th_k_per_w is not None
    return CoolingOption(id=f"cat-{entry.id}", name=entry.name, r_th_k_per_w=entry.r_th_k_per_w.typical,
                         fan_w=entry.active_w, c_th_j_per_k=max(entry.c_th_j_per_k, 1.0))  # fmt: skip


def with_option(hw: HardwareProfile, opt: CoolingOption) -> HardwareProfile:
    return hw.model_copy(update={"cooling_options": [*[c for c in hw.cooling_options if c.id != opt.id], opt]})


def analyse(
    entries: list[CoolingEntry],
    hw: HardwareProfile,
    cfg: Config,
    workload: Workload,
    forbid_throttling: bool,
    evaluate_with: Callable[[HardwareProfile, Config], EvaluationResult],
    env: Environment | None = None,
    heat_sources: dict[str, float] | None = None,
) -> dict[str, Any]:
    env = env or Environment()
    current = hw.cooling(cfg.cooling_id)
    base = evaluate_with(hw, cfg)
    heat_total = base.metrics["power_avg"].value - current.fan_w
    heat_sum = sum((heat_sources or {}).values()) or 1.0
    kwh_per_w_year = workload.hours_per_day * 365 / 1000.0
    grid = workload.grid_gco2_per_kwh / 1000.0
    rows: list[dict[str, Any]] = []

    def row(name: str, entry: CoolingEntry | None, opt: CoolingOption, r: EvaluationResult) -> dict[str, Any]:
        p = r.metrics["power_avg"].value  # includes fan/pump power (power model floor)
        op_kg = p * kwh_per_w_year * grid
        emb = (entry.embodied_kgco2e / max(entry.lifetime_years, 0.5)) if entry else 0.0
        meets = r.feasibility in (Feasibility.PREDICTED_FEASIBLE, Feasibility.VERIFIED_FEASIBLE,
                                  Feasibility.UNCERTAIN)  # fmt: skip
        thermal_ok = not (forbid_throttling and r.solver.throttled)
        return {
            "id": entry.id if entry else "current",
            "change_id": f"cooling:{entry.id}" if entry else None,
            "name": name,
            "type": entry.type if entry else "fitted",
            "r_th_k_per_w": opt.r_th_k_per_w,
            "active_w": opt.fan_w,
            "temperature_c": r.metrics["temperature"].value,
            "unthrottled_c": r.metrics["temperature_unthrottled"].value,
            "throttled": r.solver.throttled,
            "clock_factor": r.solver.clock_factor,
            "power_w": p,
            "energy_mj": r.metrics["energy_per_inference"].value,
            "feasibility": r.feasibility.value,
            "meets_requirements": meets and thermal_ok,
            "operational_kgco2e_per_year": op_kg,
            "embodied_kgco2e_per_year": emb,
            "lifetime_kgco2e_per_year": op_kg + emb,
            "electricity_kwh_per_year": p * kwh_per_w_year,
            "cost_usd": entry.cost_usd.model_dump() if entry else None,
            "noise_dba": entry.noise_dba if entry else None,
            "materials": entry.materials if entry else "",
            "recyclability": entry.recyclability if entry else None,
            "maintainability": entry.maintainability if entry else "",
            "lifetime_years": entry.lifetime_years if entry else None,
            "confidence": entry.confidence if entry else "device profile",
            "source": entry.source if entry else "the device profile's cooling option",
            "r_th_range": (entry.r_th_k_per_w or entry.r_th_factor).model_dump() if entry else None,  # type: ignore[union-attr]
            "modifier": bool(entry and entry.r_th_factor is not None),
            "targets": entry.targets if entry else ["compute", "memory", "power"],
            "heat_share_reached": (round(sum(v for k, v in (heat_sources or {}).items() if k in entry.targets) / heat_sum, 4)
                                   if entry and heat_sources else None),
            "sustainability": sustainability(entry),
            "cautions": cautions(entry, env) if entry else [],
            "needs": [NEED_TEXT.get(n, n) for n in entry.needs] if entry else [],
        }

    rows.append(row(f"Fitted today: {current.name}", None, current, base))
    excluded: list[dict[str, Any]] = []
    usable: list[CoolingEntry] = []
    for e in applicable(entries, hw):
        why = blockers(e, env, heat_total)
        if why:
            excluded.append({"id": e.id, "name": e.name, "type": e.type, "reasons": why})
            continue
        usable.append(e)
        opt = to_option(e, current)
        hw2 = with_option(hw, opt)
        try:
            r = evaluate_with(hw2, cfg.model_copy(update={"cooling_id": opt.id}))
        except ValueError:
            continue
        rows.append(row(e.name, e, opt, r))

    ok = [r for r in rows if r["meets_requirements"]]
    best = min((r["lifetime_kgco2e_per_year"] for r in ok), default=None)
    for row_ in rows:
        if not row_["meets_requirements"]:
            row_["rating"] = "does not meet requirements"
            row_["explanation"] = ("Throttles under sustained load." if row_["throttled"] and forbid_throttling
                                else f"Fails a requirement ({row_['feasibility'].replace('_', ' ')}).")  # fmt: skip
        elif best is not None and row_["lifetime_kgco2e_per_year"] <= best * (1 + LOWER_IMPACT_MARGIN):
            row_["rating"] = "lower impact"
            row_["explanation"] = (f"Meets your requirements and is within {LOWER_IMPACT_MARGIN:.0%} of the lowest "
                                f"lifetime emissions ({row_['lifetime_kgco2e_per_year']:.2f} kgCO2e/year: "
                                f"{row_['operational_kgco2e_per_year']:.2f} electricity + "
                                f"{row_['embodied_kgco2e_per_year']:.2f} embodied).")  # fmt: skip
        else:
            row_["rating"] = "meets requirements"
            row_["explanation"] = (f"Meets your requirements; {row_['lifetime_kgco2e_per_year']:.2f} kgCO2e/year, "
                                f"{(row_['lifetime_kgco2e_per_year'] / best - 1) * 100:.0f}% above the lowest.")  # fmt: skip
    rows.sort(key=lambda r: (not r["meets_requirements"], r["lifetime_kgco2e_per_year"]))
    for e in entries:
        if hw.kind not in e.applies_to:
            kinds = ", ".join(k.replace("_", " ") for k in e.applies_to)
            excluded.append({"id": e.id, "name": e.name, "type": e.type,
                             "reasons": [f"sized for {kinds}, not this kind of device"]})  # fmt: skip
    return {
        "catalogue_version": 2,
        "criteria": CRITERIA,
        "rows": rows,
        "excluded": excluded,
        "environment": env.model_dump(),
        "heat_sources": heat_rationale(heat_sources, usable) if heat_sources else [],
        "assumptions": [
            f"{workload.hours_per_day:g} h/day at {workload.grid_gco2_per_kwh:g} gCO2e/kWh (your project settings).",
            "Thermal resistances are the typical value of each class; the range is shown. Confirm with the "
            "chosen part's datasheet and a temperature log under sustained load.",
            "Embodied carbon is a rough materials estimate (low confidence); it rarely changes the ranking "
            "unless the device runs few hours per day. The cooling already fitted adds no new embodied carbon.",
        ],
    }
