"""Recommendation records: the engine's advice, alternatives and what-ifs as one structured list.

Each record says what problem it addresses, what to change, why it may help, which metrics it is
expected to move (engine numbers, never invented here), its trade-offs, what it needs, and its
status. Records are split into software and hardware and ranked by the project's own requirements
and priority. Areas the engine cannot evaluate (code-level changes, circuit or chip edits without
design data) are listed honestly as guidance or as needing an input, never as a computed gain.

Status values:
  recommended   evaluated by the engine (predicted); selectable in the plan builder
  option        a selectable option the engine has not evaluated for this analysis yet
  directional   an analytical what-if on one chip/board parameter; not a validated design
  guidance      an engineering step the engine cannot quantify; no expected number is given
  needs_input   the area needs a file or measurement the project does not have yet
Validation states (evaluated / measured / rejected) are added by the service from saved plans and
measurements.
"""

from __future__ import annotations

from typing import Any

from app.engine.plan import PlanChange
from app.schemas.project import ProjectSpec
from app.schemas.results import EvaluationResult, OptimisationResult

SCHEMA = "green-computer.recommendations/1"
SOFTWARE_AREAS = ("code", "model", "runtime", "execution")
HARDWARE_AREAS = ("architecture", "circuit", "components", "microarchitecture", "memory", "cooling",
                  "platform", "chip", "application")  # fmt: skip
AREA_LABEL = {
    "code": "Code-level change", "model": "Model and inference optimisation", "runtime": "Runtime configuration",
    "execution": "Execution settings", "architecture": "Hardware architecture", "circuit": "Circuit design",
    "components": "Components", "microarchitecture": "Microarchitecture", "memory": "Memory system",
    "cooling": "Cooling and thermal management", "platform": "Hardware platform", "chip": "Chip-level design",
    "application": "Application-specific hardware",
}  # fmt: skip
ADVICE_AREA = {"precision": "model", "pruning": "model", "resolution": "model", "batch": "runtime",
               "power_mode": "execution", "clock": "execution", "cooling": "cooling"}  # fmt: skip
WHATIF_AREA = {"bandwidth": "memory", "compute": "microarchitecture", "dynamic": "chip", "memory_energy": "memory",
               "idle": "circuit", "leakage": "chip", "thermal": "architecture"}  # fmt: skip
PRIORITY_METRIC = {"balanced": "energy_saving_percent", "lower_cost": "energy_saving_percent",
                   "lower_power": "power_saving_percent", "lower_heat": "temperature_change_c",
                   "faster": "latency_change_percent", "fit_memory": "energy_saving_percent"}  # fmt: skip
METRICS_BY_AREA = {
    "model": ["energy", "latency", "memory", "accuracy"], "runtime": ["latency", "throughput", "energy"],
    "execution": ["power", "temperature", "latency"], "cooling": ["temperature", "throttling", "power"],
    "platform": ["latency", "power", "energy", "cost"], "memory": ["latency", "energy"],
    "microarchitecture": ["latency", "power"], "chip": ["power", "energy"], "circuit": ["power"],
    "architecture": ["temperature"], "code": ["latency"], "components": ["power", "temperature"],
    "application": ["power", "latency"],
}  # fmt: skip
BOTTLENECK_TEXT = {
    "compute": "The arithmetic units limit speed (compute-bound).",
    "memory": "Moving data to and from memory limits speed (memory-bound).",
    "overhead": "Per-layer launch and framework overhead dominates the run time.",
    "heat": "The chip reaches its throttle temperature and slows down (heat-bound).",
}


def _failing(r: EvaluationResult) -> list[str]:
    return [c.name for c in r.checks if c.status != "pass"]


def _problem(base: EvaluationResult, fixes: list[str], area: str) -> str:
    if fixes:
        return "Fails today: " + ", ".join(fixes) + "."
    fails = _failing(base)
    if area in ("cooling", "architecture") and base.solver.throttled:
        return "Throttles under sustained load: " + BOTTLENECK_TEXT["heat"]
    if fails:
        return "Today the setup does not meet: " + ", ".join(fails) + "."
    return BOTTLENECK_TEXT.get(base.bottleneck, "") + " Requirements are met; this change targets efficiency."


def _rec(**kw: Any) -> dict[str, Any]:
    area = kw["area"]
    out = {
        "id": kw["id"], "category": "software" if area in SOFTWARE_AREAS else "hardware", "area": area,
        "area_label": AREA_LABEL[area], "title": kw["title"], "problem": kw["problem"], "change": kw["change"],
        "why": kw["why"], "affects": kw.get("affects") or METRICS_BY_AREA.get(area, []),
        "expected": kw.get("expected") or {}, "benefit": kw.get("benefit", ""), "tradeoffs": kw.get("tradeoffs") or [],
        "requires": kw.get("requires") or [], "status": kw["status"], "feasibility": kw.get("feasibility"),
        "confidence": kw.get("confidence", "low"), "evidence": kw.get("evidence", ""),
        "assumptions": kw.get("assumptions") or [], "fixes": kw.get("fixes") or [],
        "plan_change_id": kw.get("plan_change_id"), "in_recommended": kw.get("in_recommended", False),
        "source": kw.get("source", "engine"), "validation": {"state": "not_evaluated"},
    }  # fmt: skip
    return out


def build(
    spec: ProjectSpec,
    res: OptimisationResult,
    changes: list[PlanChange],
    design_files: list[str] | None = None,
) -> dict[str, Any]:
    """All recommendation records for one analysis run, ranked. `design_files` are the kinds of
    parsed hardware design files on the project (e.g. "kicad_pcb", "verilog")."""
    base = res.baseline
    selectable = {c.id: c for c in changes}
    design_files = design_files or []
    prov = base.metrics["latency"].provenance.value
    evidence = f"Engine evaluation ({prov}) of the project's model, device and workload."
    recs: list[dict[str, Any]] = []

    for a in res.advice:
        if a.id in ADVICE_AREA:
            area = ADVICE_AREA[a.id]
        elif a.id.startswith("model:"):
            area = "model"
        elif a.id.startswith("device:"):
            area = "platform"
        elif a.id.startswith("design:"):
            area = WHATIF_AREA.get(a.id.split(":", 1)[1], "chip")
        else:
            area = "runtime" if a.tier == "software" else "model"
        directional = a.tier == "design"
        recs.append(_rec(
            id=a.id, area=area, title=a.title, problem=_problem(base, a.fixes, area), change=a.action, why=a.why,
            expected={k: v for k, v in a.effect.items() if v is not None}, benefit=a.gain, tradeoffs=a.tradeoffs,
            requires=a.validate_steps, status="directional" if directional else "recommended",
            feasibility=None, confidence=a.confidence, evidence=evidence + " " + a.confidence_reason.capitalize() + ".",
            assumptions=["Analytical what-if on one chip/board parameter; needs architecture modelling and a "
                         "prototype to confirm."] if directional else [],
            fixes=a.fixes, plan_change_id=a.id if a.id in selectable else None, in_recommended=a.in_recommended,
        ))  # fmt: skip

    have = {r["id"] for r in recs}
    for c in changes:
        if c.id in have or c.kind not in ("cooling", "design"):
            continue
        area = "cooling" if c.kind == "cooling" else WHATIF_AREA.get(c.params.get("scenario", ""), "chip")
        recs.append(_rec(
            id=c.id, area=area, title=c.title, problem=_problem(base, [], area),
            change=c.title.split(": ", 1)[-1], why="A selectable option the engine can evaluate in the plan builder.",
            tradeoffs=[c.risk] if c.risk else [], status="directional" if c.directional else "option",
            confidence=c.confidence, evidence=evidence, plan_change_id=c.id,
            requires=["Analyse it in the plan builder to see its predicted effect."],
        ))  # fmt: skip

    # Areas the engine does not quantify: engineering guidance, or a request for the missing input.
    if base.bottleneck == "overhead":
        recs.append(_rec(
            id="code:fuse", area="code", title="Reduce per-layer launch overhead in the inference code",
            problem=BOTTLENECK_TEXT["overhead"],
            change="Fuse small layers and run the model through a compiled engine (TensorRT engine or ONNX Runtime "
                   "with graph optimisation and CUDA graphs) instead of calling layers one by one.",
            why="When launch overhead dominates, fewer, larger kernels spend more of the time computing.",
            status="guidance", requires=["Profile the real run (Validate step) to confirm launch overhead."],
            evidence="Bottleneck classification from the engine; the size of the gain is not computed.",
        ))  # fmt: skip
    if base.bottleneck == "memory":
        recs.append(_rec(
            id="code:layout", area="code", title="Keep activations in faster memory and avoid extra copies",
            problem=BOTTLENECK_TEXT["memory"],
            change="Use an inference runtime that fuses element-wise layers, pins input buffers and avoids host-device "
                   "copies per frame; check that pre-processing does not copy each frame twice.",
            why="A memory-bound run speeds up when fewer bytes move per input.",
            status="guidance", requires=["Measure memory traffic with the vendor profiler to confirm."],
            evidence="Bottleneck classification from the engine; the size of the gain is not computed.",
        ))  # fmt: skip
    pcb = "kicad_pcb" in design_files
    if base.solver.throttled or base.bottleneck == "heat":
        if pcb:
            recs.append(_rec(
                id="circuit:heat-path", area="circuit", title="Review the board's heat path under the processor",
                problem="Throttles under sustained load: " + BOTTLENECK_TEXT["heat"],
                change="Check copper area, thermal vias and component spacing under and around the SoC and regulators "
                       "in the uploaded board file; move hot regulators away from the SoC where the layout allows.",
                why="Board-level heat spreading lowers the junction-to-ambient resistance the engine uses.",
                status="guidance", requires=["A thermal simulation or a temperature log of the board to confirm."],
                evidence="The board file was read for structure only; no thermal simulation was run.",
            ))  # fmt: skip
        else:
            recs.append(_rec(
                id="circuit:needs-board", area="circuit", title="Board-level heat path: needs the board file",
                problem="Throttles under sustained load: " + BOTTLENECK_TEXT["heat"],
                change="Upload the board layout (KiCad .kicad_pcb) to get board-level heat-path suggestions.",
                why="Circuit and layout suggestions need the real design; none are guessed.",
                status="needs_input", requires=["KiCad .kicad_pcb (or .kicad_sch for parts)"],
                evidence="No board design file on this project.",
            ))  # fmt: skip
    if not any(f in design_files for f in ("verilog", "hls_report", "vivado_timing", "vivado_utilization")):
        recs.append(_rec(
            id="chip:needs-rtl", area="application", title="Application-specific accelerator: needs RTL or HLS data",
            problem="Chip-level and accelerator changes cannot be judged without the design.",
            change="Upload Verilog/SystemVerilog, an HLS csynth report or Vivado utilisation/timing reports to see "
                   "accelerator-level findings.",
            why="Accelerator suggestions need real resource and timing data; none are guessed.",
            status="needs_input", requires=["Verilog .v/.sv, HLS csynth .xml, or Vivado reports"],
            evidence="No RTL or HLS file on this project.",
        ))  # fmt: skip

    ranked = rank(recs, spec)
    return {
        "schema": SCHEMA,
        "priority": spec.priority,
        "baseline": {"feasibility": base.feasibility.value, "bottleneck": base.bottleneck,
                     "failing": _failing(base), "provenance": prov},  # fmt: skip
        "software": [r for r in ranked if r["category"] == "software"],
        "hardware": [r for r in ranked if r["category"] == "hardware"],
        "statuses": {
            "recommended": "Evaluated by the engine (predicted). Select it in the plan builder to compare.",
            "option": "A selectable option not yet evaluated for this analysis; evaluate it in the plan builder.",
            "directional": "An analytical what-if; not a validated design.",
            "guidance": "An engineering step the engine cannot quantify; no expected number is given.",
            "needs_input": "Needs a file or a measurement this project does not have yet.",
        },
    }


def rank(recs: list[dict[str, Any]], spec: ProjectSpec) -> list[dict[str, Any]]:
    """Fixing a failing requirement first, then the user's priority metric, then confidence."""
    key = PRIORITY_METRIC.get(spec.priority, "energy_saving_percent")
    status_order = {"recommended": 0, "option": 1, "directional": 2, "guidance": 3, "needs_input": 4}
    conf = {"high": 0, "medium": 1, "low": 2}

    def score(r: dict[str, Any]) -> tuple[Any, ...]:
        gain = r["expected"].get(key)
        if gain is None:
            gain = r["expected"].get("energy_saving_percent") or 0.0
        if key == "temperature_change_c":
            gain = -gain  # a drop in temperature is the gain
        return (status_order.get(r["status"], 9), -len(r["fixes"]), not r["in_recommended"], -float(gain or 0.0),
                conf.get(r["confidence"], 3))  # fmt: skip

    out = sorted(recs, key=score)
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out
