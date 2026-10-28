"""Optional AI assistant provider, behind an interface.

The deterministic engine computes every technical number. An AI provider may only plan which
engine tools to call and explain their results (see agent/guardrail.py); the platform works fully
with no provider configured. Any OpenAI-compatible endpoint works (Nebius Token Factory is the
default base URL); the provider is chosen by configuration, not code.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.config import get_llm_settings


class AIProvider(Protocol):
    def chat(self, model: str, messages: list[dict[str, Any]], tools: Any = None,
             tool_choice: Any = None, temperature: float = 0.2, max_tokens: int = 2048) -> Any: ...  # fmt: skip

    def list_models(self) -> list[str]: ...


def get_ai_provider() -> AIProvider | None:
    """The configured provider, or None (the assistant is then simply off)."""
    s = get_llm_settings()
    if not s.configured:
        return None
    from app.agent.client import TokenFactoryClient

    return TokenFactoryClient(s.api_key or "", s.base_url, s.timeout_s)


def ai_status() -> dict[str, Any]:
    s = get_llm_settings()
    return {
        "configured": s.configured,
        "base_url": s.base_url if s.configured else None,
        "role": "Optional assistant: explains results and drives the engine through tools. "
                "It never produces a number itself.",
    }
