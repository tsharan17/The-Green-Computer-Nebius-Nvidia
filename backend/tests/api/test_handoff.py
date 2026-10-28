"""Release 2 hand-over files: summary, full engineering report, graph, evidence, reproducibility
manifest (with the offline verifier) and the handoff package."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.data_loader import clear_custom_devices
from app.main import create_app
from tests.helpers import AuthedClient, tiny_cnn_bytes

P = "/api/v1"
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def c(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'h.db'}", data_dir=str(tmp_path / "d"))
    with AuthedClient(app) as cl:
        cl.pid = cl.get(f"{P}/projects").json()[0]["id"]  # the robot example
        yield cl
    clear_custom_devices()
    app.state.engine.dispose()


def _export(c, kind: str, plan_id: int | None = None) -> tuple[dict, bytes]:
    r = c.post(f"{P}/projects/{c.pid}/exports", json={"kind": kind, "plan_id": plan_id})
    assert r.status_code == 201, r.text
    e = r.json()
    data = c.get(f"{P}/projects/{c.pid}/exports/{e['id']}/download").content
    assert hashlib.sha256(data).hexdigest() == e["sha256"]
    return e, data


def _plan(c) -> int:
    opts = c.get(f"{P}/projects/{c.pid}/plan-options").json()
    return c.post(f"{P}/projects/{c.pid}/plans", json={"change_ids": opts["recommended"], "name": "handoff"}).json()["id"]


def test_catalogue_and_optimised_models_are_honest(c):
    kinds = {x["kind"]: x for x in c.get(f"{P}/projects/{c.pid}/exports/catalog").json()}
    for k in ("founder_md", "founder_pdf", "engineering_full_md", "engineering_full_pdf", "system_graph_json",
              "evidence_zip", "manifest_json", "handoff_zip"):  # fmt: skip
        assert k in kinds and kinds[k]["available"] is False and "analysis first" in kinds[k]["reason"]
    assert kinds["handoff_zip"]["needs_plan"] is True
    opt = {o["kind"]: o for o in c.get(f"{P}/exports/optimised-models").json()}
    assert set(opt) == {"int8_model", "fp16_model", "pruned_model", "tensorrt_engine"}
    assert all(o["status"] == "not offered" for k, o in opt.items() if k != "int8_model") and all(o["how"] for o in opt.values())


def test_reports_graph_and_evidence(c):
    assert c.post(f"{P}/projects/{c.pid}/optimise").status_code == 200
    e, md = _export(c, "founder_md")
    text = md.decode()
    assert e["label"] == "generated" and "## The answer" in text and "## How sure we are" in text
    assert "nothing has been measured on your device yet" in text and "## What we recommend" in text
    _, pdf = _export(c, "founder_pdf")
    assert pdf.startswith(b"%PDF")
    _, full = _export(c, "engineering_full_md")
    full_t = full.decode()
    for h in ("# Appendices", "## A. Hardware system graph", "## B. Thermal attribution", "## C. Runtime effects",
              "## D. Evidence for each headline number", "### Calibration quality"):  # fmt: skip
        assert h in full_t, h
    assert "attribution" in full_t and "lumped" in full_t.lower()
    _, fpdf = _export(c, "engineering_full_pdf")
    assert fpdf.startswith(b"%PDF") and len(fpdf) > len(pdf)
    _, g = _export(c, "system_graph_json")
    assert json.loads(g)["graph"]["schema_id"] == "green-computer.system-graph/1"
    _, z = _export(c, "evidence_zip")
    names = set(zipfile.ZipFile(io.BytesIO(z)).namelist())
    assert {"README.md", "evidence.json", "measurements.json", "calibration_history.json", "device_runs.json",
            "system_graph.json", "analysis.json", "physics_today.json"} <= names  # fmt: skip


def test_reproducibility_manifest_round_trip_and_tampering(c, tmp_path):
    assert c.post(f"{P}/projects/{c.pid}/optimise").status_code == 200
    e, raw = _export(c, "manifest_json")
    doc = json.loads(raw)
    assert doc["schema"] == "green-computer.reproducibility/1" and doc["engine"]["version"]
    assert doc["expected"]["baseline"]["latency"] > 0 and doc["stored_analysis_differs"] is False
    assert set(doc["hashes"]) >= {"spec", "model_profile", "hardware_profile", "calibrations", "measurements", "cooling_catalogue"}
    assert doc["software"]["packages"]["numpy"]
    ok = c.post(f"{P}/reproduce/verify", json=doc).json()
    assert ok["ok"] and ok["compared"] >= 10 and ok["problems"] == []
    # A browser re-serialises 40.0 as 40: the same manifest after a JavaScript round trip still verifies.
    js = json.loads(json.dumps(doc), parse_float=lambda x: int(float(x)) if float(x).is_integer() else float(x))
    assert c.post(f"{P}/reproduce/verify", json=js).json()["ok"]

    bad = copy.deepcopy(doc)
    bad["inputs"]["spec"]["workload"]["ambient_c"] += 15
    out = c.post(f"{P}/reproduce/verify", json=bad).json()
    assert not out["ok"] and any("does not match its hash" in p for p in out["problems"])
    assert any(p.startswith("baseline.temperature") for p in out["problems"])
    old = {**doc, "engine": {**doc["engine"], "version": "0.0.1"}}
    assert any("engine version differs" in p for p in c.post(f"{P}/reproduce/verify", json=old).json()["problems"])
    assert c.post(f"{P}/reproduce/verify", json={"schema": "x"}).json()["ok"] is False

    # The offline script gives the same answer, with an exit code.
    f = tmp_path / "m.json"
    f.write_text(raw.decode(), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "reproduce.py"), str(f)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and r.stdout.startswith("Reproduced"), r.stdout + r.stderr
    f.write_text(json.dumps(bad), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "reproduce.py"), str(f)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 1 and "NOT reproduced" in r.stdout


def test_handoff_package_is_complete_labelled_and_hashed(c):
    up = c.post(f"{P}/intake/upload?filename=tiny.onnx&project_id={c.pid}", content=tiny_cnn_bytes(random_weights=True),
                headers={"Content-Type": "application/octet-stream"}).json()  # fmt: skip
    assert up["status"] == "valid"
    spec = c.get(f"{P}/projects/{c.pid}").json()["spec"]
    spec["model_id"] = up["model"]["id"]
    assert c.put(f"{P}/projects/{c.pid}", json=spec).status_code == 200
    assert c.post(f"{P}/projects/{c.pid}/optimise").status_code == 200
    assert c.post(f"{P}/projects/{c.pid}/exports", json={"kind": "handoff_zip"}).status_code == 422  # needs a plan
    int8, _ = _export(c, "int8_model")
    assert int8["label"] == "generated_validated"
    pid = _plan(c)
    e, z = _export(c, "handoff_zip", pid)
    zf = zipfile.ZipFile(io.BytesIO(z))
    names = set(zf.namelist())
    man = json.loads(zf.read("MANIFEST.json"))
    assert man["schema"] == "green-computer.handoff/1" and man["plan"]["id"] == pid
    for f in man["files"]:
        assert f["path"] in names and hashlib.sha256(zf.read(f["path"])).hexdigest() == f["sha256"], f["path"]
        assert f["label_text"]
    folders = {n.split("/")[0] for n in names}
    assert {"reports", "plan", "hardware", "data", "evidence", "recipe", "models"} <= folders
    labels = {f["path"].split("/")[0]: f["label"] for f in man["files"]}
    assert labels["recipe"] == "template" and labels["models"] == "generated_validated"
    assert {o["kind"] for o in man["optimised_models_not_included"]} == {"fp16_model", "pruned_model", "tensorrt_engine"}
    readme = zf.read("README.md").decode()
    assert "Not included" in readme and "no PCB, RTL or chip file" in readme
    assert any(n.endswith("-reproducibility.json") for n in names)
    with AuthedClient(c.app, email="other@x.io") as o:
        assert o.post(f"{P}/projects/{c.pid}/exports", json={"kind": "handoff_zip", "plan_id": pid}).status_code == 404
