"""Green Agent endpoints: status and a streaming chat turn (server-sent events)."""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.client import LLM, LLMError, TokenFactoryClient
from app.agent.loop import run_turn
from app.agent.router import ResolvedModels, resolve_models
from app.auth.deps import Ctx, get_db, require_ctx, require_writer
from app.config import get_llm_settings
from app.db import repository as repo
from app.security import limit_agent

router = APIRouter()
MODEL_CACHE_S = 300.0
DISABLED = (
    "The Green Agent is disabled: set TOKEN_FACTORY_API_KEY (Nebius Token Factory) in .env and "
    "restart the server. The engine and every other screen work without it."
)


def default_llm_factory() -> LLM:
    s = get_llm_settings()
    if not s.configured:
        raise LLMError(DISABLED)
    return TokenFactoryClient(s.api_key or "", s.base_url, s.timeout_s)


def _llm(request: Request) -> LLM:
    factory: Callable[[], LLM] = getattr(request.app.state, "llm_factory", default_llm_factory)
    return factory()


def _models(request: Request, llm: LLM) -> ResolvedModels:
    cache = getattr(request.app.state, "model_cache", None)
    if cache and time.monotonic() - cache[0] < MODEL_CACHE_S:
        return cache[1]
    resolved = resolve_models(llm, get_llm_settings())
    request.app.state.model_cache = (time.monotonic(), resolved)
    return resolved


@router.get("/agent/status")
def agent_status(request: Request, _ctx: Ctx = Depends(require_ctx)) -> dict[str, Any]:
    s = get_llm_settings()
    out: dict[str, Any] = {"configured": s.configured, "base_url": s.base_url, "detail": None}
    if not s.configured:
        out["detail"] = DISABLED
        return out
    try:
        m = _models(request, _llm(request))
        out["models"] = m.by_tier
        out["available_models"] = len(m.available)
        if not any(m.by_tier.values()):
            out["detail"] = "connected, but no Nemotron model was found in the provider's model list"
    except LLMError as e:
        out["detail"] = f"could not reach the provider: {e}"
    return out


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=6000)


class ChatIn(BaseModel):
    project_id: int
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@router.post("/agent/chat")
def agent_chat(
    body: ChatIn,
    request: Request,
    db: Session = Depends(get_db),
    ctx: Ctx = Depends(require_writer),
    _rl: None = Depends(limit_agent),
) -> StreamingResponse:
    if repo.get_project(db, body.project_id, ctx.workspace_id) is None:
        raise HTTPException(404, "project not found")
    ws_id, user_id = ctx.workspace_id, ctx.user_id
    s = get_llm_settings()
    try:
        llm = _llm(request)
        models = _models(request, llm)
    except LLMError as e:
        # 503 = agent unavailable; the rest of the app is unaffected.
        raise HTTPException(503, str(e)) from e

    q: queue.Queue[dict[str, Any] | None] = queue.Queue()
    tavily_factory = getattr(request.app.state, "tavily_factory", None)
    session_factory = request.app.state.session_factory

    def worker() -> None:
        thread_db = session_factory()
        try:
            run_turn(
                thread_db, body.project_id, body.message,
                [m.model_dump() for m in body.history], llm, models, q.put, s.max_tool_calls,
                tavily_factory, ws_id, user_id,
            )  # fmt: skip
        except Exception as e:  # noqa: BLE001  never leave the stream hanging
            q.put({"type": "error", "text": f"agent failed: {type(e).__name__}: {e}"})
            q.put({"type": "done"})
        finally:
            thread_db.close()
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream() -> Iterator[str]:
        while True:
            item = q.get()
            if item is None:
                return
            yield _sse(item)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )
