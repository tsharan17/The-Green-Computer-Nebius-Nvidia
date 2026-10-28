"""Cooling catalogue v2: applicability where the device runs, sustainability, per-heat-source rationale."""

from __future__ import annotations

import pytest

from app.data_loader import clear_custom_devices, get_device, load_cooling_catalogue
from app.engine.cooling_catalogue import blockers, cautions, heat_rationale, sustainability
from app.main import create_app
from app.schemas.project import Environment
from tests.helpers import AuthedClient

P = "/api/v1"


def _entry(eid: str):
    return next(e for e in load_cooling_catalogue() if e.id == eid)


def test_every_catalogue_entry_carries_v2_fields():
    for e in load_cooling_catalogue():
        assert e.targets and set(e.targets) <= {"compute", "memory", "power"}, e.id
        assert e.heat_w_max is not None and e.repairability in ("high", "medium", "low"), e.id


def test_blockers_follow_the_environment():
    fan = _entry("edge-heatsink-fan")
    assert blockers(fan, Environment(), 10.0) == []
    sealed = blockers(fan, Environment(sealed_enclosure=True), 10.0)
    assert sealed and "sealed" in sealed[0]
    assert any("dBA" in r for r in blockers(fan, Environment(max_noise_dba=20), 10.0))
    assert any("mm tall" in r for r in blockers(_entry("edge-heatpipe-tower"), Environment(max_height_mm=30), 10.0))
    assert any("sized for up to" in r for r in blockers(_entry("edge-passive-extrusion"), Environment(), 25.0))
    dc = _entry("dc-direct-liquid")
    assert len(blockers(dc, Environment(), 300.0)) == 2  # needs a rack and facility water
    assert blockers(dc, Environment(datacenter=True, facility_water=True), 300.0) == []
    # A sealed box still allows parts that need no airflow.
    assert blockers(_entry("tim-upgrade"), Environment(sealed_enclosure=True), 10.0) == []


def test_cautions_sustainability_and_rationale():
    assert any("dusty" in c for c in cautions(_entry("edge-heatsink-fan"), Environment(dusty=True)))
    assert any("glycol" in c for c in cautions(_entry("edge-liquid-loop"), Environment()))
    s = sustainability(_entry("dc-server-air"))
    assert s["water_l_per_year"] is None and "not estimated" in s["water_note"]
    assert sustainability(None)["embodied_note"].startswith("Already fitted")
    rat = heat_rationale({"compute": 15.0, "memory": 0.6, "power": 3.5}, [_entry("edge-passive-large"), _entry("enclosure-venting")])
    by = {r["source"]: r for r in rat}
    assert by["compute"]["share"] == pytest.approx(15 / 19.1, rel=1e-3) and "Most of the heat" in by["compute"]["why"]
    assert by["power"]["reached_by"] == ["Vented enclosure (convection path)"] and "only airflow" in by["power"]["why"]
    assert all("attribution" in r["label"] for r in rat)


@pytest.fixture()
def c(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'c2.db'}", data_dir=str(tmp_path / "d"))
    with AuthedClient(app) as cl:
        cl.pid = cl.get(f"{P}/projects").json()[0]["id"]  # robot example on a Jetson Orin NX, too hot today
        yield cl
    clear_custom_devices()
    app.state.engine.dispose()


def test_cooling_report_v2_uses_the_projects_environment(c):
    r = c.get(f"{P}/projects/{c.pid}/cooling").json()
    assert r["catalogue_version"] == 2 and len(r["criteria"]) == 4
    assert {h["source"] for h in r["heat_sources"]} == {"compute", "memory", "power"}
    assert sum(h["share"] for h in r["heat_sources"]) == pytest.approx(1.0, abs=1e-3)
    fan = next(x for x in r["rows"] if x["id"] == "edge-heatsink-fan")
    assert fan["heat_share_reached"] == pytest.approx(1.0, abs=1e-3) and fan["sustainability"]["repairability"] == "medium"
    excl = {x["id"]: x for x in r["excluded"]}
    assert "dc-direct-liquid" in excl and "not this kind of device" in excl["dc-direct-liquid"]["reasons"][0]

    # A sealed, quiet box: fans and vents drop out, each with its reason; the answer changes honestly.
    spec = c.get(f"{P}/projects/{c.pid}").json()["spec"]
    spec["environment"] = {"sealed_enclosure": True, "max_noise_dba": 20, "dusty": True}
    assert c.put(f"{P}/projects/{c.pid}", json=spec).status_code == 200
    r2 = c.get(f"{P}/projects/{c.pid}/cooling").json()
    ids = {x["id"] for x in r2["rows"]}
    assert "edge-heatsink-fan" not in ids and "enclosure-venting" not in ids
    reasons = {x["id"]: " ".join(x["reasons"]) for x in r2["excluded"]}
    assert "sealed" in reasons["edge-heatsink-fan"] and "sealed" in reasons["enclosure-venting"]
    assert r2["environment"]["sealed_enclosure"] is True
    # Older specs without an environment still load and default to "rules nothing out".
    assert get_device("jetson-orin-nx").kind == "edge_soc"


def test_sealed_enclosure_keeps_fans_out_of_the_search_and_the_plan(c):
    spec = c.get(f"{P}/projects/{c.pid}").json()["spec"]
    spec["environment"] = {"sealed_enclosure": True}
    assert c.put(f"{P}/projects/{c.pid}", json=spec).status_code == 200
    res = c.post(f"{P}/projects/{c.pid}/optimise").json()
    hw = get_device("jetson-orin-nx")
    fans = {o.id for o in hw.cooling_options if o.fan_w > 0}
    fitted = res["baseline"]["config"]["cooling_id"]
    picked = {r["config"]["cooling_id"] for r in res["top"]} | ({res["best"]["config"]["cooling_id"]} if res["best"] else set())
    assert not (picked - {fitted}) & fans
    assert "sealed" in res["message"] and all(o.name in res["message"] for o in hw.cooling_options if o.fan_w > 0 and o.id != fitted)
    opts = c.get(f"{P}/projects/{c.pid}/plan-options").json()
    ids = {x["id"] for x in opts["changes"]}
    assert "cooling:edge-heatsink-fan" not in ids and "cooling:enclosure-venting" not in ids
