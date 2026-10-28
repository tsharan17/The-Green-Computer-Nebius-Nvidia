"""Tavily search client (finds datasheets and spec pages for hardware not in the library).

Request format from the Tavily docs: POST https://api.tavily.com/search with
`Authorization: Bearer tvly-...` and a JSON body (query, search_depth, max_results,
include_raw_content, include_domains, topic). The key is only sent in that header.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

TAVILY_URL = "https://api.tavily.com/search"


class TavilyError(RuntimeError):
    """The search service could not be used."""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    content: str  # short snippet
    score: float
    raw_content: str | None = None


def configured() -> bool:
    return bool(os.environ.get("TAVILY_API_KEY"))


class TavilyClient:
    def __init__(
        self,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 30.0,
    ):
        key = api_key or os.environ.get("TAVILY_API_KEY")
        if not key:
            raise TavilyError("no Tavily key configured (set TAVILY_API_KEY in .env)")
        self._client = httpx.Client(
            timeout=timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )

    def search(
        self, query: str, max_results: int = 5, include_raw_content: bool = False
    ) -> list[SearchHit]:
        body: dict[str, Any] = {
            "query": query,
            "search_depth": "basic",
            "max_results": max(1, min(max_results, 10)),
            "include_raw_content": "text" if include_raw_content else False,
            "topic": "general",
        }
        try:
            r = self._client.post(TAVILY_URL, json=body)
        except httpx.HTTPError as e:
            raise TavilyError(f"could not reach Tavily: {type(e).__name__}") from e
        if r.status_code in (401, 403):
            raise TavilyError("Tavily rejected the API key")
        if r.status_code == 429:
            raise TavilyError("Tavily rate limit or credit limit reached")
        if r.status_code >= 400:
            raise TavilyError(f"Tavily error {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise TavilyError("Tavily returned invalid JSON") from e
        return [
            SearchHit(
                title=str(x.get("title", "")),
                url=str(x.get("url", "")),
                content=str(x.get("content", "")),
                score=float(x.get("score") or 0.0),
                raw_content=x.get("raw_content"),
            )
            for x in data.get("results", [])
            if x.get("url")
        ]
