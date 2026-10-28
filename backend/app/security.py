"""Safeguards for a public demo: rate limits, a switch for billed actions, security headers.

A judge-facing URL spends real credits (Token Factory tokens, Tavily searches, GPU time) and is
shared by strangers, so in demo mode (`GC_PUBLIC_DEMO=1`) billed or abusable actions are refused
and the Green Agent and the analysis endpoint are rate limited per client IP. Locally nothing here
restricts anything unless you set the limits yourself.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def demo_mode() -> bool:
    return _truthy(os.environ.get("GC_PUBLIC_DEMO"))


def _int_env(name: str, demo_default: int, local_default: int = 0) -> int:
    raw = os.environ.get(name)
    if raw not in (None, ""):
        try:
            return int(raw)
        except ValueError:
            pass
    return demo_default if demo_mode() else local_default


def client_ip(request: Request) -> str:
    """The caller's address. X-Forwarded-For is trusted only behind a known proxy."""
    if _truthy(os.environ.get("GC_TRUST_PROXY")):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Sliding-window limiter, in memory (one process). max_calls <= 0 disables it."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, max_calls: int, window_s: float) -> float | None:
        """None if allowed, else seconds until the next call would be allowed."""
        if max_calls <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= max_calls:
                return window_s - (now - q[0])
            q.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class DatabaseRateLimiter:
    """Shared across processes and machines: sliding-window counters in the `rate_limits` table.

    The estimate is previous_window * (share of it still inside the window) + current_window, the
    usual sliding-window counter. Each call increments atomically (INSERT .. ON CONFLICT DO UPDATE ..
    RETURNING), so concurrent workers cannot both slip under the limit.
    """

    def __init__(self, engine) -> None:  # type: ignore[no-untyped-def]
        self.engine = engine
        self.dialect = engine.dialect.name
        self._calls = 0

    def _incr(self, conn, key: str, start: int) -> int:  # type: ignore[no-untyped-def]
        from sqlalchemy.dialects import postgresql, sqlite

        from app.db.models import RateLimitRow

        t = RateLimitRow.__table__
        ins = (postgresql.insert if self.dialect == "postgresql" else sqlite.insert)(RateLimitRow).values(key=key, window_start=start, count=1)
        stmt = ins.on_conflict_do_update(index_elements=["key", "window_start"], set_={"count": t.c.count + 1}).returning(t.c.count)
        return int(conn.execute(stmt).scalar_one())

    def check(self, key: str, max_calls: int, window_s: float) -> float | None:
        if max_calls <= 0:
            return None
        from sqlalchemy import delete, select

        from app.db.models import RateLimitRow

        w = max(int(window_s), 1)
        now = time.time()
        cur = int(now // w) * w
        k = f"{key}|{w}"[:200]
        t = RateLimitRow.__table__
        with self.engine.begin() as conn:
            n = self._incr(conn, k, cur)
            prev = conn.execute(select(t.c.count).where(t.c.key == k, t.c.window_start == cur - w)).scalar() or 0
            self._calls += 1
            if self._calls % 500 == 0:  # occasional clean-up of windows nobody can still need
                conn.execute(delete(RateLimitRow).where(RateLimitRow.window_start < int(now) - 2 * 86400))
        estimate = prev * (1 - (now - cur) / w) + n
        if estimate > max_calls:
            return max(w - (now - cur), 1.0)
        return None

    def reset(self) -> None:
        from sqlalchemy import delete

        from app.db.models import RateLimitRow

        with self.engine.begin() as conn:
            conn.execute(delete(RateLimitRow))


class SharedLimiter:
    """The limiter every route uses; the backend is chosen when the app starts (configure_limiter)."""

    def __init__(self) -> None:
        self.backend: RateLimiter | DatabaseRateLimiter = RateLimiter()
        self.kind = "memory"

    def check(self, key: str, max_calls: int, window_s: float) -> float | None:
        return self.backend.check(key, max_calls, window_s)

    def reset(self) -> None:
        self.backend.reset()


LIMITER = SharedLimiter()


def configure_limiter(engine) -> str:  # type: ignore[no-untyped-def]
    """GC_RATE_LIMIT_BACKEND = memory | database | auto (default: database on PostgreSQL, where
    several workers share one database; memory on SQLite, a single process)."""
    choice = os.environ.get("GC_RATE_LIMIT_BACKEND", "auto").lower()
    if choice == "database" or (choice == "auto" and engine.dialect.name == "postgresql"):
        if engine.dialect.name in ("postgresql", "sqlite"):
            LIMITER.backend, LIMITER.kind = DatabaseRateLimiter(engine), "database"
            return LIMITER.kind
    LIMITER.backend, LIMITER.kind = RateLimiter(), "memory"
    return LIMITER.kind


def _limit(request: Request, bucket: str, max_calls: int, window_s: float, what: str) -> None:
    wait = LIMITER.check(f"{bucket}:{client_ip(request)}", max_calls, window_s)
    if wait is not None:
        raise HTTPException(
            429,
            f"Too many {what} from your address. This public demo limits usage to protect its "
            f"credits; try again in about {int(wait) + 1} seconds.",
            headers={"Retry-After": str(int(wait) + 1)},
        )


def limit_agent(request: Request) -> None:
    _limit(request, "agent", _int_env("GC_AGENT_RATE_PER_HOUR", 40), 3600.0, "agent questions")


def limit_datasheet(request: Request) -> None:
    """Datasheet search and reading spend Tavily and Token Factory credits: a few per hour in the demo."""
    _limit(request, "datasheet", _int_env("GC_DATASHEET_RATE_PER_HOUR", 5), 3600.0, "datasheet imports")


def limit_analyse(request: Request) -> None:
    _limit(request, "analyse", _int_env("GC_ANALYSE_RATE_PER_MIN", 30), 60.0, "analyses")


def block_in_demo(feature: str) -> None:
    """Refuse a billed or abusable action in the public demo."""
    if demo_mode():
        raise HTTPException(
            403,
            f"{feature} is turned off in the public demo because it spends real credits. "
            "Run your own copy (see the README) to use it.",
        )


def max_projects() -> int:
    return _int_env("GC_MAX_PROJECTS", 5000)


def max_custom_items() -> int:
    return _int_env("GC_MAX_CUSTOM_ITEMS", 50)


def max_upload_bytes(default: int) -> int:
    return min(default, _int_env("GC_MAX_UPLOAD_MB", 10) * 1024 * 1024) if demo_mode() else default


CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; frame-src 'self' about:; base-uri 'self'; "
    "form-action 'self'; object-src 'none'"
)


class OriginCheck(BaseHTTPMiddleware):
    """Refuse state-changing API calls whose Origin is neither this server nor an allowed origin
    (defence in depth on top of the SameSite cookie and the CSRF header)."""

    def __init__(self, app, allowed: tuple[str, ...] = ()) -> None:
        super().__init__(app)
        self.allowed = {o.rstrip("/") for o in allowed}

    async def dispatch(self, request: Request, call_next) -> Response:
        origin = request.headers.get("origin")
        if (
            origin
            and request.method not in ("GET", "HEAD", "OPTIONS")
            and request.url.path.startswith("/api/")
        ):
            scheme = request.url.scheme
            if _truthy(os.environ.get("GC_TRUST_PROXY")):
                # Behind a TLS-terminating proxy (Cloud Run, Render) the app itself sees plain http.
                scheme = request.headers.get("x-forwarded-proto", scheme).split(",")[0].strip()
            own = f"{scheme}://{request.headers.get('host', '')}"
            if origin.rstrip("/") not in self.allowed | {own}:
                return Response(
                    '{"detail":"Cross-origin request refused."}', status_code=403,
                    media_type="application/json",
                )  # fmt: skip
        return await call_next(request)


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
            resp.headers.setdefault("Content-Security-Policy", CSP)
        return resp
