"""Day 5 API: report/recipe export and hardware import with mandatory confirmation."""

import io
import json
import zipfile

import httpx
import pytest

from app.agent.client import ChatReply, ToolCall
from app.agent.datasheet import clean_extraction
from app.agent.loop import run_turn as _run_turn
from app.agent.router import ResolvedModels
from app.agent.tools import TOOL_SCHEMAS
from app.integrations.tavily import SearchHit, TavilyClient, TavilyError
from app.main import create_app
from tests.helpers import AuthedClient

P = "/api/v1"


@pytest.fixture()
def client(tmp_path):
    with AuthedClient(create_app(f"sqlite:///{tmp_path / 'd.db'}")) as c:
        _WS[0] = c.me["active_workspace_id"]
        yield c


_WS: list[int | None] = [None]


def run_turn(*a, **kw):
    """Run the agent as the signed-in test user's workspace (as the API does)."""
    kw.setdefault("workspace_id", _WS[0])
    return _run_turn(*a, **kw)


def pid(c):
    return c.get(f"{P}/projects").json()[0]["id"]


# --- export ----------------------------------------------------------------------------------


def test_export_needs_an_analysis_first_then_serves_report_and_recipe(client):
    p = pid(client)
    assert client.get(f"{P}/projects/{p}/report").status_code == 404
    assert client.get(f"{P}/projects/{p}/recipe").status_code == 404
    assert client.post(f"{P}/projects/{p}/optimise").status_code == 200

    md = client.get(f"{P}/projects/{p}/report")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "## 2. The verdict" in md.text
    html = client.get(f"{P}/projects/{p}/report?format=html")
    assert html.headers["content-type"].startswith("text/html") and "<table>" in html.text
    assert client.get(f"{P}/projects/{p}/report?format=pdf").status_code == 422

    rec = client.get(f"{P}/projects/{p}/recipe").json()
    assert "optimise_model.py" in rec["files"] and rec["warnings"]
    z = client.get(f"{P}/projects/{p}/recipe.zip")
    assert z.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "README.md" in names and "runtime_config.json" in names


def test_optimise_payload_has_the_new_advice_fields(client):
    b = client.post(f"{P}/projects/{pid(client)}/optimise").json()
    assert b["verdict"]["summary"] and b["repair"]["needed"] is True
    assert {a["tier"] for a in b["advice"]} >= {"model", "configure", "design"}
    assert b["alternatives"] and b["whatifs"] and b["limiting_factor"]
    assert all("sparsity" in p["config"] for p in b["points"][:3])


# --- Tavily client ---------------------------------------------------------------------------


def test_tavily_request_format_and_errors(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"results": [
            {"title": "T", "url": "https://x/ds", "content": "snip", "score": 0.9, "raw_content": "raw"},
            {"title": "no url", "content": "x"}]})  # fmt: skip

    c = TavilyClient("tvly-test", transport=httpx.MockTransport(handler))
    hits = c.search("Jetson Orin NX datasheet", max_results=3, include_raw_content=True)
    assert seen["url"] == "https://api.tavily.com/search" and seen["auth"] == "Bearer tvly-test"
    assert seen["body"]["query"] == "Jetson Orin NX datasheet" and seen["body"]["max_results"] == 3
    assert seen["body"]["include_raw_content"] == "text"
    assert [h.url for h in hits] == ["https://x/ds"]  # results without a URL are dropped

    for code, msg in ((401, "rejected"), (429, "limit")):
        bad = TavilyClient("k", transport=httpx.MockTransport(lambda r, c=code: httpx.Response(c)))
        with pytest.raises(TavilyError, match=msg):
            bad.search("x")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)  # a developer .env may set one
    with pytest.raises(TavilyError, match="TAVILY_API_KEY"):
        TavilyClient()


# --- hardware import -------------------------------------------------------------------------

DEVICE_JSON = {
    "name": "Acme Edge AI X1", "vendor": "Acme", "kind": "edge_soc",
    "peak_tflops": {"fp32": 2.0, "fp16": 8.0, "int8": 16.0},
    "mem_bw_gbs": 120.0, "mem_gb": 8.0,
    "power_modes": [{"name": "10W", "cap_w": 10}, {"name": "20W", "cap_w": 20}],
    "t_throttle_c": 95.0,
    "sources": {"peak_tflops": "https://acme.example/x1", "mem_bw_gbs": "https://acme.example/x1",
                "mem_gb": "https://acme.example/x1", "power_modes": "https://other.example/not-read"},
    "notes": "int8 figure was sparse; halved",
}  # fmt: skip


class FakeTavily:
    def search(self, query, max_results=5, include_raw_content=False):
        return [SearchHit("Acme X1 datasheet", "https://acme.example/x1", "snippet", 0.9, "raw page text")]


class ExtractLLM:
    """Stands in for Nemotron during extraction."""

    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def chat(self, model, messages, tools=None, tool_choice=None, temperature=0.2, max_tokens=2048):
        self.calls.append(messages)
        text = "```json\n" + json.dumps(self.payload) + "\n```"
        return ChatReply(content=text, message={"role": "assistant", "content": text})

    def list_models(self):
        return ["nvidia/nemotron-3-nano-30b-a3b"]


@pytest.fixture()
def hw_client(client, monkeypatch):
    monkeypatch.setenv("TOKEN_FACTORY_API_KEY", "k")
    client.app.state.tavily_factory = lambda: FakeTavily()
    client.app.state.llm_factory = lambda: ExtractLLM(DEVICE_JSON)
    return client


def test_import_creates_a_draft_that_is_not_usable_until_confirmed(hw_client):
    c = hw_client
    d = c.post(f"{P}/hardware/import", json={"name": "Acme Edge AI X1"}).json()
    assert d["status"] == "draft" and d["needs_confirmation"] is True and d["missing"] == []
    cand = d["candidate"]
    assert cand["mem_bw_gbs"] == 120.0 and cand["peak_tflops"]["int8"] == 16.0
    ev = {e["field"]: e["source_url"] for e in d["evidence"]}
    assert ev["mem_bw_gbs"] == "https://acme.example/x1"
    assert ev["power_modes"] is None  # a URL the search never returned is not accepted as a source

    # Not usable yet: absent from the library, rejected by projects.
    assert all(not x["id"].startswith("custom-hw") for x in c.get(f"{P}/hardware/library").json())
    bad = {"name": "p", "model_id": "yolov8s", "device_id": "custom-hw-acme-edge-ai-x1"}
    assert c.post(f"{P}/projects", json=bad).status_code == 422

    prof = c.post(f"{P}/hardware/drafts/{d['id']}/confirm", json={}).json()
    assert prof["id"] == "custom-hw-acme-edge-ai-x1" and prof["verified"] is False
    assert prof["sources"]["mem_bw_gbs"] == "extracted_unverified"
    assert prof["sources"]["power_model"] == "assumed" and prof["sources"]["thermal"] == "assumed"
    assert any(x["id"] == prof["id"] for x in c.get(f"{P}/hardware/library").json())

    p = c.post(f"{P}/projects", json={**bad, "device_id": prof["id"]}).json()
    out = c.post(f"{P}/projects/{p['id']}/optimise")
    assert out.status_code == 200 and out.json()["verdict"]["summary"]
    assert c.post(f"{P}/hardware/drafts/{d['id']}/confirm", json={}).status_code == 422  # only once


def test_implausible_extracted_values_are_dropped_and_missing_ones_must_be_supplied(hw_client):
    c = hw_client
    bad = {**DEVICE_JSON, "mem_bw_gbs": 99999999.0, "mem_gb": None}
    c.app.state.llm_factory = lambda: ExtractLLM(bad)
    d = c.post(f"{P}/hardware/import", json={"name": "Acme Edge AI X1"}).json()
    assert d["dropped_implausible"] == ["mem_bw_gbs"]
    assert set(d["missing"]) == {"mem_bw_gbs", "mem_gb"}
    r = c.post(f"{P}/hardware/drafts/{d['id']}/confirm", json={})
    assert r.status_code == 422 and "mem_bw_gbs" in r.json()["detail"]  # never guessed
    ok = c.post(f"{P}/hardware/drafts/{d['id']}/confirm", json={"overrides": {"mem_bw_gbs": 100, "mem_gb": 4}})
    assert ok.status_code == 200 and ok.json()["mem_bw_gbs"] == 100


def test_user_can_reject_and_manual_entry_uses_the_same_gate(hw_client):
    c = hw_client
    d = c.post(f"{P}/hardware/import", json={"name": "Acme Edge AI X1"}).json()
    assert c.post(f"{P}/hardware/drafts/{d['id']}/reject").json()["status"] == "rejected"
    assert c.post(f"{P}/hardware/drafts/{d['id']}/confirm", json={}).status_code == 422
    m = c.post(f"{P}/hardware/manual", json={"name": "My Board", "peak_tflops": {"fp16": 3.0},
                                             "mem_bw_gbs": 50, "mem_gb": 4, "tdp_w": 12}).json()
    assert m["status"] == "draft" and m["needs_confirmation"]
    assert c.post(f"{P}/hardware/drafts/{m['id']}/confirm", json={}).status_code == 200
    assert len(c.get(f"{P}/hardware/drafts").json()) == 2


def test_confirmed_devices_survive_a_restart(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'r.db'}"
    with AuthedClient(create_app(url)) as c:
        d = c.post(f"{P}/hardware/manual", json={"name": "Keep Me", "peak_tflops": {"fp16": 3.0},
                                                 "mem_bw_gbs": 50, "mem_gb": 4, "tdp_w": 12}).json()
        c.post(f"{P}/hardware/drafts/{d['id']}/confirm", json={})
    with AuthedClient(create_app(url)) as c2:
        assert any(x["id"] == "custom-hw-keep-me" for x in c2.get(f"{P}/hardware/library").json())
    with AuthedClient(create_app(f"sqlite:///{tmp_path / 'other.db'}")) as c3:  # another db: no leak
        assert all(not x["id"].startswith("custom-hw") for x in c3.get(f"{P}/hardware/library").json())


def test_import_is_disabled_clearly_without_keys(client):
    r = client.post(f"{P}/hardware/import", json={"name": "Acme Edge AI X1"})
    assert r.status_code == 503 and "TAVILY_API_KEY" in r.json()["detail"]


def test_clean_extraction_only_accepts_sourced_plausible_values():
    ex = clean_extraction({**DEVICE_JSON, "t_throttle_c": 400, "kind": "toaster"}, {"https://acme.example/x1"})
    assert "t_throttle_c" not in ex.candidate and "kind" not in ex.candidate
    assert ex.dropped == ["t_throttle_c"]


def test_extraction_repairs_a_missing_comma_between_lines():
    from app.agent.datasheet import _json_from

    assert _json_from('{"name": "X1",\n "peak_tflops": {"int8": 4}\n "mem_gb": 8}') == {
        "name": "X1", "peak_tflops": {"int8": 4}, "mem_gb": 8}  # fmt: skip


@pytest.mark.parametrize("answers", [True, False])
def test_a_reasoning_model_that_runs_out_of_room_gets_twice_the_room_once(answers):
    from app.agent.datasheet import DatasheetError, extract_hardware_spec

    class Thinker(ExtractLLM):
        def __init__(self, payload):
            super().__init__(payload)
            self.budgets: list[int] = []

        def chat(self, model, messages, tools=None, tool_choice=None, temperature=0.2, max_tokens=2048):
            self.budgets.append(max_tokens)
            if len(self.budgets) == 2 and answers:
                return super().chat(model, messages, max_tokens=max_tokens)
            return ChatReply(content="", message={"role": "assistant", "content": ""}, finish_reason="length")

    llm = Thinker(DEVICE_JSON)
    hits = FakeTavily().search("x")
    if answers:
        assert extract_hardware_spec(llm, "n", "Acme X1", hits).candidate["mem_bw_gbs"] == 120.0
    else:
        with pytest.raises(DatasheetError, match="ran out of room"):
            extract_hardware_spec(llm, "n", "Acme X1", hits)
    assert llm.budgets == [4096, 8192]


# --- agent tools -----------------------------------------------------------------------------

MODELS = ResolvedModels(available=["n"], by_tier={"ultra": "u", "super": "s", "nano": "n"})


def test_the_agent_can_draft_hardware_but_has_no_way_to_confirm_it(hw_client):
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert {"find_datasheet", "import_hardware"} <= names
    assert not any("confirm" in n for n in names)

    c = hw_client
    steps = [
        ChatReply(content="", tool_calls=[ToolCall("c1", "import_hardware", {"device_name": "Acme Edge AI X1"})],
                  message={"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                           "function": {"name": "import_hardware", "arguments": "{\"device_name\": \"Acme Edge AI X1\"}"}}]}),
        ChatReply(content="I drafted it; you must confirm it first.", message={"role": "assistant", "content": "x"}),
    ]  # fmt: skip

    class Scripted(ExtractLLM):
        def chat(self, model, messages, tools=None, **k):
            if tools is None:
                return super().chat(model, messages, **k)  # the extraction call
            return steps.pop(0)

    db = c.app.state.session_factory()
    seen = []
    out = run_turn(db, pid(c), "add the Acme board", [], Scripted(DEVICE_JSON), MODELS, seen.append,
                   tavily_factory=lambda: FakeTavily())  # fmt: skip
    res = [e for e in seen if e["type"] == "tool_result"][0]
    assert res["ok"] and out["guardrail_ok"]
    drafts = c.get(f"{P}/hardware/drafts").json()
    assert len(drafts) == 1 and drafts[0]["status"] == "draft"  # still a draft after the agent turn
    db.close()
