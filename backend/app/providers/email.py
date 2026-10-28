"""Outgoing email behind an interface. No vendor is required.

- ``ConsoleEmailSender`` (default): writes the message to the server log and keeps the last messages
  in memory, so a self-hosted install without mail works and a developer can read reset links.
- ``SmtpEmailSender``: used when GC_SMTP_HOST is set (any SMTP relay).
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Protocol

log = logging.getLogger("green_computer.email")


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str
    sent_at: datetime


class EmailSender(Protocol):
    name: str

    def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleEmailSender:
    name = "console"

    def __init__(self, keep: int = 50) -> None:
        self.outbox: deque[OutgoingEmail] = deque(maxlen=keep)
        self._lock = threading.Lock()

    def send(self, to: str, subject: str, body: str) -> None:
        with self._lock:
            self.outbox.append(OutgoingEmail(to, subject, body, datetime.now(UTC)))
        log.warning("EMAIL (console sender, not delivered) to=%s subject=%s\n%s", to, subject, body)


class SmtpEmailSender:
    name = "smtp"

    def __init__(self, host: str, port: int, user: str | None, password: str | None,
                 sender: str, starttls: bool) -> None:  # fmt: skip
        self.host, self.port, self.user, self.password = host, port, user, password
        self.sender, self.starttls = sender, starttls

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=15) as s:
            if self.starttls:
                s.starttls(context=ssl.create_default_context())
            if self.user:
                s.login(self.user, self.password or "")
            s.send_message(msg)


def make_email_sender() -> EmailSender:
    env = os.environ.get
    host = env("GC_SMTP_HOST")
    if host:
        return SmtpEmailSender(
            host, int(env("GC_SMTP_PORT") or 587), env("GC_SMTP_USER"), env("GC_SMTP_PASSWORD"),
            env("GC_SMTP_FROM") or "The Green Computer <no-reply@localhost>",
            (env("GC_SMTP_STARTTLS") or "1") not in ("0", "false", "no"),
        )  # fmt: skip
    return ConsoleEmailSender()
