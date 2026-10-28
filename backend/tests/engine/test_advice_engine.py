"""Day 5 engine: pruning, model swaps, repair, verdict, what-ifs, advice, recipe, report."""

import json
import py_compile
import re
import subprocess

import pytest

from app.data_loader import get_device, get_model, load_devices, load_models
from app.engine.advisor import build_advice
from app.engine.alternatives import device_alternatives, model_alternatives
from app.engine.changes import effort
from app.engine.evaluator import evaluate, matches
from app.engine.optimise import _rank_key, make_evaluator, optimise
from app.engine.recipe import build_recipe
from app.engine.report import build_report_md, md_to_html
from app.engine.roofline import roofline_at
from app.engine.verdict import build_verdict
from app.engine.whatif import SCENARIOS, limiting_factor, make_whatifs
from app.schemas.measurement import MeasurementIn
from app.schemas.metrics import Feasibility, Provenance
from app.schemas.project import Constraints, ProjectSpec, Workload
from app.schemas.results import Config
from app.services.advisor_service import enrich  # noqa: F401  (import check)

NX = get_device("jetson-orin-nx")
S = get_model("yolov8s")


def spec(model="yolov8s", dev="jetson-orin-nx", **kw):
    d = get_device(dev)
    return ProjectSpec(name="t", model_id=model, device_id=dev, cooling_id=d.default_cooling,
                       constraints=Constraints(max_latency_ms=100), **kw)  # fmt: skip


def analyse(model="yolov8s", dev="jetson-orin-nx", **kw):
    sp = spec(model, dev, **kw)
    m, hw = get_model(model), get_device(dev)
    res = optimise(sp, m, hw)
    ev = make_evaluator(m, hw, sp.workload, sp.constraints)

    def ev_hw(h, cfg):
        return evaluate(m, h, cfg, sp.workload, sp.constraints)

    base_cfg = res.baseline.config
    best_cfg = res.best.config if res.best else base_cfg
    res.whatifs = make_whatifs(ev_hw, hw, best_cfg, base_cfg)
    res.limiting_factor = limiting_factor(ev_hw, hw, base_cfg)
    models, devices = list(load_models().values()), list(load_devices().values())
    res.alternatives = [
        *model_alternatives(sp, m, hw, models, res.baseline, None, res.best),
        *device_alternatives(sp, m, hw, devices, res.baseline, None, res.best),
    ]
    res.advice = build_advice(ev, hw, res.baseline, res.best, res.alternatives, res.whatifs,
                              res.limiting_factor)  # fmt: skip
    return sp, m, hw, res


@pytest.fixture(scope="module")
def example():
    return analyse()


# --- pruning and model swaps -----------------------------------------------------------------


def test_pruning_shrinks_compute_and_memory_and_needs_measuring():
    base = roofline_at(S, NX, 1.0, 1.0, "fp16", 1)
    pruned = roofline_at(S, NX, 1.0, 1.0, "fp16", 1, sparsity=0.4)
    assert pruned.t_batch_s < base.t_batch_s and pruned.mem_gb < base.mem_gb
    cfg = Config(precision="fp16", batch=1, power_mode_id="25W", cooling_id="fan_heatsink", sparsity=0.1)
    r = evaluate(S, NX, cfg, Workload(), Constraints())
    full = evaluate(S, NX, cfg.model_copy(update={"sparsity": 0.0}), Workload(), Constraints())
    assert r.metrics["accuracy"].value < full.metrics["accuracy"].value
    assert r.feasibility == Feasibility.UNCERTAIN and any("fine-tun" in n for n in r.notes)
    meas = MeasurementIn(device_id=NX.id, model_id="yolov8s", precision="fp16", batch=1,
                         power_mode_id="25W", latency_ms_mean=5)  # fmt: skip
    assert not matches(meas, NX, "yolov8s", cfg)  # a measurement of the unpruned model is not this point


def test_model_swap_accuracy_uses_published_ratio():
    cfg = Config(precision="fp32", batch=1, power_mode_id="MAXN", cooling_id="fan_heatsink")
    n = evaluate(get_model("yolov8n"), NX, cfg, Workload(), Constraints(), accuracy_reference=S.baseline_accuracy)
    assert n.metrics["accuracy"].value == pytest.approx(0.373 / 0.449, rel=1e-6)
    assert n.metrics["accuracy"].provenance == Provenance.VENDOR_SPEC
    assert next(c for c in n.checks if c.name == "accuracy").status == "fail"  # 83% < 90%
    mob = evaluate(get_model("mobilenet-v2"), NX, cfg, Workload(), Constraints(),
                   accuracy_reference=get_model("resnet-50").baseline_accuracy)  # fmt: skip
    assert next(c for c in mob.checks if c.name == "accuracy").status == "pass"  # 94.5% >= 90%


# --- repair ----------------------------------------------------------------------------------


def test_repair_ranks_certified_fixes_by_effort_not_energy(example):
    _, _, _, res = example
    rep = res.repair
    assert rep.needed and rep.plans
    efforts = [p.effort_score for p in rep.plans]
    assert efforts == sorted(efforts)
    for p in rep.plans:
        assert p.feasibility in (Feasibility.PREDICTED_FEASIBLE, Feasibility.VERIFIED_FEASIBLE)
        assert p.changes and p.fixes  # it changes something and says what it fixes
        assert p.effort_score == effort(res.baseline.config, p.config)
    assert "throughput" in rep.plans[0].fixes or "no sustained throttling" in rep.plans[0].fixes


def test_repair_not_needed_when_baseline_already_works():
    sp, m, hw, res = analyse("mobilenet-v2", "jetson-agx-orin")
    assert res.baseline.feasibility in (Feasibility.PREDICTED_FEASIBLE, Feasibility.VERIFIED_FEASIBLE)
    assert res.repair.needed is False and not res.repair.plans


def test_repair_reports_binding_constraints_and_never_relaxes():
    sp = ProjectSpec(name="t", model_id="yolov8m", device_id="jetson-orin-nano",
                     workload=Workload(fps=600))  # fmt: skip
    res = optimise(sp, get_model("yolov8m"), get_device("jetson-orin-nano"))
    assert res.repair.needed and not res.repair.plans
    assert "throughput" in res.repair.binding_constraints
    assert "not relaxed" in res.repair.message
    assert sp.constraints.max_power_w == 55 and sp.constraints.min_accuracy == 0.9


# --- verdict ---------------------------------------------------------------------------------


def test_verdict_speaks_plainly_and_quantifies_savings(example):
    sp, _, _, res = example
    v = res.verdict
    assert not v.baseline.ok and "Overheats" in v.baseline.headline
    assert v.improved.ok and v.improved.headline == "Meets every requirement"
    assert v.savings.power_percent > 40 and v.savings.energy_percent > 40
    assert v.savings.carbon_kg_per_year > 0
    assert "fails" in v.summary and "less power" in v.summary
    assert v.fps_required == sp.workload.fps


def test_verdict_wording_for_an_uncertain_baseline():
    cfg = Config(precision="int8", batch=1, power_mode_id="30W", cooling_id="fan_heatsink")
    r = evaluate(S, get_device("jetson-agx-orin"), cfg, Workload(), Constraints())
    assert r.feasibility == Feasibility.UNCERTAIN
    v = build_verdict(r, None, Workload(), Constraints())
    assert not v.baseline.ok and "needs a real measurement" in v.baseline.headline


# --- what-ifs --------------------------------------------------------------------------------


def test_whatifs_change_one_parameter_and_report_elasticities(example):
    _, _, _, res = example
    by = {w.id: w for w in res.whatifs}
    assert set(by) <= {s.id for s in SCENARIOS} and len(by) >= 6
    assert by["idle"].energy_change_percent < 0  # less idle power -> less energy
    assert by["compute"].elasticity_latency <= 0 and by["bandwidth"].elasticity_latency <= 0
    for w in res.whatifs:
        assert w.verify_with and w.factor > 0
    energies = [w.energy_change_percent for w in res.whatifs]
    assert energies == sorted(energies)  # ranked: biggest saving first


def test_limiting_factor_names_what_holds_the_workload_back(example):
    assert example[3].limiting_factor.startswith("heat")
    _, _, _, res = analyse("resnet-50", "jetson-orin-nano")
    assert res.limiting_factor.split(":")[0] in {
        "compute throughput", "memory bandwidth", "idle power", "heat",
        "fixed per-layer overheads"}  # fmt: skip


# --- advice ----------------------------------------------------------------------------------


def test_advice_covers_model_settings_hardware_and_design_levels(example):
    _, _, _, res = example
    tiers = {a.tier for a in res.advice}
    assert {"model", "configure", "design"} <= tiers
    for a in res.advice:
        assert a.why and a.action and a.gain and a.confidence_reason
        assert a.confidence in ("high", "medium", "low") and a.effort in ("low", "medium", "high")
    order = [a.tier for a in res.advice]
    rank = {"model": 0, "software": 1, "configure": 2, "select": 3, "design": 4}
    assert [rank[t] for t in order] == sorted(rank[t] for t in order)


def test_precision_advice_shows_contribution_and_what_it_fixes(example):
    a = next(x for x in example[3].advice if x.id == "precision")
    assert a.tier == "model" and a.in_recommended and "FP16" in a.title
    assert a.effect["energy_saving_percent"] > 20 and "throughput" in a.fixes
    assert a.confidence == "medium"  # analytical, fp16 accuracy is not capped


def test_unmeasured_accuracy_changes_get_low_confidence():
    sp, m, hw, res = analyse("yolov8m", "jetson-orin-nano")
    risky = [a for a in res.advice if a.id in ("precision", "pruning", "resolution")
             and "estimate" in a.confidence_reason]  # fmt: skip
    assert risky and all(a.confidence == "low" for a in risky)


def test_design_advice_is_labelled_a_low_confidence_what_if(example):
    design = [a for a in example[3].advice if a.tier == "design"]
    assert design
    for a in design:
        assert a.confidence == "low" and a.effort == "high" and not a.in_recommended
        assert "silicon" in " ".join(a.validate_steps) or "synthes" in " ".join(a.validate_steps)


def test_only_lighter_models_are_recommended_and_compared_with_the_plan(example):
    _, _, _, res = example
    names = [a.title for a in res.advice if a.tier == "model" and a.id.startswith("model:")]
    assert not any("YOLOv8m" in n for n in names)  # heavier than YOLOv8s: never "lighter"
    swaps = {a.id: a for a in res.alternatives if a.kind == "model"}
    assert swaps["yolov8m"].lighter is False and swaps["yolov8n"].lighter is True
    assert swaps["yolov8n"].feasibility in (Feasibility.INFEASIBLE, Feasibility.VERIFIED_INFEASIBLE)
    assert all(a.compared_with == "your recommended setup" for a in res.alternatives)


def test_effort_enters_the_ranking_as_a_small_penalty(example):
    _, _, _, res = example
    r, base = res.best, res.baseline.config
    near = _rank_key(r, r.config)  # zero effort away from itself
    far = _rank_key(r, base)
    assert far[1] >= near[1]  # more effort never ranks better


# --- recipe ----------------------------------------------------------------------------------


def test_recipe_files_are_valid_and_carry_the_recommended_settings(example, tmp_path):
    sp, m, hw, res = example
    files, warnings = build_recipe(sp, m, hw, res)
    assert {"README.md", "optimise_model.py", "build_engine.sh", "set_power_mode.sh", "verify.sh",
            "runtime_config.json", "hardware_design_notes.md"} <= set(files)  # fmt: skip
    assert warnings and "not run" in warnings[0].lower()
    py = tmp_path / "optimise_model.py"
    py.write_text(files["optimise_model.py"].content, encoding="utf-8")
    py_compile.compile(str(py), doraise=True)
    for n in ("build_engine.sh", "set_power_mode.sh", "verify.sh"):
        ok = subprocess.run(["bash", "-n"], input=files[n].content, text=True, capture_output=True)
        assert ok.returncode == 0, (n, ok.stderr)
    cfg = json.loads(files["runtime_config.json"].content)
    assert cfg["config"]["precision"] == res.best.config.precision == "fp16"
    assert cfg["predicted"]["power_avg"]["source"] == "analytical"
    assert "--fp16" in files["build_engine.sh"].content
    assert "K/W" in files["hardware_design_notes.md"].content
    assert "nvpmodel" in files["set_power_mode.sh"].content and "Generated, not run" in files["set_power_mode.sh"].content


def test_recipe_for_a_non_jetson_device_has_no_jetson_scripts_and_warns_when_nothing_works():
    sp = spec("resnet-50", "host-cpu")
    res = optimise(sp, get_model("resnet-50"), get_device("host-cpu"))
    res.whatifs, res.limiting_factor = [], None
    files, warnings = build_recipe(sp, get_model("resnet-50"), get_device("host-cpu"), res)
    assert "build_engine.sh" not in files and "set_power_mode.sh" not in files

    bad = ProjectSpec(name="t", model_id="yolov8m", device_id="jetson-orin-nano", workload=Workload(fps=600))
    r2 = optimise(bad, get_model("yolov8m"), get_device("jetson-orin-nano"))
    r2.whatifs, r2.limiting_factor = [], None
    _, w2 = build_recipe(bad, get_model("yolov8m"), get_device("jetson-orin-nano"), r2)
    assert any("No configuration met every requirement" in w for w in w2)


# --- report ----------------------------------------------------------------------------------


def test_report_contains_every_section_and_only_engine_numbers(example):
    sp, m, hw, res = example
    md = build_report_md(sp, m, hw, res)
    for h in ("## 1. The question", "## 2. The verdict", "## 3. The cheapest fix",
              "## 4. Recommendations", "## 5. Other models and hardware",
              "## 6. What limits this workload", "## 7. How sure are we?", "## 8. Assumptions"):  # fmt: skip
        assert h in md
    assert f"{res.verdict.improved.power_w:.1f} W" in md  # the engine's number, verbatim
    assert "Change the AI model" in md and "Redesign the chip or board" in md
    assert not re.search(r"nan", md.lower()) and "None" not in md
    html = md_to_html(md)
    assert "<table>" in html and "<h2>" in html and "<script" not in html.lower()


def test_html_escapes_markup():
    h = md_to_html("# <b>x</b>\n\n- **bold** <script>alert(1)</script>")
    assert "<script>" not in h and "&lt;script&gt;" in h and "<strong>bold</strong>" in h
