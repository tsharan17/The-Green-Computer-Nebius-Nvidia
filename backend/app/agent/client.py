"""OpenAI-compatible chat client for Nebius Token Factory (Nemotron).

Written against the documented request/response format: POST {base}/chat/completions with
`messages`, optional `tools` (JSON-Schema functions) and `tool_choice`; replies carry
`tool_calls[].function.{name, arguments(JSON string)}`. The API key is only ever sent in the
Authorization header and never logged or returned.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(RuntimeError):
    """The model provider could not be used (auth, quota, network, bad response)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    arg_error: str | None = None


@dataclass
class ChatReply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The assistant message in wire format, to append to the conversation unchanged.
    message: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    # Why the model stopped: "stop" (finished), "length" (ran out of max_tokens), "tool_calls", ...
    finish_reason: str = ""


class LLM(Protocol):
    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ChatReply: ...

    def list_models(self) -> list[str]: ...


def strip_reasoning(text: str) -> str:
    """Remove <think>…</think> blocks that reasoning models may include in `content`."""
    return _THINK.sub("", text or "").strip()


class TokenFactoryClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout_s: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
    ):
        if not api_key:
            raise LLMError("no API key configured (set TOKEN_FACTORY_API_KEY)")
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        self._sleep = sleep
        self._retries = retries

    def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self._retries):
            try:
                r = self._client.request(method, path.lstrip("/"), **kw)
            except httpx.HTTPError as e:
                last = LLMError(f"could not reach the model provider: {type(e).__name__}")
                self._sleep(1.0 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self._retries - 1:
                last = LLMError(f"provider returned {r.status_code}", r.status_code)
                self._sleep(1.0 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                raise LLMError("the model provider rejected the API key", r.status_code)
            if r.status_code >= 400:
                detail = ""
                try:
                    body = r.json()
                    detail = str(body.get("error", {}).get("message") or body.get("detail") or "")
                except ValueError:
                    pass
                raise LLMError(
                    f"model provider error {r.status_code}: {detail[:300]}".strip(), r.status_code
                )
            try:
                return r.json()
            except ValueError as e:
                raise LLMError("the model provider returned invalid JSON") from e
        raise last or LLMError("model provider request failed")

    def list_models(self) -> list[str]:
        data = self._request("GET", "models")
        items = data.get("data", data if isinstance(data, list) else [])
        return [m["id"] for m in items if isinstance(m, dict) and "id" in m]

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ChatReply:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        data = self._request("POST", "chat/completions", json=body)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("unexpected response shape from the model provider") from e

        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw = fn.get("arguments", "") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
                err = None if isinstance(args, dict) else "arguments are not a JSON object"
                args = args if isinstance(args, dict) else {}
            except (ValueError, TypeError):
                args, err = {}, "arguments are not valid JSON"
            calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args,
                                  arg_error=err))  # fmt: skip

        wire = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            wire["tool_calls"] = msg["tool_calls"]
        return ChatReply(
            content=strip_reasoning(msg.get("content") or ""),
            tool_calls=calls,
            message=wire,
            model=data.get("model", model),
            usage=data.get("usage", {}) or {},
            finish_reason=str(data["choices"][0].get("finish_reason") or ""),
        )
