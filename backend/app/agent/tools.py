"""Agent tools: the ONLY way the Green Agent touches data or numbers.

Every tool returns plain JSON computed by the deterministic engine or read from storage. The agent
never computes a metric; it plans which tool to call and explains the results. Hard constraints
are not writable here, by construction: `update_project_spec` has no constraint fields and refuses
them explicitly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.agent.datasheet import DatasheetError, extract_hardware_spec, search_hits
from app.data_loader import get_device, load_devices, load_models
from app.db import repository as repo
from app.integrations import tavily as tv
from app.integrations.jobs import JobsError, JobSpec
from app.schemas.project import ProjectSpec
from app.schemas.results import Config, EvaluationResult, OptimisationResult
from app.services import benchmark_service as bench
from app.services import evaluation_service as svc
from app.services import hardware_service
from app.services import measurement_service as ms
from app.services.model_lookup import resolve_model
from app.services.project_service import SpecError, validate_refs

KEY_METRICS = (
    "latency", "batch_time", "capacity_fps", "power_avg", "energy_per_inference",
    "temperature", "temperature_unthrottled", "memory", "accuracy", "clock_factor",
    "cost_per_day", "carbon_per_day",
)  # fmt: skip
WORKLOAD_FIELDS = ("fps", "ambient_c", "hours_per_day", "price_per_kwh", "grid_gco2_per_kwh",
                   "streaming")  # fmt: skip


def sig(x: float, n: int = 4) -> float:
    """Round to n significant digits so tool output stays compact and quotable."""
    if x == 0 or not math.isfinite(x):
        return x
    return round(x, n - 1 - math.floor(math.log10(abs(x))))


def summarise(r: EvaluationResult) -> dict[str, Any]:
    c = r.config
    return {
        "config": c.model_dump(),
        "feasibility": r.feasibility.value,
        "bottleneck": r.bottleneck,
        "throttle_risk": r.throttle_risk,
        "throttled": r.solver.throttled,
        "metrics": {
            k: {
                "value": sig(r.metrics[k].value),
                "unit": r.metrics[k].unit,
                "best_case": sig(r.metrics[k].low),
                "worst_case": sig(r.metrics[k].high),
                "source": r.metrics[k].provenance.value,
            }
            for k in KEY_METRICS
            if k in r.metrics
        },
        "checks": [
            {
                "name": ck.name,
                "status": ck.status,
                "limit": sig(ck.limit),
                "unit": ck.unit,
                "worst_case_value": sig(ck.pessimistic),
            }
            for ck in r.checks
        ],
        "notes": r.notes,
    }


def _pct_saving(base: float, new: float) -> float:
    return sig((1.0 - new / base) * 100.0)


@dataclass
class ToolContext:
    db: Session
    project_id: int
    # The caller's workspace: every lookup is scoped to it (None only for single-user scripts).
    workspace_id: int | None = None
    user_id: int | None = None
    # Candidate ids ("top-1"...) from the latest optimise call, so the model refers to results
    # by id and never has to retype a configuration or a number.
    candidates: dict[str, EvaluationResult] = field(default_factory=dict)
    last_optimisation: OptimisationResult | None = None
    called_tools: list[str] = field(default_factory=list)
    # Provider handles for datasheet import (None when the agent runs without them).
    llm: Any = None
    models: Any = None
    tavily_factory: Any = None

    @property
    def spec(self) -> ProjectSpec:
        p = repo.get_project(self.db, self.project_id, self.workspace_id)
        if p is None:
            raise ToolError("project not found")
        return p.spec


class ToolError(ValueError):
    """A tool call that is invalid; reported to the model so it can correct itself."""


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


_CONFIG_PROPS = {
    "precision": {"type": "string", "enum": ["fp32", "fp16", "int8"]},
    "batch": {"type": "integer", "minimum": 1},
    "power_mode_id": {"type": "string"},
    "cooling_id": {"type": "string"},
    "resolution_scale": {"type": "number", "minimum": 0.1, "maximum": 1},
    "clock_trim": {"type": "number", "minimum": 0.1, "maximum": 1},
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    _fn("get_project",
        "Read the current project: model, device, cooling, baseline, workload and the HARD "
        "constraints (read-only for you).", {}),
    _fn("list_devices",
        "List hardware profiles with supported precisions, power modes and cooling options.", {}),
    _fn("list_models", "List available AI models (library and user-uploaded).", {}),
    _fn("update_project_spec",
        "SAVE a change to the project's model, device, cooling, baseline deployment or workload. Only "
        "when the user explicitly asks to change their project; for what-ifs use `evaluate`. You CANNOT "
        "change hard constraints (power, accuracy, latency, memory); the user does that in the form.",
        {
            "model_id": {"type": "string"},
            "device_id": {"type": "string"},
            "cooling_id": {"type": "string"},
            "baseline": {"type": "object", "properties": _CONFIG_PROPS,
                         "required": ["precision", "batch", "power_mode_id", "cooling_id"]},
            "workload": {"type": "object", "properties": {
                "fps": {"type": "number"}, "ambient_c": {"type": "number"},
                "hours_per_day": {"type": "number"}, "price_per_kwh": {"type": "number"},
                "grid_gco2_per_kwh": {"type": "number"}, "streaming": {"type": "boolean"}}},
        }),
    _fn("evaluate",
        "Run the physics engine on one configuration (omit config for the baseline). Returns every "
        "metric with unit, best/worst case and source, plus constraint checks. For a what-if, pass "
        "model_id, device_id, cooling_id and/or workload: they apply to this evaluation only and the "
        "project is NOT changed. Omit config when you change device_id (power modes differ per device).",
        {"config": {"type": "object", "properties": _CONFIG_PROPS,
                    "required": ["precision", "batch", "power_mode_id", "cooling_id"]},
         "model_id": {"type": "string"},
         "device_id": {"type": "string"},
         "cooling_id": {"type": "string"},
         "workload": {"type": "object", "properties": {
             "fps": {"type": "number"}, "ambient_c": {"type": "number"},
             "hours_per_day": {"type": "number"}, "streaming": {"type": "boolean"}}}}),
    _fn("optimise",
        "Sweep thousands of software and hardware configurations and rank them under the hard "
        "constraints. Returns the best candidate, the top list (ids like top-1), the "
        "software-only / hardware-only / combined comparison and the interaction factor. "
        "Constraints are never relaxed; if nothing is feasible it says so.", {}),
    _fn("get_candidate",
        "Full details for a candidate id returned by `optimise` (for example top-1).",
        {"candidate_id": {"type": "string"}}, ["candidate_id"]),
    _fn("calibration_status",
        "How well the model has been checked against real measurements for this project's device, "
        "and whether a calibration is applied.", {}),
    _fn("find_datasheet",
        "Search the web (Tavily) for spec pages of a hardware device that is not in the library. "
        "Returns titles, URLs and snippets only.",
        {"device_name": {"type": "string"}}, ["device_name"]),
    _fn("import_hardware",
        "Create a DRAFT hardware profile for a device from its datasheet (Tavily search + "
        "Nemotron extraction). The draft is unusable until the USER confirms it in the app; you "
        "cannot confirm it. Tell the user which values were found, which are missing, and that "
        "they must review and confirm them.",
        {"device_name": {"type": "string"}}, ["device_name"]),
    _fn("submit_benchmark",
        "Launch a real GPU benchmark on Nebius Serverless Jobs (reference models only). Fails "
        "clearly if jobs are not configured.",
        {"model_id": {"type": "string", "enum": ["resnet-50", "mobilenet-v2"]},
         "precision": {"type": "string", "enum": ["fp32", "fp16"]},
         "batches": {"type": "array", "items": {"type": "integer", "minimum": 1}}},
        ["model_id"]),
    _fn("get_benchmark", "Check a benchmark job and ingest its measurements when finished.",
        {"benchmark_id": {"type": "integer"}}, ["benchmark_id"]),
]  # fmt: skip


# --- implementations -------------------------------------------------------------------------


def _get_project(ctx: ToolContext, _a: dict) -> dict:
    spec = ctx.spec
    hw = get_device(spec.device_id, ctx.workspace_id)
    model = resolve_model(ctx.db, spec.model_id, ctx.workspace_id)
    return {
        "project": spec.model_dump(mode="json"),
        "model": {"id": model.id, "name": model.name, "task": model.task,
                  "params_m": sig(model.params_m), "gflops": sig(model.gflops)},
        "device": {"id": hw.id, "name": hw.name, "precisions": [p for p, v in hw.peak_tflops.items() if v > 0],
                   "power_modes": [m.id for m in hw.power_modes],
                   "cooling": [c.id for c in hw.cooling_options]},
        "constraints_are_read_only": True,
    }  # fmt: skip


def _list_devices(ctx: ToolContext, _a: dict) -> dict:
    return {
        "devices": [
            {"id": d.id, "name": d.name, "kind": d.kind, "role": d.role,
             "precisions": [p for p, v in d.peak_tflops.items() if v > 0],
             "power_modes": [{"id": m.id, "cap_w": m.cap_w} for m in d.power_modes],
             "cooling": [c.id for c in d.cooling_options]}
            for d in load_devices(ctx.workspace_id).values()
        ]
    }  # fmt: skip


def _list_models(ctx: ToolContext, _a: dict) -> dict:
    items = [*load_models().values(), *repo.list_custom_models(ctx.db, ctx.workspace_id)]
    return {
        "models": [
            {"id": m.id, "name": m.name, "task": m.task, "params_m": sig(m.params_m),
             "gflops": sig(m.gflops), "fidelity": m.fidelity}
            for m in items
        ]
    }  # fmt: skip


def _changed_spec(ctx: ToolContext, a: dict) -> ProjectSpec:
    """The project's spec with the model, device, cooling, baseline or workload in `a` swapped in."""
    forbidden = [k for k in a if k in ("constraints", "max_power_w", "min_accuracy",
                                       "max_latency_ms", "max_memory_gb", "forbid_throttling")]  # fmt: skip
    if forbidden:
        raise ToolError(
            "Hard constraints cannot be changed by the agent. Ask the user to change "
            f"{', '.join(forbidden)} in the project form."
        )
    unknown = [k for k in a if k not in ("model_id", "device_id", "cooling_id", "baseline", "workload")]
    if unknown:
        raise ToolError(f"unknown field(s): {', '.join(unknown)}")
    data = ctx.spec.model_dump()
    for k in ("model_id", "device_id", "cooling_id"):
        if k in a:
            data[k] = a[k]
    if "device_id" in a and "cooling_id" not in a:
        devs = load_devices(ctx.workspace_id)
        data["cooling_id"] = devs[a["device_id"]].default_cooling if a["device_id"] in devs else None
        data["baseline"] = None  # a baseline from another device would not be valid here
    if "baseline" in a:
        data["baseline"] = a["baseline"]
    if "workload" in a:
        bad = [k for k in a["workload"] if k not in WORKLOAD_FIELDS]
        if bad:
            raise ToolError(f"unknown workload field(s): {', '.join(bad)}")
        data["workload"] = {**data["workload"], **a["workload"]}
    try:
        new = ProjectSpec.model_validate(data)
        validate_refs(ctx.db, new, ctx.workspace_id)
    except (SpecError, ValueError) as e:
        raise ToolError(str(e)) from e
    return new


def _update_project_spec(ctx: ToolContext, a: dict) -> dict:
    new = _changed_spec(ctx, a)
    repo.update_project(ctx.db, ctx.project_id, new, ctx.workspace_id, ctx.user_id,
                        note="changed by the Green Agent at the user's request")  # fmt: skip
    ctx.candidates.clear()
    ctx.last_optimisation = None
    return {"updated": True, "project": new.model_dump(mode="json")}


def _evaluate(ctx: ToolContext, a: dict) -> dict:
    # A what-if on another model, device, cooling or workload is evaluated without saving it.
    what_if = {k: a[k] for k in ("model_id", "device_id", "cooling_id", "workload") if k in a}
    spec = _changed_spec(ctx, what_if) if what_if else ctx.spec
    cfg = None
    if a.get("config"):
        try:
            cfg = Config.model_validate(a["config"])
        except ValueError as e:
            raise ToolError(f"invalid config: {e}") from e
    try:
        r = svc.evaluate_project(ctx.db, ctx.project_id, spec, cfg, ctx.workspace_id,
                                 ctx.user_id)  # fmt: skip
    except (ValueError, KeyError) as e:
        raise ToolError(str(e)) from e
    return summarise(r)


def _optimise(ctx: ToolContext, _a: dict) -> dict:
    res = svc.optimise_project(ctx.db, ctx.project_id, ctx.spec, ctx.workspace_id, ctx.user_id)
    ctx.last_optimisation = res
    ctx.candidates = {f"top-{i + 1}": r for i, r in enumerate(res.top)}
    base_e = res.baseline.metrics["energy_per_inference"].value
    base_p = res.baseline.metrics["power_avg"].value
    out: dict[str, Any] = {
        "evaluated": res.evaluated,
        "counts": res.counts,
        "search_seconds": sig(res.search_seconds),
        "baseline": summarise(res.baseline),
        "message": res.message or None,
    }
    if res.best is not None and res.recommendation is not None:
        be = res.best.metrics["energy_per_inference"].value
        bp = res.best.metrics["power_avg"].value
        out["best"] = {
            "candidate_id": "top-1",
            **summarise(res.best),
            "changes_from_baseline": res.recommendation.changes,
            "energy_saving_percent": _pct_saving(base_e, be),
            "power_saving_percent": _pct_saving(base_p, bp),
            "caveats": res.recommendation.caveats,
        }
    out["top"] = [
        {"candidate_id": f"top-{i + 1}", "config": r.config.model_dump(),
         "feasibility": r.feasibility.value,
         "power_w": sig(r.metrics["power_avg"].value),
         "energy_mj": sig(r.metrics["energy_per_inference"].value),
         "latency_ms": sig(r.metrics["latency"].value),
         "temperature_c": sig(r.metrics["temperature"].value)}
        for i, r in enumerate(res.top[:6])
    ]  # fmt: skip
    if res.verdict:
        v = res.verdict
        out["verdict"] = {
            "summary": v.summary,
            "current_setup_ok": v.baseline.ok,
            "current_problems": v.baseline.problems,
            "recommended_setup_ok": v.improved.ok if v.improved else None,
            "savings": None if not v.savings else {
                "power_percent": sig(v.savings.power_percent),
                "energy_percent": sig(v.savings.energy_percent),
                "carbon_kg_per_year": sig(v.savings.carbon_kg_per_year),
            },
        }  # fmt: skip
    if res.repair:
        out["cheapest_fix"] = {
            "needed": res.repair.needed,
            "message": res.repair.message,
            "binding_constraints": res.repair.binding_constraints,
            "plans": [
                {"changes": p.changes, "effort_score": p.effort_score,
                 "feasibility": p.feasibility.value, "fixes": p.fixes}
                for p in res.repair.plans
            ],
        }  # fmt: skip
    out["limiting_factor"] = res.limiting_factor
    out["recommendations"] = [
        {"tier": a.tier, "title": a.title, "gain": a.gain, "confidence": a.confidence,
         "effort": a.effort, "why": a.why, "tradeoffs": a.tradeoffs, "fixes": a.fixes}
        for a in res.advice
    ]  # fmt: skip
    out["alternatives"] = [
        {"kind": x.kind, "name": x.name, "feasibility": x.feasibility.value,
         "energy_saving_percent": None if x.energy_saving_percent is None else sig(x.energy_saving_percent),
         "compared_with": x.compared_with,
         "accuracy_retention": None if x.accuracy_retention is None else sig(x.accuracy_retention)}
        for x in res.alternatives
    ]  # fmt: skip
    out["hardware_design_whatifs"] = [
        {"change": w.change, "energy_change_percent": sig(w.energy_change_percent),
         "latency_change_percent": sig(w.latency_change_percent),
         "elasticity_latency": sig(w.elasticity_latency)}
        for w in res.whatifs[:4]
    ]  # fmt: skip
    cd = res.codesign

    def arm(r: EvaluationResult | None) -> dict | None:
        if r is None:
            return None
        e = r.metrics["energy_per_inference"].value
        return {"config": r.config.model_dump(), "feasibility": r.feasibility.value,
                "energy_mj": sig(e), "power_w": sig(r.metrics["power_avg"].value),
                "energy_saving_percent": _pct_saving(base_e, e)}  # fmt: skip

    out["codesign"] = {
        "software_only": arm(cd.software_only),
        "hardware_only": arm(cd.hardware_only),
        "combined": arm(cd.combined),
        "interaction_factor": None if cd.interaction_factor is None else sig(cd.interaction_factor),
        "interpretation": cd.interpretation,
    }
    return out


def _get_candidate(ctx: ToolContext, a: dict) -> dict:
    cid = a.get("candidate_id", "")
    if cid not in ctx.candidates:
        have = ", ".join(ctx.candidates) or "none (call optimise first)"
        raise ToolError(f"unknown candidate '{cid}'; available: {have}")
    return summarise(ctx.candidates[cid])


def _calibration_status(ctx: ToolContext, _a: dict) -> dict:
    spec = ctx.spec
    v = ms.verify(ctx.db, spec.device_id, ctx.workspace_id)
    if not v.summaries:
        return {
            "device_id": spec.device_id,
            "measurements": 0,
            "note": "No real measurements exist for this device: every number is an analytical "
            "prediction with wide bands.",
        }
    return {
        "device_id": spec.device_id,
        "summaries": [
            {"precision": s.precision, "measurements": s.n,
             "error_before_percent": None if s.mape_before is None else sig(s.mape_before * 100),
             "error_after_percent": None if s.mape_after is None else sig(s.mape_after * 100),
             "calibration_applied": s.calibration_active, "note": s.note}
            for s in v.summaries
        ],
    }  # fmt: skip


def _submit_benchmark(ctx: ToolContext, a: dict) -> dict:
    spec = JobSpec(
        device_id=ctx.spec.device_id, model_id=a["model_id"], precision=a.get("precision", "fp32"),
        batches=tuple(a.get("batches") or (1, 8, 32)),
    )  # fmt: skip
    try:
        return bench.submit(ctx.db, spec, ws=ctx.workspace_id)
    except JobsError as e:
        raise ToolError(str(e)) from e


def _get_benchmark(ctx: ToolContext, a: dict) -> dict:
    try:
        return bench.refresh(ctx.db, int(a["benchmark_id"]), ws=ctx.workspace_id)
    except (JobsError, KeyError, ValueError) as e:
        raise ToolError(str(e)) from e


def _find_datasheet(ctx: ToolContext, a: dict) -> dict:
    name = str(a.get("device_name", "")).strip()
    if len(name) < 2:
        raise ToolError("device_name is required")
    try:
        client = (ctx.tavily_factory or tv.TavilyClient)()
        hits = search_hits(client, name)
    except tv.TavilyError as e:
        raise ToolError(str(e)) from e
    return {"results": [{"title": h.title, "url": h.url, "snippet": h.content[:300]} for h in hits]}


def _import_hardware(ctx: ToolContext, a: dict) -> dict:
    name = str(a.get("device_name", "")).strip()
    if len(name) < 2:
        raise ToolError("device_name is required")
    if ctx.llm is None or ctx.models is None:
        raise ToolError("the model provider is not available for extraction")
    try:
        client = (ctx.tavily_factory or tv.TavilyClient)()
        hits = search_hits(client, name)
        ex = extract_hardware_spec(ctx.llm, ctx.models.for_task("fast"), name, hits)
    except (tv.TavilyError, DatasheetError) as e:
        raise ToolError(str(e)) from e
    draft = hardware_service.store_draft(ctx.db, name, ex, [h.url for h in hits], ctx.workspace_id)
    return {
        "draft_id": draft["id"],
        "status": "draft: NOT usable until the user confirms it in the app",
        "found": draft["candidate"],
        "missing": draft["missing"],
        "dropped_implausible": draft["dropped_implausible"],
        "needs_user_confirmation": True,
    }


_IMPLS = {"find_datasheet": _find_datasheet, "import_hardware": _import_hardware,
          
    "get_project": _get_project, "list_devices": _list_devices, "list_models": _list_models,
    "update_project_spec": _update_project_spec, "evaluate": _evaluate, "optimise": _optimise,
    "get_candidate": _get_candidate, "calibration_status": _calibration_status,
    "submit_benchmark": _submit_benchmark, "get_benchmark": _get_benchmark,
}  # fmt: skip


def dispatch(ctx: ToolContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run a tool. Never raises: errors come back as {"error": ...} for the model to read."""
    ctx.called_tools.append(name)
    impl = _IMPLS.get(name)
    if impl is None:
        return {"error": f"unknown tool '{name}'", "available": sorted(_IMPLS)}
    try:
        return impl(ctx, args)
    except ToolError as e:
        return {"error": str(e)}
    except (KeyError, TypeError) as e:
        return {"error": f"invalid arguments for {name}: {e}"}
