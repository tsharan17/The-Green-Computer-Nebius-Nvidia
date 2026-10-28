"""Design plans, cooling catalogue, exports (labels, content, validation, isolation)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
import yaml

from app.data_loader import clear_custom_devices
from app.main import create_app
from tests.helpers import AuthedClient, tiny_cnn_bytes

P = "/api/v1"
PNG_MAGIC = b"\x89PNG"
OCTET = {"Content-Type": "application/octet-stream"}


@pytest.fixture()
def app(tmp_path):
    a = create_app(f"sqlite:///{tmp_path / 'p.db'}", data_dir=str(tmp_path / "d"))
    yield a
    clear_custom_devices()


@pytest.fixture()
def c(app):
    with AuthedClient(app) as cl:
        cl.pid = cl.get(f"{P}/projects").json()[0]["id"]  # the robot example: overheats today
        yield cl


def _analysed(c):
    assert c.post(f"{P}/projects/{c.pid}/optimise").status_code == 200
    return c.get(f"{P}/projects/{c.pid}/plan-options").json()


def test_cooling_catalogue_is_ranked_by_stated_criteria(c):
    r = c.get(f"{P}/projects/{c.pid}/cooling").json()
    assert len(r["criteria"]) == 4 and r["rows"][0]["meets_requirements"]
    fitted = next(x for x in r["rows"] if x["id"] == "current")
    assert fitted["throttled"] and fitted["rating"] == "does not meet requirements"
    ok = [x for x in r["rows"] if x["meets_requirements"]]
    assert ok and all(x["rating"] in ("lower impact", "meets requirements") for x in ok)
    best = min(x["lifetime_kgco2e_per_year"] for x in ok)
    for x in ok:
        if x["rating"] == "lower impact":
            assert x["lifetime_kgco2e_per_year"] <= best * 1.1 + 1e-9 and "kgCO2e/year" in x["explanation"]
    active = next(x for x in r["rows"] if x["id"] == "edge-heatsink-fan")
    assert active["active_w"] == pytest.approx(0.8) and active["source"] and active["confidence"] in ("low", "medium")
    assert all(x["id"] not in ("mcu-bare-board", "dc-direct-liquid") for x in r["rows"])  # class filter


def test_plan_needs_an_analysis_and_lists_every_kind_of_change(c):
    r = c.get(f"{P}/projects/{c.pid}/plan-options")
    assert r.status_code == 422 and "analysis first" in r.json()["detail"]
    opts = _analysed(c)
    kinds = {x["kind"] for x in opts["changes"]}
    assert {"config", "design", "cooling"} <= kinds
    assert opts["recommended"] and all(i in {x["id"] for x in opts["changes"]} for i in opts["recommended"])


def test_plan_analysis_compares_baseline_each_change_and_combined(c):
    opts = _analysed(c)
    a = c.post(f"{P}/projects/{c.pid}/plans/analyse", json={"change_ids": opts["recommended"]}).json()
    assert a["baseline"]["feasibility"] == "infeasible"
    assert a["plan"]["feasibility"] in ("predicted_feasible", "uncertain")
    assert len(a["singles"]) == len(opts["recommended"])
    changed = {p["name"] for p in a["diff"]["parameters"] if p["changed"]}
    assert changed and changed <= set(a["baseline"]["parameters"])
    energy = next(m for m in a["diff"]["metrics"] if m["name"] == "energy_per_inference")
    assert energy["change_percent"] < 0 and energy["better"] is True
    assert "validate" in a["accuracy"].lower() or "measured" in a["accuracy"].lower()
    assert all(r["risk"] for r in a["risks"])
    bad = c.post(f"{P}/projects/{c.pid}/plans/analyse", json={"change_ids": ["nope"]})
    assert bad.status_code == 422


def test_plan_trace_draws_the_analysis_with_the_changes_applied(c):
    opts = _analysed(c)
    base = c.post(f"{P}/projects/{c.pid}/traces?which=baseline").json()
    cooler = next(x["id"] for x in opts["changes"] if x["kind"] == "cooling")
    t = c.post(f"{P}/projects/{c.pid}/plans/trace", json={"change_ids": [cooler]})
    assert t.status_code == 201, t.text
    t = t.json()
    assert t["trace"]["kind"] == "predicted" and t["label"].startswith("with: ")
    # the trace is drawn with the other cooler fitted, not today's
    assert t["trace"]["thermal"]["cooling_name"] != base["trace"]["thermal"]["cooling_name"]
    assert t["trace"]["thermal"]["unthrottled_c"] != base["trace"]["thermal"]["unthrottled_c"]
    r = c.get(f"{P}/projects/{c.pid}/traces/{t['id']}/figures/thermal-board")
    assert r.status_code == 200 and r.content[:4] == PNG_MAGIC
    assert c.post(f"{P}/projects/{c.pid}/plans/trace", json={"change_ids": ["nope"]}).status_code == 422


def test_hardware_swap_replaces_board_settings_and_design_items_are_directional(c):
    opts = _analysed(c)
    ids = {x["id"]: x for x in opts["changes"]}
    sel = [i for i in ids if i.startswith("design:")][:1]
    dev = [i for i in ids if i.startswith("device:")][:1]
    pm = [i for i in ("power_mode", "cooling") if i in ids][:1]
    a = c.post(f"{P}/projects/{c.pid}/plans/analyse", json={"change_ids": sel + dev + pm}).json()
    assert a["directional"] and any("directional" in n for n in a["applied"]["notes"])
    if dev and pm:
        assert pm[0] in a["applied"]["skipped"]
        assert any("replaced by the hardware swap" in n for n in a["applied"]["notes"])


def test_plans_are_versioned_immutable_and_private(c, app):
    opts = _analysed(c)
    body = {"change_ids": opts["recommended"], "name": "Ship it", "note": "first cut"}
    p1 = c.post(f"{P}/projects/{c.pid}/plans", json=body).json()
    p2 = c.post(f"{P}/projects/{c.pid}/plans", json=body | {"change_ids": opts["recommended"][:1]}).json()
    assert (p1["version"], p2["version"]) == (1, 2) and p1["analysis"]["changes"] != p2["analysis"]["changes"]
    assert [x["id"] for x in c.get(f"{P}/projects/{c.pid}/plans").json()] == [p2["id"], p1["id"]]
    assert c.get(f"{P}/projects/{c.pid}/plans/{p1['id']}").json()["analysis"]["summary"]
    assert "plan.saved" in {e["action"] for e in c.get(f"{P}/audit").json()}
    with AuthedClient(app, email="other@x.io") as o:
        assert o.get(f"{P}/projects/{c.pid}/plans/{p1['id']}").status_code == 404
        assert o.post(f"{P}/projects/{c.pid}/exports", json={"kind": "plan_json", "plan_id": p1["id"]}).status_code == 404


def test_exports_are_labelled_stored_and_downloadable(c):
    opts = _analysed(c)
    plan = c.post(f"{P}/projects/{c.pid}/plans", json={"change_ids": opts["recommended"], "name": "Export me"}).json()
    cat = {x["kind"]: x for x in c.get(f"{P}/projects/{c.pid}/exports/catalog").json()}
    assert cat["int8_model"]["available"] is False and "upload" in cat["int8_model"]["reason"].lower()
    assert cat["deployment_yaml"]["label"] == "template" and cat["plan_json"]["needs_plan"]

    def export(kind, plan_id=None):
        r = c.post(f"{P}/projects/{c.pid}/exports", json={"kind": kind, "plan_id": plan_id})
        assert r.status_code == 201, r.text
        row = r.json()
        dl = c.get(f"{P}/projects/{c.pid}/exports/{row['id']}/download")
        assert dl.status_code == 200 and dl.headers["x-green-computer-label"] == row["label"]
        return row, dl.content

    row, pdf = export("report_pdf", plan["id"])
    assert pdf.startswith(b"%PDF") and row["label"] == "generated"
    from pypdf import PdfReader

    text = "".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf)).pages)
    assert "Design plan: Export me" in text and "Outputs, baseline vs plan" in text
    _, pj = export("plan_json", plan["id"])
    doc = json.loads(pj)
    assert doc["schema"] == "green-computer.plan/1" and doc["plan"]["version"] == 1 and doc["analysis"]["diff"]["metrics"]
    _, csv = export("comparison_csv", plan["id"])
    assert csv.decode().splitlines()[0].startswith("section,name,unit,baseline")
    row, dep = export("deployment_yaml", plan["id"])
    assert row["label"] == "template" and yaml.safe_load(dep)["status"].startswith("TEMPLATE")
    _, hwc = export("hardware_changes_yaml", plan["id"])
    assert "no PCB" in yaml.safe_load(hwc)["status"]
    _, recs = export("recommendations_csv")
    assert recs.decode().startswith("id,tier,title")
    _, pkg = export("plan_package_zip", plan["id"])
    z = zipfile.ZipFile(io.BytesIO(pkg))
    man = json.loads(z.read("MANIFEST.json"))
    labels = {f["path"]: f["label"] for f in man["files"]}
    assert any(p.endswith(".pdf") for p in labels) and any(v == "template" for v in labels.values())
    assert set(labels) <= set(z.namelist())
    r = c.post(f"{P}/projects/{c.pid}/exports", json={"kind": "plan_json"})
    assert r.status_code == 422 and "plan" in r.json()["detail"]
    r = c.post(f"{P}/projects/{c.pid}/exports", json={"kind": "int8_model"})
    assert r.status_code == 422 and "library models cannot be transformed" in r.json()["detail"]
    hist = c.get(f"{P}/projects/{c.pid}/exports").json()
    assert len(hist) == 7 and all(h["sha256"] for h in hist)
    assert c.get(f"{P}/usage").json()["this_period"]["export"] == 7


def test_int8_model_is_exported_only_when_it_runs_and_agrees(c):
    up = c.post(f"{P}/models/upload?filename=net.onnx", content=tiny_cnn_bytes(random_weights=True),
                headers=OCTET).json()  # fmt: skip
    spec = c.get(f"{P}/projects/{c.pid}").json()["spec"] | {"model_id": up["profile"]["id"]}
    assert c.put(f"{P}/projects/{c.pid}", json=spec).status_code == 200
    c.post(f"{P}/projects/{c.pid}/optimise")
    r = c.post(f"{P}/projects/{c.pid}/exports", json={"kind": "int8_model"})
    assert r.status_code == 201, r.text
    row = r.json()
    v = row["validation"]
    assert row["label"] == "generated_validated" and v["passed"] and v["worst_cosine"] >= 0.99
    assert "NOT measured" in v["not_measured"]
    blob = c.get(f"{P}/projects/{c.pid}/exports/{row['id']}/download").content
    import onnx

    m = onnx.load_model_from_string(blob)
    assert any(n.op_type in ("DynamicQuantizeLinear", "MatMulInteger", "ConvInteger", "DequantizeLinear")
               for n in m.graph.node)  # fmt: skip
    assert c.get(f"{P}/usage").json()["this_period"]["execution_seconds"] > 0
