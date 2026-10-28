"""System graph API: uploads of design files, versions, diff, save, trace links, tenancy, deletion."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.data_loader import clear_custom_devices
from app.main import create_app
from tests.helpers import TEST_PASSWORD, AuthedClient

P = "/api/v1"
OCTET = {"Content-Type": "application/octet-stream"}
F = Path(__file__).resolve().parent.parent / "fixtures" / "design"


@pytest.fixture()
def app(tmp_path):
    a = create_app(f"sqlite:///{tmp_path / 'sg.db'}", data_dir=str(tmp_path / "d"))
    yield a
    clear_custom_devices()
    a.state.engine.dispose()


def _fpga_project(c) -> int:
    return c.post(f"{P}/projects", json={"name": "fpga", "model_id": "tiny-vww-mobilenet",
                                         "device_id": "xilinx-kria-kv260"}).json()["id"]  # fmt: skip


def _upload(c, name: str, pid: int | None = None, data: bytes | None = None) -> dict:
    q = f"{P}/intake/upload?filename={name}" + (f"&project_id={pid}" if pid else "")
    r = c.post(q, content=data if data is not None else (F / name).read_bytes(), headers=OCTET)
    assert r.status_code == 200, r.text
    return r.json()


def test_design_uploads_are_parsed_and_labelled_as_metadata(app):
    with AuthedClient(app) as c:
        pid = _fpga_project(c)
        sch = _upload(c, "camera.kicad_sch", pid)
        assert sch["status"] == "valid" and sch["kind"] == "kicad_schematic" and "Metadata only" in sch["message"]
        assert sch["report"]["parts_total"] == 5
        pcb = _upload(c, "camera.kicad_pcb", pid)
        assert pcb["status"] == "valid" and "70 × 50 mm" in pcb["message"] and "nothing was simulated" in pcb["message"]
        v = _upload(c, "accel.v", pid)
        assert v["status"] == "valid" and "not simulated" in v["message"] and v["warnings"]
        t = _upload(c, "timing.rpt", pid)
        assert t["status"] == "valid" and t["kind"] == "vendor_report" and "according to Vivado" in t["message"]
        h = _upload(c, "conv_top_csynth.rpt", pid)
        assert h["status"] == "valid" and h["kind"] == "hls_report" and "HLS estimate" in h["message"]
        # Bad files: a recognised format with wrong content is refused, recorded, and not stored.
        bad = _upload(c, "broken.kicad_sch", pid, b"(kicad_sch (version 1) (symbol")
        assert bad["status"] == "invalid" and "truncated" in bad["message"]
        old = _upload(c, "legacy.kicad_sch", pid, b"EESchema Schematic File Version 4\n")
        assert old["status"] == "invalid" and "KiCad 6" in old["message"]
        vhdl = _upload(c, "core.vhd", pid, b"entity m is end m;")
        assert vhdl["status"] == "unsupported" and "Vivado" in vhdl["required"]
        arts = {a["id"]: a for a in c.get(f"{P}/artifacts").json()}
        assert not arts[bad["artifact_id"]]["stored"] and arts[sch["artifact_id"]]["stored"]


def test_graph_versions_build_diff_save_and_trace_links(app):
    with AuthedClient(app) as c:
        pid = _fpga_project(c)
        g0 = c.get(f"{P}/projects/{pid}/system-graph").json()
        assert g0["version"] == 0 and g0["saved"] is False and g0["links"] is None
        assert {x["id"] for x in g0["graph"]["components"]} >= {"compute", "ram", "power", "cooling"}

        ids = [_upload(c, n, pid)["artifact_id"] for n in ("accel.v", "timing.rpt", "conv_top_csynth.rpt", "camera.kicad_pcb")]
        src = c.get(f"{P}/projects/{pid}/system-graph/sources").json()
        assert {s["id"] for s in src} >= set(ids) and {s["tool"] for s in src} >= {"verilog", "vivado_timing", "hls_report", "kicad_pcb"}

        v1 = c.post(f"{P}/projects/{pid}/system-graph/from-files", json={"artifact_ids": ids[:2]})
        assert v1.status_code == 201, v1.text
        assert v1.json()["version"] == 1 and v1.json()["source_artifacts"] == ids[:2]
        v2 = c.post(f"{P}/projects/{pid}/system-graph/from-files", json={"artifact_ids": ids[2:], "note": "board"}).json()
        assert v2["version"] == 2 and v2["note"] == "board"
        cur = c.get(f"{P}/projects/{pid}/system-graph").json()
        assert cur["version"] == 2 and cur["saved"] and not cur["outdated"]
        comps = {x["id"]: x for x in cur["graph"]["components"]}
        assert "rtl:conv_top" in comps and "timing_wns_ns" in comps["compute"]["attributes"]  # v1 facts carried into v2
        assert comps["part:U1"]["physical"]["x_mm"] == 130.0

        vers = c.get(f"{P}/projects/{pid}/system-graph/versions").json()
        assert [v["version"] for v in vers] == [2, 1] and "graph" not in vers[0]
        assert c.get(f"{P}/projects/{pid}/system-graph/1").json()["version"] == 1
        assert c.get(f"{P}/projects/{pid}/system-graph/9").status_code == 404
        d = c.get(f"{P}/projects/{pid}/system-graph/diff", params={"from": 1, "to": 2}).json()
        assert "part:U1" in d["components_added"] and not d["components_removed"]
        assert c.get(f"{P}/projects/{pid}/system-graph/diff", params={"from": 0, "to": 7}).status_code == 404

        # Save an edit as a new version; structural errors and a foreign device are refused.
        g = cur["graph"]
        g["components"].append({"id": "sensor", "name": "IMX219 camera", "kind": "sensor", "parent": "system",
                                "origin": "user", "confidence": "stated"})  # fmt: skip
        g["connections"].append({"id": "sensor-compute", "source": "sensor", "target": "compute", "kind": "data",
                                 "origin": "user", "confidence": "stated"})  # fmt: skip
        v3 = c.post(f"{P}/projects/{pid}/system-graph", json={"graph": g, "note": "added the camera"})
        assert v3.status_code == 201 and v3.json()["version"] == 3
        bad = {**g, "connections": [*g["connections"], {"id": "x", "source": "sensor", "target": "ghost", "kind": "data",
                                                         "origin": "user", "confidence": "stated"}]}  # fmt: skip
        r = c.post(f"{P}/projects/{pid}/system-graph", json={"graph": bad})
        assert r.status_code == 422 and "both ends" in r.json()["detail"]
        r = c.post(f"{P}/projects/{pid}/system-graph", json={"graph": {**g, "device_id": "jetson-orin-nano"}})
        assert r.status_code == 422 and "project uses" in r.json()["detail"]
        r = c.post(f"{P}/projects/{pid}/system-graph/from-files", json={"artifact_ids": [999999]})
        assert r.status_code == 422 and "not found" in r.json()["detail"]
        sch_bad = _upload(c, "x.kicad_sch", pid, b"nope")
        r = c.post(f"{P}/projects/{pid}/system-graph/from-files", json={"artifact_ids": [sch_bad["artifact_id"]]})
        assert r.status_code == 422

        # A trace links its events to the graph's components.
        assert c.post(f"{P}/projects/{pid}/traces?which=baseline").status_code == 201
        links = c.get(f"{P}/projects/{pid}/system-graph").json()["links"]
        assert links["kind"] == "predicted" and links["components"]["compute"]["events"] > 0


def test_graph_is_private_to_its_workspace_and_deleted_with_it(app):
    with AuthedClient(app, email="owner@x.io") as a:
        pid = _fpga_project(a)
        aid = _upload(a, "accel.v", pid)["artifact_id"]
        assert a.post(f"{P}/projects/{pid}/system-graph/from-files", json={"artifact_ids": [aid]}).status_code == 201
    with AuthedClient(app, email="other@x.io") as b:
        for path in ("system-graph", "system-graph/versions", "system-graph/1", "system-graph/sources"):
            assert b.get(f"{P}/projects/{pid}/{path}").status_code == 404, path
        assert b.post(f"{P}/projects/{pid}/system-graph/from-files", json={"artifact_ids": [aid]}).status_code == 404
        mine = _fpga_project(b)
        r = b.post(f"{P}/projects/{mine}/system-graph/from-files", json={"artifact_ids": [aid]})
        assert r.status_code == 422 and "not found" in r.json()["detail"]  # another workspace's file
        assert b.get(f"{P}/projects/{mine}/system-graph/sources").json() == []
    with TestClient(app) as anon:
        assert anon.get(f"{P}/projects/{pid}/system-graph").status_code == 401
    with AuthedClient(app, email="owner@x.io") as a:
        assert a.post(f"{P}/auth/account/delete", json={"password": TEST_PASSWORD, "confirm": "DELETE"}).status_code == 200
    with app.state.engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM system_graphs WHERE project_id = :p"), {"p": pid}).scalar() == 0
