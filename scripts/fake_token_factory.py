"""A FAKE OpenAI-compatible model server for development without credentials.

    python scripts/fake_token_factory.py            # serves http://127.0.0.1:8765/v1/
    TOKEN_FACTORY_API_KEY=fake TOKEN_FACTORY_BASE_URL=http://127.0.0.1:8765/v1/ <start the app>

It is NOT Nemotron and NOT Nebius. It follows a fixed script (call get_project and optimise, then
summarise the optimise result using only numbers from it) so the agent loop, streaming UI and
guardrail can be exercised end to end. Never present its output as a model's reasoning.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

MODELS = ["fake/nemotron-3-ultra-dev", "fake/nemotron-3-super-dev", "fake/nemotron-3-nano-dev"]


def last_tool(messages, name):
    for m in reversed(messages):
        if m.get("role") == "tool" and m.get("name") == name:
            return json.loads(m["content"])
    return None


def script(messages, tools):
    user_turns = [m for m in messages if m["role"] == "user"]
    last_user = user_turns[-1]["content"] if user_turns else ""
    if "rewrite" in last_user.lower() and "figures" in last_user.lower():
        opt = last_tool(messages, "optimise")
        b = opt["best"]
        return {"content": f"Use {b['candidate_id']}: energy drops {b['energy_saving_percent']}%."}
    opt = last_tool(messages, "optimise")
    if opt is None and tools:
        return {"content": "", "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "get_project", "arguments": "{}"}},
            {"id": "t2", "type": "function", "function": {"name": "optimise", "arguments": "{}"}},
        ]}  # fmt: skip
    if opt is None:
        return {"content": "I have no results to report."}
    b = opt.get("best")
    if not b:
        return {"content": "No configuration satisfies every hard constraint, and I cannot relax them."}
    m = b["metrics"]
    ch = "; ".join(b["changes_from_baseline"])
    return {
        "content": (
            f"Recommendation ({b['feasibility']}): {ch}.\n"
            f"- Average power {m['power_avg']['value']} W, energy {m['energy_per_inference']['value']} mJ per "
            f"inference ({b['energy_saving_percent']}% less energy than the baseline).\n"
            f"- Search covered {opt['evaluated']} configurations.\n"
            f"- {opt['codesign']['interpretation']}"
        )
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            return self._send({"data": [{"id": m} for m in MODELS]})
        self._send({"error": {"message": "not found"}}, 404)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send({"error": {"message": "not found"}}, 404)
        msg = {"role": "assistant", **script(body["messages"], body.get("tools"))}
        self._send({"model": body["model"], "choices": [{"message": msg}], "usage": {"total_tokens": 0}})

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    print("FAKE token factory on http://127.0.0.1:8765/v1/ (development only)")
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
