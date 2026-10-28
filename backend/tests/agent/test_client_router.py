import json

import httpx
import pytest

from app.agent.client import LLMError, TokenFactoryClient, strip_reasoning
from app.agent.router import resolve_models
from app.config import DEFAULT_LLM_BASE_URL, get_llm_settings

BASE = "https://api.tokenfactory.nebius.com/v1/"


def make_client(handler, **kw):
    return TokenFactoryClient(
        "sk-secret-123", BASE, transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw
    )


def completion(message, model="m"):
    return httpx.Response(200, json={"model": model, "choices": [{"message": message}],
                                     "usage": {"total_tokens": 5}})  # fmt: skip


def test_default_base_url_is_the_documented_one():
    assert DEFAULT_LLM_BASE_URL == BASE


def test_request_format_matches_the_openai_compatible_contract():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return completion({"role": "assistant", "content": "hi"})

    tools = [{"type": "function", "function": {"name": "f", "description": "d", "parameters": {}}}]
    r = make_client(handler).chat("nvidia/x", [{"role": "user", "content": "yo"}], tools, "auto")
    assert seen["url"] == BASE + "chat/completions"
    assert seen["auth"] == "Bearer sk-secret-123"
    assert seen["body"]["model"] == "nvidia/x" and seen["body"]["tools"] == tools
    assert seen["body"]["tool_choice"] == "auto" and seen["body"]["messages"][0]["content"] == "yo"
    assert r.content == "hi" and r.tool_calls == [] and r.usage["total_tokens"] == 5


def test_tool_calls_are_parsed_and_bad_arguments_are_flagged():
    msg = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "evaluate", "arguments": '{"a": 1}'}},
        {"id": "c2", "type": "function", "function": {"name": "optimise", "arguments": "{oops"}},
        {"id": "c3", "type": "function", "function": {"name": "x", "arguments": "[1]"}},
    ]}  # fmt: skip
    r = make_client(lambda req: completion(msg)).chat("m", [])
    assert [(c.name, c.arguments, c.arg_error is None) for c in r.tool_calls] == [
        ("evaluate", {"a": 1}, True), ("optimise", {}, False), ("x", {}, False)]  # fmt: skip
    assert r.message["tool_calls"] == msg["tool_calls"]  # echoed unchanged for the next turn


def test_reasoning_blocks_are_stripped():
    assert strip_reasoning("<think>hmm\nlots</think>Answer 7.9 W") == "Answer 7.9 W"
    r = make_client(lambda q: completion({"content": "<think>x</think>Done"})).chat("m", [])
    assert r.content == "Done"


def test_retries_transient_errors_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return completion({"content": "ok"})

    assert make_client(handler).chat("m", []).content == "ok" and calls["n"] == 3


def test_auth_failure_is_clear_and_never_leaks_the_key():
    with pytest.raises(LLMError) as e:
        make_client(lambda req: httpx.Response(401, json={})).chat("m", [])
    assert "rejected the API key" in str(e.value) and "sk-secret-123" not in str(e.value)
    assert e.value.status == 401


def test_provider_error_message_and_network_failure():
    with pytest.raises(LLMError, match="400.*bad model"):
        make_client(lambda r: httpx.Response(400, json={"error": {"message": "bad model"}})).chat("m", [])

    def boom(req):
        raise httpx.ConnectError("no route")

    with pytest.raises(LLMError, match="could not reach"):
        make_client(boom).chat("m", [])


def test_no_key_is_refused():
    with pytest.raises(LLMError, match="TOKEN_FACTORY_API_KEY"):
        TokenFactoryClient("", BASE)


def test_list_models():
    c = make_client(lambda r: httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b/c"}]}))
    assert c.list_models() == ["a", "b/c"]


class FakeList:
    def __init__(self, ids):
        self.ids = ids

    def list_models(self):
        return self.ids


def test_router_prefers_configured_ids_then_matches_by_name_and_skips_omni():
    s = get_llm_settings()
    ids = ["nvidia/nemotron-3-super-120b-a12b", "NVIDIA/Nemotron-3-Ultra-550B-A55B",
           "nvidia/nemotron-3-nano-omni", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "other/model"]  # fmt: skip
    m = resolve_models(FakeList(ids), s)
    assert m.by_tier["super"] == "nvidia/nemotron-3-super-120b-a12b"
    assert m.by_tier["ultra"] == "NVIDIA/Nemotron-3-Ultra-550B-A55B"  # matched case-insensitively
    assert m.by_tier["nano"] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"  # not the omni variant
    assert m.for_task("plan") == m.by_tier["ultra"] and m.for_task("fast") == m.by_tier["nano"]


def test_router_falls_back_across_tiers_and_fails_loudly_when_nothing_matches():
    only_super = resolve_models(FakeList(["nvidia/nemotron-3-super-120b-a12b"]), get_llm_settings())
    assert only_super.for_task("plan") == "nvidia/nemotron-3-super-120b-a12b"
    none = resolve_models(FakeList(["meta/llama"]), get_llm_settings())
    with pytest.raises(LLMError, match="none of the Nemotron"):
        none.for_task("plan")
