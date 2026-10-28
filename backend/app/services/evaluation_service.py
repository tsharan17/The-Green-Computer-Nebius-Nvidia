from __future__ import annotations

from sqlalchemy.orm import Session

from app.data_loader import get_device
from app.db import repository as repo
from app.engine.evaluator import evaluate, matches
from app.engine.optimise import baseline_config, optimise
from app.schemas.project import ProjectSpec
from app.schemas.results import Config, EvaluationResult, OptimisationResult
from app.services import advisor_service
from app.services import measurement_service as ms
from app.services.model_lookup import resolve_model


def evaluate_project(
    db: Session, project_id: int, spec: ProjectSpec, config: Config | None,
    workspace_id: int | None = None, user_id: int | None = None,
) -> EvaluationResult:  # fmt: skip
    ws = workspace_id
    model, hw = resolve_model(db, spec.model_id, ws), get_device(spec.device_id, ws)
    cfg = config or spec.baseline or baseline_config(hw, spec.cooling_id)
    cals = ms.active_calibrations(db, hw.id, ws)
    meas = ms.measurements_for(db, hw.id, model.id, ws)
    ret = ms.measured_retention(db, model.id, ws)
    result = evaluate(
        model, hw, cfg, spec.workload, spec.constraints, ret.get(cfg.precision),
        calibration=cals.get(cfg.precision),
        measured=next((m for m in meas if matches(m, hw, model.id, cfg)), None),
    )  # fmt: skip
    repo.save_run(db, project_id, "evaluate", result.engine_version, result.model_dump(mode="json"),
                  user_id)  # fmt: skip
    return result


def optimise_project(
    db: Session, project_id: int, spec: ProjectSpec, workspace_id: int | None = None,
    user_id: int | None = None,
) -> OptimisationResult:  # fmt: skip
    ws = workspace_id
    model, hw = resolve_model(db, spec.model_id, ws), get_device(spec.device_id, ws)
    result = optimise(
        spec, model, hw,
        measured=ms.measured_retention(db, model.id, ws),
        calibrations=ms.active_calibrations(db, hw.id, ws),
        measurements=ms.measurements_for(db, hw.id, model.id, ws),
    )  # fmt: skip
    advisor_service.enrich(db, spec, model, hw, result, ws)
    repo.save_run(
        db, project_id, "optimise", result.baseline.engine_version, result.model_dump(mode="json"),
        user_id,
    )
    return result
