"""Error reporting adapters. None is required; the default writes the error to the log.

  GC_ERROR_REPORTER = log (default) | webhook | sentry
  webhook  POSTs a scrubbed JSON report to GC_ERROR_WEBHOOK_URL (your own endpoint), in the background
  sentry   uses sentry_sdk with GC_SENTRY_DSN if that package is installed; otherwise falls back to log

Reports never carry request bodies, cookies, headers, emails or tokens: messages are scrubbed of
email addresses and long secret-looking strings, and only file:line:function of the stack is sent.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import traceback
import urllib.request
from typing import Any, Protocol

log = logging.getLogger("green_computer.errors")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SECRET = re.compile(r"[A-Za-z0-9_\-]{24,}")


def scrub(text: str) -> str:
    return _SECRET.sub("[redacted]", _EMAIL.sub("[email]", text))[:2000]


def report_of(exc: BaseException, context: dict[str, Any]) -> dict[str, Any]:
    frames = traceback.extract_tb(exc.__traceback__)[-15:]
    return {"type": type(exc).__name__, "message": scrub(str(exc)),
            "frames": [f"{f.filename.rsplit('app', 1)[-1]}:{f.lineno}:{f.name}" for f in frames],
            "context": {k: v for k, v in context.items() if k in ("request_id", "route", "method", "version", "status")}}  # fmt: skip


class ErrorReporter(Protocol):
    name: str

    def capture(self, exc: BaseException, context: dict[str, Any]) -> None: ...


class LogReporter:
    name = "log"

    def capture(self, exc: BaseException, context: dict[str, Any]) -> None:
        log.error("unhandled error %s", json.dumps(report_of(exc, context)), exc_info=(type(exc), exc, exc.__traceback__))


class WebhookReporter:
    name = "webhook"

    def __init__(self, url: str) -> None:
        self.url = url

    def capture(self, exc: BaseException, context: dict[str, Any]) -> None:
        LogReporter().capture(exc, context)
        body = json.dumps(report_of(exc, context)).encode()

        def send() -> None:
            try:
                req = urllib.request.Request(self.url, data=body, method="POST", headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=3).close()  # noqa: S310 - operator-configured URL
            except Exception as e:  # noqa: BLE001 - reporting must never raise
                log.warning("error webhook failed: %s", e)

        threading.Thread(target=send, daemon=True).start()


class SentryReporter:
    name = "sentry"

    def __init__(self, dsn: str) -> None:
        import sentry_sdk  # type: ignore[import-not-found]

        sentry_sdk.init(dsn=dsn, send_default_pii=False, traces_sample_rate=0.0)
        self.sdk = sentry_sdk

    def capture(self, exc: BaseException, context: dict[str, Any]) -> None:
        LogReporter().capture(exc, context)
        with self.sdk.push_scope() as scope:
            for k, v in report_of(exc, context)["context"].items():
                scope.set_tag(k, v)
            self.sdk.capture_exception(exc)


def make_error_reporter() -> ErrorReporter:
    kind = os.environ.get("GC_ERROR_REPORTER", "log").lower()
    if kind == "webhook" and os.environ.get("GC_ERROR_WEBHOOK_URL"):
        return WebhookReporter(os.environ["GC_ERROR_WEBHOOK_URL"])
    if kind == "sentry" and os.environ.get("GC_SENTRY_DSN"):
        try:
            return SentryReporter(os.environ["GC_SENTRY_DSN"])
        except ImportError:
            log.warning("GC_ERROR_REPORTER=sentry but sentry_sdk is not installed; using the log")
    return LogReporter()
