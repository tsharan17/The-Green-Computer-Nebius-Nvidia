"""Library API for software engineers: no server, no database.

    from app.sdk import load_project, analyse, gate

    p = load_project("greencomputer.yaml")
    res = analyse(p)
    g = gate(p, res)           # g.passed, g.status, g.messages

The same engine as the web app, so numbers agree. Everything is analytical unless you give
measurements (not supported here yet): calibration needs the app's Real measurements page.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.data_loader import get_device, load_devices, load_models
from app.engine.advisor import build_advice
from app.engine.alternatives import device_alternatives, model_alternatives
from app.engine.evaluator import evaluate
from app.engine.hwbuild import CustomHardwareSpec, build_custom
from app.engine.optimise import baseline_config, make_evaluator, optimise
from app.engine.whatif import limiting_factor, make_whatifs
from app.engine.workload.onnx_parser import parse_onnx
from app.schemas.config import Config
from app.schemas.hardware import HardwareProfile
from app.schemas.metrics import Feasibility
from app.schemas.model import ModelProfile
from app.schemas.project import Constraints, ProjectSpec, Workload
from app.schemas.results import EvaluationResult, OptimisationResult

EXAMPLE = """\
# greencomputer.yaml - requirements and the setup you run today, checked in CI with `gc check`.
name: my-edge-vision
model: models/detector.onnx      # an ONNX file, or a library id such as mobilenet-v2 / yolov8n
device: jetson-orin-nano         # a library id (see `gc devices`) or an inline `custom:` block below
# custom:                        # describe your own board instead of `device:`
#   name: My board
#   style: mcu                   # generic | mcu | fpga | accelerator
#   core_mhz: 480
#   int8_macs_per_cycle: 2
#   flash_kb: 2048
#   sram_kb: 1024
#   run_power_mw: 250
workload:
  fps: 30
  ambient_c: 35
requirements:
  max_power_w: 15
  min_accuracy: 0.95             # fraction of the model's baseline accuracy
  max_latency_ms: 50
current:                         # what you run today (default: fp32, batch 1, top power mode)
  precision: fp16
  batch: 1
budgets:                         # optional regression gates against a saved result (`gc check --compare`)
  max_energy_increase_percent: 5
  max_latency_increase_percent: 10
"""


class ProjectFileError(ValueError):
    """The project file is wrong in a way the user can fix."""


@dataclass
class Project:
    spec: ProjectSpec
    model: ModelProfile
    hw: HardwareProfile
    budgets: dict[str, float] = field(default_factory=dict)
    source: str = ""


def _read(source: str | Path | dict[str, Any]) -> tuple[dict[str, Any], Path]:
    if isinstance(source, dict):
        return source, Path.cwd()
    path = Path(source)
    if not path.is_file():
        raise ProjectFileError(f"project file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ProjectFileError(f"{path}: not valid YAML ({e})") from e
    if not isinstance(data, dict):
        raise ProjectFileError(f"{path}: expected a mapping at the top level")
    return data, path.parent


def _model(ref: Any, base: Path) -> ModelProfile:
    if not isinstance(ref, str) or not ref:
        raise ProjectFileError("`model:` must be a library id or the path of an .onnx file")
    lib = load_models()
    if ref in lib:
        return lib[ref]
    p = (base / ref) if not Path(ref).is_absolute() else Path(ref)
    if p.suffix.lower() != ".onnx":
        raise ProjectFileError(
            f"model '{ref}' is not in the library ({', '.join(sorted(lib))}) and is not an .onnx "
            "file. Export PyTorch/TensorFlow models to ONNX first."
        )
    if not p.is_file():
        raise ProjectFileError(f"model file not found: {p}")
    from app.engine.workload.onnx_parser import ModelParseError

    try:
        parsed = parse_onnx(p.read_bytes(), name=p.stem, dynamic_dim_default=1)
    except ModelParseError as e:
        raise ProjectFileError(f"{p.name}: {e}") from e
    return parsed.profile


def _device(data: dict[str, Any], workspace_id: int | None = None) -> HardwareProfile:
    if "custom" in data:
        try:
            return build_custom(CustomHardwareSpec.model_validate(data["custom"]), "custom-project")
        except ValueError as e:
            raise ProjectFileError(f"custom hardware: {e}") from e
    dev = data.get("device")
    if not isinstance(dev, str):
        raise ProjectFileError("give `device:` (a library id, see `gc devices`) or a `custom:` block")
    try:
        return get_device(dev, workspace_id)
    except KeyError:
        raise ProjectFileError(
            f"unknown device '{dev}'. Known: {', '.join(sorted(load_devices(workspace_id)))}"
        ) from None


def load_project(source: str | Path | dict[str, Any], workspace_id: int | None = None) -> Project:
    data, base = _read(source)
    model = _model(data.get("model"), base)
    hw = _device(data, workspace_id)
    try:
        wl = Workload.model_validate(data.get("workload", {}))
        cons = Constraints.model_validate(data.get("requirements", {}))
        cur = data.get("current")
        top = max(hw.power_modes, key=lambda m: m.cap_w).id
        baseline = None
        if cur:
            fallback = baseline_config(hw, data.get("cooling")).model_dump()
            baseline = Config.model_validate({**fallback, **cur})
        spec = ProjectSpec(
            name=str(data.get("name", "project")), model_id=model.id, device_id=hw.id,
            cooling_id=data.get("cooling"), baseline=baseline, workload=wl, constraints=cons,
        )  # fmt: skip
        if baseline and baseline.power_mode_id not in {m.id for m in hw.power_modes}:
            raise ProjectFileError(
                f"current.power_mode_id '{baseline.power_mode_id}' unknown for {hw.id}; "
                f"choose from {[m.id for m in hw.power_modes]} (default {top})"
            )
    except ProjectFileError:
        raise
    except ValueError as e:
        raise ProjectFileError(str(e)) from e
    return Project(spec, model, hw, {k: float(v) for k, v in (data.get("budgets") or {}).items()},
                   str(source))


def analyse(p: Project, top_n: int = 5) -> OptimisationResult:
    res = optimise(p.spec, p.model, p.hw, top_n=top_n)
    ev = make_evaluator(p.model, p.hw, p.spec.workload, p.spec.constraints)

    def ev_hw(hw2: HardwareProfile, cfg: Config) -> EvaluationResult:
        return evaluate(p.model, hw2, cfg, p.spec.workload, p.spec.constraints)

    base_cfg = res.baseline.config
    best_cfg = res.best.config if res.best else base_cfg
    res.whatifs = make_whatifs(ev_hw, p.hw, best_cfg, base_cfg)
    res.limiting_factor = limiting_factor(ev_hw, p.hw, base_cfg)
    res.alternatives = [
        *model_alternatives(p.spec, p.model, p.hw, list(load_models().values()), res.baseline, {},
                            res.best),
        *device_alternatives(p.spec, p.model, p.hw, list(load_devices().values()), res.baseline,
                             lambda d: {}, res.best),
    ]
    res.advice = build_advice(
        ev, p.hw, res.baseline, res.best, res.alternatives, res.whatifs, res.limiting_factor
    )
    return res


@dataclass
class GateResult:
    status: str  # pass | uncertain | fail
    passed: bool
    messages: list[str]
    summary: dict[str, float]


def snapshot(res: OptimisationResult) -> dict[str, float]:
    m = res.baseline.metrics
    return {
        "energy_mj": m["energy_per_inference"].value,
        "latency_ms": m["latency"].value,
        "power_w": m["power_avg"].value,
        "temperature_c": m["temperature"].value,
        "capacity_fps": m["capacity_fps"].value,
    }


def gate(
    p: Project,
    res: OptimisationResult,
    *,
    strict: bool = False,
    previous: dict[str, float] | None = None,
    tolerance_percent: float | None = None,
) -> GateResult:
    """Does the setup you run TODAY meet the requirements, and has it regressed?"""
    base = res.baseline
    msgs: list[str] = []
    state = "pass"
    if base.feasibility in (Feasibility.INFEASIBLE, Feasibility.VERIFIED_INFEASIBLE):
        state = "fail"
        msgs += [
            f"{c.name}: {c.pessimistic:.3g} {c.unit} vs limit {c.limit:.3g} ({c.note or 'fails'})"
            for c in base.checks if c.status == "fail"
        ]
        if res.repair and res.repair.plans:
            msgs.append("Cheapest fix: " + "; ".join(res.repair.plans[0].changes))
    elif base.feasibility == Feasibility.UNCERTAIN:
        state = "fail" if strict else "uncertain"
        msgs += [f"{c.name}: cannot be certified (best {c.optimistic:.3g}, worst {c.pessimistic:.3g} "
                 f"{c.unit}, limit {c.limit:.3g})" for c in base.checks if c.status == "uncertain"]
        msgs += base.notes
    cur = snapshot(res)
    if previous:
        b = p.budgets
        limits = {
            "energy_mj": tolerance_percent if tolerance_percent is not None
            else b.get("max_energy_increase_percent"),
            "latency_ms": b.get("max_latency_increase_percent", tolerance_percent),
        }
        for key, lim in limits.items():
            if lim is None or previous.get(key, 0) <= 0:
                continue
            change = (cur[key] / previous[key] - 1) * 100
            if change > lim:
                state = "fail"
                msgs.append(f"{key} regressed {change:+.1f}% (allowed {lim:+.1f}%): "
                            f"{previous[key]:.4g} -> {cur[key]:.4g}")
    return GateResult(state, state == "pass" or (state == "uncertain" and not strict), msgs, cur)


def _scale(v: float, lo: float, hi: float, unit: str) -> tuple[float, float, float, str]:
    """Show memory in MB / KB when it is small (microcontrollers)."""
    if unit == "GB" and v < 1.0:
        k, u = (1e3, "MB") if v >= 1e-3 else (1e6, "KB")
        return v * k, lo * k, hi * k, u
    return v, lo, hi, unit


def markdown_summary(p: Project, res: OptimisationResult, g: GateResult) -> str:
    """Compact PR-comment text."""
    icon = {"pass": "PASS", "uncertain": "UNCERTAIN", "fail": "FAIL"}[g.status]
    b = res.baseline
    L = [f"### Green Computer check: **{icon}**",
         f"`{p.model.name}` on `{p.hw.name}` at {p.spec.workload.fps:g} fps, {p.spec.workload.ambient_c:g} °C ambient", ""]  # fmt: skip
    if res.verdict:
        L += [res.verdict.summary, ""]
    L += ["| Metric | Value | Best-case to worst-case | Source |", "|---|---|---|---|"]
    for k, label in (("latency", "Latency"), ("capacity_fps", "Throughput"),
                     ("power_avg", "Power"), ("energy_per_inference", "Energy / inference"),
                     ("temperature", "Temperature"), ("memory", "Memory"),
                     ("accuracy", "Accuracy")):  # fmt: skip
        m = b.metrics[k]
        v, lo, hi, u = _scale(m.value, m.low, m.high, m.unit)
        L.append(f"| {label} | {v:.4g} {u} | {lo:.4g} to {hi:.4g} | {m.provenance.value} |")
    if g.messages:
        L += ["", "**Why:**", *[f"- {m}" for m in g.messages]]
    adv = [a for a in res.advice if a.in_recommended][:4] or res.advice[:4]
    if adv:
        L += ["", "**Suggested changes:**", *[f"- {a.title}: {a.gain}" for a in adv]]
    L += ["", "_Predictions from an analytical model (not measurements) unless a source says measured._"]
    return "\n".join(L)


def load_snapshot(path: str | Path) -> dict[str, float]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
