"""Request ids, structured logs, metrics and error capture for every request.

  GC_LOG_FORMAT = text (default) | json    one line per request: id, method, route, status, ms
  GC_LOG_IPS = 1                           also log the client address (off by default)
  GC_METRICS_TOKEN = <secret>              enables GET /metrics (Prometheus text) for that bearer token

Logged and counted by route template (/api/v1/projects/{project_id}), never by raw path or query,
so ids, emails and search terms do not end up in logs or metric labels.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections import defaultdict
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from app.providers.errors import ErrorReporter

log = logging.getLogger("green_computer.http")
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
_RID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
                               "level": record.levelname.lower(), "logger": record.name, "msg": record.getMessage()}  # fmt: skip
        for k in ("request_id", "method", "route", "status", "ms", "ip"):
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)[-4000:]
        return json.dumps(out)


def configure_logging() -> None:
    root = logging.getLogger("green_computer")
    if getattr(root, "_gc_configured", False):
        return
    handler = logging.StreamHandler(sys.stderr)
    if os.environ.get("GC_LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(os.environ.get("GC_LOG_LEVEL", "INFO").upper())
    root.propagate = False
    root._gc_configured = True  # type: ignore[attr-defined]


class Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self.hist: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0] * (len(BUCKETS) + 1))
        self.sums: dict[tuple[str, str], float] = defaultdict(float)
        self.errors = 0
        self.started = time.time()

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        with self.lock:
            self.requests[(method, route, str(status))] += 1
            h = self.hist[(method, route)]
            for i, b in enumerate(BUCKETS):
                if seconds <= b:
                    h[i] += 1
            h[-1] += 1
            self.sums[(method, route)] += seconds

    def render(self, extra: dict[str, float] | None = None) -> str:
        def esc(v: str) -> str:
            return v.replace("\\", "\\\\").replace('"', '\\"')

        lines = ["# HELP gc_http_requests_total Requests by method, route template and status.",
                 "# TYPE gc_http_requests_total counter"]  # fmt: skip
        with self.lock:
            for (m, r, s), n in sorted(self.requests.items()):
                lines.append(f'gc_http_requests_total{{method="{m}",route="{esc(r)}",status="{s}"}} {n}')
            lines += ["# HELP gc_http_request_duration_seconds Request duration.", "# TYPE gc_http_request_duration_seconds histogram"]
            for (m, r), h in sorted(self.hist.items()):
                lab = f'method="{m}",route="{esc(r)}"'
                for i, b in enumerate(BUCKETS):
                    lines.append(f'gc_http_request_duration_seconds_bucket{{{lab},le="{b}"}} {h[i]}')
                lines.append(f'gc_http_request_duration_seconds_bucket{{{lab},le="+Inf"}} {h[-1]}')
                lines.append(f"gc_http_request_duration_seconds_sum{{{lab}}} {self.sums[(m, r)]:.6f}")
                lines.append(f"gc_http_request_duration_seconds_count{{{lab}}} {h[-1]}")
            lines += ["# HELP gc_unhandled_errors_total Unhandled errors.", "# TYPE gc_unhandled_errors_total counter",
                      f"gc_unhandled_errors_total {self.errors}",
                      "# HELP gc_uptime_seconds Seconds since this process started.", "# TYPE gc_uptime_seconds gauge",
                      f"gc_uptime_seconds {time.time() - self.started:.0f}"]  # fmt: skip
        for k, v in (extra or {}).items():
            lines += [f"# TYPE {k} gauge", f"{k} {v}"]
        return "\n".join(lines) + "\n"


def _route(request: Request) -> str:
    r = request.scope.get("route")
    path = getattr(r, "path", None)
    if path:
        path = str(path)
        if request.url.path.startswith("/api/") and not path.startswith("/api/"):
            from app.config import get_settings

            path = get_settings().api_prefix.rstrip("/") + path  # included routers report their own part only
        return path
    return "/api/(unmatched)" if request.url.path.startswith("/api/") else "/(static)"


class RequestObservability(BaseHTTPMiddleware):
    def __init__(self, app, metrics: Metrics, reporter: ErrorReporter, version: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.metrics, self.reporter, self.version = metrics, reporter, version

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        inbound = request.headers.get("x-request-id", "")
        rid = inbound if _RID.match(inbound) else uuid.uuid4().hex
        request.state.request_id = rid
        t0 = time.perf_counter()
        try:
            resp = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - every unhandled error is reported, then answered plainly
            self.metrics.errors += 1
            route = _route(request)
            self.reporter.capture(exc, {"request_id": rid, "route": route, "method": request.method, "version": self.version})
            resp = JSONResponse({"detail": "Something went wrong on the server. Quote this id if you report it.",
                                 "request_id": rid}, status_code=500)  # fmt: skip
        dt = time.perf_counter() - t0
        route = _route(request)
        self.metrics.observe(request.method, route, resp.status_code, dt)
        resp.headers["X-Request-ID"] = rid
        extra: dict[str, Any] = {"request_id": rid, "method": request.method, "route": route, "status": resp.status_code, "ms": round(dt * 1000, 1)}
        if os.environ.get("GC_LOG_IPS", "") in ("1", "true", "yes"):
            extra["ip"] = request.client.host if request.client else ""
        if route != "/(static)":
            log.info("%s %s %s %.1fms", request.method, route, resp.status_code, dt * 1000, extra=extra)
        return resp


def metrics_response(request: Request, metrics: Metrics, extra: dict[str, float]) -> Response:
    token = os.environ.get("GC_METRICS_TOKEN", "")
    if not token:
        return PlainTextResponse("metrics are off (set GC_METRICS_TOKEN)\n", status_code=404)
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given, token):
        return PlainTextResponse("unauthorised\n", status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return PlainTextResponse(metrics.render(extra), media_type="text/plain; version=0.0.4")
