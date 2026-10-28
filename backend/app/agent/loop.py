"""The Green Agent loop: Nemotron plans and explains, tools compute, a guardrail checks the words.

Per turn: the model may call tools (capped at `max_tool_calls`); tool results are fed back; the
final reply is checked so every figure traces to a tool result. A failed check triggers up to two
rewrites; if it still fails the offending figures are redacted and flagged, never passed through.
Progress is reported through an `emit(event)` callback so the API can stream it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.agent.client import LLM, LLMError
from app.agent.guardrail import check
from app.agent.router import ResolvedModels
from app.agent.tools import TOOL_SCHEMAS, ToolContext, dispatch
from app.db.models import AgentRunRow

Emit = Callable[[dict[str, Any]], None]
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text(encoding="utf-8")
MAX_REWRITES = 2
MAX_RESULT_CHARS = 24000  # keep tool output within a sane context budget
EMPTY_RETRY_TOKENS = 8192
WRITE_NOW = (
    "Write your answer to my question now, from the tool results above. Do not call more tools. "
    "Keep it short: the recommendation first, then a few bullet points."
)


def _compact(result: dict[str, Any]) -> str:
    text = json.dumps(result, separators=(",", ":"), default=str)
    if len(text) > MAX_RESULT_CHARS:
        # Stay valid JSON so the model can still read what fits and knows it was cut.
        return json.dumps({"truncated": True, "partial": text[:MAX_RESULT_CHARS]})
    return text


def run_turn(
    db: Session,
    project_id: int,
    user_message: str,
    history: list[dict[str, Any]],
    llm: LLM,
    models: ResolvedModels,
    emit: Emit,
    max_tool_calls: int = 12,
    tavily_factory: Any = None,
    workspace_id: int | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Run one user turn. Returns {"reply", "events", "guardrail_ok", "messages"}."""
    ctx = ToolContext(
        db=db, project_id=project_id, llm=llm, models=models, tavily_factory=tavily_factory,
        workspace_id=workspace_id, user_id=user_id,
    )
    events: list[dict[str, Any]] = []

    def out(e: dict[str, Any]) -> None:
        events.append(e)
        emit(e)

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
    messages.append({"role": "user", "content": user_message})
    user_texts = [user_message] + [m["content"] for m in history if m.get("role") == "user"]
    tool_results: list[Any] = []
    calls_made = 0
    plan_model = models.for_task("plan")

    out({"type": "status", "text": f"Planning with {plan_model}"})
    reply_text = ""
    rewrites = 0
    empty_retries = 0
    guard_ok = True

    try:
        while True:
            force_text = calls_made >= max_tool_calls
            reply = llm.chat(
                plan_model,
                messages,
                tools=None if force_text else TOOL_SCHEMAS,
                tool_choice=None if force_text else "auto",
            )
            messages.append(reply.message)

            if reply.tool_calls and not force_text:
                for tc in reply.tool_calls:
                    if calls_made >= max_tool_calls:
                        result: dict[str, Any] = {
                            "error": f"tool-call limit ({max_tool_calls}) reached this turn"
                        }
                    elif tc.arg_error:
                        result = {"error": f"could not read arguments: {tc.arg_error}"}
                    else:
                        out({"type": "tool_call", "name": tc.name, "arguments": tc.arguments})
                        result = dispatch(ctx, tc.name, tc.arguments)
                    calls_made += 1
                    if "error" not in result:
                        tool_results.append(result)
                    out(
                        {
                            "type": "tool_result",
                            "name": tc.name,
                            "ok": "error" not in result,
                            "summary": result.get("error") or _summary(tc.name, result),
                        }
                    )
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                         "content": _compact(result)}  # fmt: skip
                    )
                continue

            reply_text = reply.content
            if not reply_text.strip():
                # A reasoning model can spend its whole budget thinking and write nothing: ask
                # once more, without tools and with more room, before giving up.
                if empty_retries < 1:
                    empty_retries += 1
                    out({"type": "status", "text": "Writing the answer"})
                    messages.pop()
                    messages.append({"role": "user", "content": WRITE_NOW})
                    reply = llm.chat(plan_model, messages, max_tokens=EMPTY_RETRY_TOKENS)
                    messages.append(reply.message)
                    reply_text = reply.content
                if not reply_text.strip():
                    raise LLMError(
                        "the model used its whole budget thinking and wrote no answer. Ask again, "
                        "or ask a narrower question"
                    )
            verdict = check(reply_text, tool_results, user_texts)
            if verdict.ok:
                guard_ok = True
                break
            if rewrites < MAX_REWRITES:
                rewrites += 1
                out({"type": "guardrail", "action": "rewrite", "figures": verdict.unverified})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your reply contained figures that do not appear in any tool result: "
                            f"{', '.join(verdict.unverified)}. Rewrite it using only numbers from "
                            "the tool results (or the user's own numbers). If you need a number you "
                            "do not have, call a tool or say you do not have it."
                        ),
                    }
                )
                continue
            guard_ok = False
            reply_text = verdict.text
            out({"type": "guardrail", "action": "redacted", "figures": verdict.unverified})
            break
    except LLMError as e:
        out({"type": "error", "text": str(e)})
        _save(db, project_id, user_message, "", events, False)
        return {"reply": "", "events": events, "guardrail_ok": True, "messages": messages,
                "error": str(e)}  # fmt: skip

    out({"type": "message", "text": reply_text, "guardrail_ok": guard_ok})
    out({"type": "done"})
    _save(db, project_id, user_message, reply_text, events, guard_ok)
    visible = [m for m in messages[1:] if m["role"] in ("user", "assistant") and not m.get("tool_calls")]
    return {"reply": reply_text, "events": events, "guardrail_ok": guard_ok, "messages": visible}


def _summary(name: str, result: dict[str, Any]) -> str:
    if name == "optimise":
        best = result.get("best")
        n = result.get("evaluated")
        return f"searched {n} configurations" + (
            f"; best {best['candidate_id']} ({best['feasibility']})" if best else "; none feasible"
        )
    if name == "evaluate":
        return f"{result.get('feasibility')}"
    if name == "update_project_spec":
        return "project updated"
    if name in ("list_devices", "list_models"):
        return f"{len(result.get('devices', result.get('models', [])))} items"
    return "ok"


def _save(
    db: Session, project_id: int, user: str, reply: str, events: list[dict], guard_ok: bool
) -> None:
    db.add(
        AgentRunRow(
            project_id=project_id,
            user_message=user[:4000],
            reply=reply[:8000],
            events=events,
            guardrail_ok=guard_ok,
        )
    )
    db.commit()
