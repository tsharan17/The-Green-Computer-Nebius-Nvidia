"""Task -> Nemotron tier routing, resolved against the provider's real model list.

Tiers (from the plan): Ultra for planning, constraint-repair reasoning and the final narrative;
Super/Nano for fast calls such as intent parsing and tool selection. Configured model IDs are
checked against `GET /models`; if one is absent we look for a model whose name contains the
tier keyword, and otherwise fail loudly rather than call a model that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.client import LLM, LLMError
from app.config import LLMSettings

KEYWORDS = {"ultra": "nemotron-3-ultra", "super": "nemotron-3-super", "nano": "nemotron-3-nano"}
# Preference order per task: first available wins.
TASK_TIERS = {
    "fast": ("nano", "super", "ultra"),  # intent parsing, simple replies
    "plan": ("ultra", "super", "nano"),  # multi-step planning, explanations, narrative
    "recommend": ("super", "ultra", "nano"),  # one structured JSON reply: candidate changes
}


@dataclass(frozen=True)
class ResolvedModels:
    available: list[str]
    by_tier: dict[str, str | None]

    def for_task(self, task: str) -> str:
        for tier in TASK_TIERS[task]:
            m = self.by_tier.get(tier)
            if m:
                return m
        raise LLMError("none of the Nemotron models is available on this provider/account")


def resolve_models(llm: LLM, settings: LLMSettings) -> ResolvedModels:
    available = llm.list_models()
    lower = {m.lower(): m for m in available}
    wanted = {"ultra": settings.ultra, "super": settings.super_, "nano": settings.nano}
    out: dict[str, str | None] = {}
    for tier, configured in wanted.items():
        if configured.lower() in lower:
            out[tier] = lower[configured.lower()]
            continue
        hit = next(
            (orig for low, orig in lower.items() if KEYWORDS[tier] in low and "omni" not in low),
            None,
        )
        out[tier] = hit
    return ResolvedModels(available=available, by_tier=out)
