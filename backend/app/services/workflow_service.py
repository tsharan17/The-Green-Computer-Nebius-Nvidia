"""The workflow stages for one project, as structured records:

  input -> verification -> analysis -> recommendations -> validation -> results/exports

Every stored record is immutable and kept. When an input changes, only records that depend on the
analysed inputs are marked stale (renaming a project does not invalidate its analysis); nothing is
deleted, so going back to an earlier stage never loses work.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data_loader import load_cooling_catalogue, load_devices
from app.db import repository as repo
from app.db.models import (
    ArtifactRow,
    DesignPlanRow,
    ExecutionJobRow,
    ExportRow,
    ProjectVersionRow,
    RunRow,
)
from app.engine import recommendations as recs_engine
from app.engine.cooling_catalogue import applicable
from app.engine.evaluator import matches
from app.engine.plan import changes_from_result
from app.engine.verification import verify
from app.schemas.project import ProjectOut
from app.schemas.results import Config, OptimisationResult
from app.services import measurement_service as ms
from app.services.model_lookup import resolve_model

SCHEMA = "green-computer.pipeline/1"
NOT_ANALYSED = "Run the analysis first: recommendations are built from its results."
# Spec fields that do not change any number (renaming must not invalidate an analysis).
_LABEL_FIELDS = ("name", "use_case")
DESIGN_KINDS = {"kicad_schematic": "kicad_sch", "kicad_pcb": "kicad_pcb", "verilog": "verilog",
                "hls_report": "hls_report", "vivado_timing": "vivado_timing",
                "vivado_utilization": "vivado_utilization", "vivado_power": "vivado_power"}  # fmt: skip


def inputs_hash(spec: dict[str, Any]) -> str:
    core = {k: v for k, v in spec.items() if k not in _LABEL_FIELDS}
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()


def _version_hash(db: Session, version_id: int | None) -> str | None:
    if version_id is None:
        return None
    v = db.get(ProjectVersionRow, version_id)
    return inputs_hash(v.spec) if v is not None else None


def _files(db: Session, ws: int, project_id: int) -> list[ArtifactRow]:
    return list(db.scalars(select(ArtifactRow).where(ArtifactRow.workspace_id == ws, ArtifactRow.project_id == project_id,
                                                     ArtifactRow.deleted_at.is_(None)).order_by(ArtifactRow.id)))  # fmt: skip


def _file_view(a: ArtifactRow) -> dict[str, Any]:
    v = a.validation or {}
    errors = v.get("errors") or []
    required = v.get("required")
    if isinstance(required, list):
        required = "; ".join(str(x) for x in required)
    return {"id": a.id, "filename": a.filename, "kind": a.kind, "status": a.status,
            "needed": f"Supply instead: {required}" if required else None,
            "error": errors[0] if errors else None}  # fmt: skip


# --- verification ----------------------------------------------------------------------------------


def verification(db: Session, project: ProjectOut, ws: int) -> dict[str, Any]:
    spec = project.spec
    try:
        model = resolve_model(db, spec.model_id, ws)
    except (KeyError, ValueError, LookupError):
        model = None
    hw = load_devices(ws).get(spec.device_id)
    measured = bool(model and ms.measured_retention(db, model.id, ws))
    files = [_file_view(a) for a in _files(db, ws, project.id)]
    return verify(spec, model, hw, files, measured, project.version)


# --- recommendations -------------------------------------------------------------------------------


def _latest_analysis(db: Session, project_id: int) -> tuple[OptimisationResult, RunRow] | None:
    row = repo.latest_run_row(db, project_id, "optimise")
    if row is None:
        return None
    return OptimisationResult.model_validate(row.result), row


def _validation_states(db: Session, project: ProjectOut, ws: int, run_id: int,
                       items: list[dict[str, Any]]) -> None:  # fmt: skip
    """Attach what has happened to each recommendation: evaluated in a saved plan (predicted),
    rejected there (does not meet requirements), or measured on a real run of that setup."""
    plans = list(db.scalars(select(DesignPlanRow).where(DesignPlanRow.workspace_id == ws,
                                                        DesignPlanRow.project_id == project.id)
                            .order_by(DesignPlanRow.id.desc())))  # fmt: skip
    hw = load_devices(ws).get(project.spec.device_id)
    meas = ms.measurements_for(db, hw.id, project.spec.model_id, ws) if hw else []
    ok = ("predicted_feasible", "verified_feasible")
    for r in items:
        cid = r.get("plan_change_id")
        if not cid:
            continue
        for p in plans:
            single = next((s for s in p.analysis.get("singles", []) if s["change"]["id"] == cid), None)
            if single is None:
                continue
            view = single["view"]
            base = p.analysis["baseline"]["metrics"].get("energy_per_inference", {}).get("value")
            new = view["metrics"].get("energy_per_inference", {}).get("value")
            state = "evaluated" if view["feasibility"] in ok else "rejected"
            cfg = Config.model_validate(view["config"])
            measured = hw is not None and any(matches(m, hw, project.spec.model_id, cfg) for m in meas)
            r["validation"] = {
                "state": "measured" if measured else state,
                "plan_id": p.id, "plan_name": p.name, "plan_version": p.version,
                "stale": p.source_run_id != run_id,
                "feasibility": view["feasibility"],
                "energy_change_percent": (new / base - 1) * 100 if base and new is not None else None,
                "method": "measured on a real run of this setup" if measured else
                          "engine prediction in a saved plan (not measured)",
            }  # fmt: skip
            break


def recommendations(db: Session, project: ProjectOut, ws: int) -> dict[str, Any]:
    got = _latest_analysis(db, project.id)
    if got is None:
        return {"schema": recs_engine.SCHEMA, "analysed": False, "message": NOT_ANALYSED,
                "software": [], "hardware": [], "ai": None}  # fmt: skip
    res, row = got
    hw = load_devices(ws)[project.spec.device_id]
    changes = changes_from_result(res, applicable(load_cooling_catalogue(), hw))
    design = sorted({DESIGN_KINDS[a.kind] for a in _files(db, ws, project.id)
                     if a.kind in DESIGN_KINDS and a.status in ("valid", "needs_review")})  # fmt: skip
    out = recs_engine.build(project.spec, res, changes, design)
    _validation_states(db, project, ws, row.id, [*out["software"], *out["hardware"]])
    ai = repo.latest_run_row(db, project.id, "ai_candidates")
    ai_view = None
    if ai is not None and ai.result.get("source_run_id") == row.id:
        ai_view = ai.result
        _validation_states(db, project, ws, row.id, ai_view.get("candidates", []))
    cur_row = repo.current_version_row(db, project.id)
    cur = _version_hash(db, cur_row.id) if cur_row else None
    out.update({"analysed": True, "source_run_id": row.id, "design_files": design, "ai": ai_view,
                "stale": _version_hash(db, row.version_id) not in (None, cur)})  # fmt: skip
    return out


def ai_candidates(db: Session, project: ProjectOut, ws: int, user_id: int | None, llm: Any, model: str,
                  refresh: bool = False) -> dict[str, Any]:  # fmt: skip
    """Ask the language model for candidate changes, once per analysis (cached by input hash)."""
    from app.agent import recommender

    got = _latest_analysis(db, project.id)
    if got is None:
        raise ValueError(NOT_ANALYSED)
    res, row = got
    deterministic = recommendations(db, project, ws)
    resolved = resolve_model(db, project.spec.model_id, ws)
    analysis = res.model_dump(mode="json")
    analysis["model_name"] = resolved.name
    analysis["device_name"] = load_devices(ws)[project.spec.device_id].name
    summary = recommender.summary_for(analysis, deterministic, project.spec.model_dump(mode="json"))
    h = recommender.input_hash(summary, model)
    cached = repo.latest_run_row(db, project.id, "ai_candidates")
    if cached is not None and not refresh and cached.result.get("input_hash") == h:
        return {**cached.result, "cached": True}
    out = recommender.generate(llm, model, summary)
    out.update({"input_hash": h, "source_run_id": row.id, "sent": sorted(summary)})
    repo.save_run(db, project.id, "ai_candidates", f"llm:{out['model']}"[:40], out, user_id)
    return {**out, "cached": False}


# --- pipeline --------------------------------------------------------------------------------------


def pipeline(db: Session, project: ProjectOut, ws: int) -> dict[str, Any]:
    cur_row = repo.current_version_row(db, project.id)
    cur_hash = _version_hash(db, cur_row.id) if cur_row else inputs_hash(project.spec.model_dump(mode="json"))
    files = _files(db, ws, project.id)
    ver = verification(db, project, ws)
    stages: list[dict[str, Any]] = [
        {"id": "input", "title": "Inputs", "state": "done",
         "record": {"project_version": project.version, "model_id": project.spec.model_id,
                    "device_id": project.spec.device_id, "files": len(files)},
         "summary": f"Version {project.version}; {len(files)} file(s) on the project."},
        {"id": "verification", "title": "Verification", "depends_on": ["input"],
         "state": "blocked" if not ver["ready_for_analysis"] else "done",
         "record": {"status": ver["status"], "counts": ver["counts"]},
         "summary": (f"{ver['counts']['fail']} problem(s) to fix before analysis." if not ver["ready_for_analysis"]
                     else f"{ver['counts']['pass']} checks pass, {ver['counts']['warn']} warning(s).")},
    ]  # fmt: skip
    got = _latest_analysis(db, project.id)
    if got is None:
        stages.append({"id": "analysis", "title": "Analysis", "depends_on": ["verification"],
                       "state": "ready" if ver["ready_for_analysis"] else "blocked", "record": None,
                       "summary": "Not analysed yet."})  # fmt: skip
        run_id, stale = None, False
    else:
        res, row = got
        run_hash = _version_hash(db, row.version_id)
        stale = run_hash is not None and run_hash != cur_hash
        run_id = row.id
        stages.append({"id": "analysis", "title": "Analysis", "depends_on": ["verification"],
                       "state": "stale" if stale else "done",
                       "record": {"run_id": row.id, "created_at": row.created_at.isoformat(),
                                  "feasibility": res.baseline.feasibility.value, "bottleneck": res.baseline.bottleneck,
                                  "provenance": res.baseline.metrics["latency"].provenance.value},
                       "summary": ("Inputs changed since this analysis; it is kept, and re-running adds a new one."
                                   if stale else f"Today: {res.baseline.feasibility.value.replace('_', ' ')}; "
                                                 f"limited by {res.baseline.bottleneck}.")})  # fmt: skip
    if run_id is None:
        stages.append({"id": "recommendations", "title": "Recommendations", "depends_on": ["analysis"],
                       "state": "blocked", "record": None, "summary": NOT_ANALYSED})  # fmt: skip
    else:
        ai = repo.latest_run_row(db, project.id, "ai_candidates")
        n_adv = len(got[0].advice) if got else 0
        stages.append({"id": "recommendations", "title": "Recommendations", "depends_on": ["analysis"],
                       "state": "stale" if stale else "done",
                       "record": {"source_run_id": run_id, "engine_items": n_adv,
                                  "ai_candidates": len(ai.result.get("candidates", []))
                                  if ai is not None and ai.result.get("source_run_id") == run_id else 0},
                       "summary": f"{n_adv} engine recommendation(s) from analysis #{run_id}."})  # fmt: skip
    plans = list(db.scalars(select(DesignPlanRow).where(DesignPlanRow.workspace_id == ws,
                                                        DesignPlanRow.project_id == project.id)
                            .order_by(DesignPlanRow.id.desc())))  # fmt: skip
    current_plans = [p for p in plans if p.source_run_id == run_id] if run_id else []
    jobs = db.scalar(select(func.count()).select_from(ExecutionJobRow).where(
        ExecutionJobRow.workspace_id == ws, ExecutionJobRow.project_id == project.id,
        ExecutionJobRow.status == "succeeded")) or 0  # fmt: skip
    if run_id is None:
        vstate = "blocked"
    elif current_plans:
        vstate = "stale" if stale else "done"
    else:
        vstate = "stale" if plans else "ready"
    stages.append({"id": "validation", "title": "Validation", "depends_on": ["recommendations"], "state": vstate,
                   "record": {"plans": len(plans), "plans_for_this_analysis": len(current_plans),
                              "latest_plan_id": plans[0].id if plans else None, "real_runs": jobs},
                   "summary": (f"{len(current_plans)} saved plan(s) evaluated against this analysis; {jobs} real run(s)."
                               if run_id else "Needs an analysis and a selection of changes.")})  # fmt: skip
    exports = list(db.scalars(select(ExportRow).where(ExportRow.workspace_id == ws, ExportRow.project_id == project.id)
                              .order_by(ExportRow.id.desc()).limit(50)))  # fmt: skip
    stages.append({"id": "results", "title": "Results and exports", "depends_on": ["validation"],
                   "state": "done" if exports else ("ready" if run_id else "blocked"),
                   "record": {"exports": len(exports), "latest_export_id": exports[0].id if exports else None,
                              "latest_kind": exports[0].kind if exports else None},
                   "summary": f"{len(exports)} export(s) with receipts." if exports else "Nothing exported yet."})  # fmt: skip
    nxt = next((s for s in stages if s["state"] in ("blocked", "stale", "ready")), None)
    return {"schema": SCHEMA, "project_id": project.id, "project_version": project.version,
            "inputs_hash": cur_hash, "stages": stages, "next": nxt["id"] if nxt else None,
            "rule": "Records are never deleted. A change to the analysed inputs marks the analysis and what "
                    "was built from it as stale; renaming does not."}  # fmt: skip
