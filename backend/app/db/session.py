from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url == "sqlite://":
            kwargs["poolclass"] = StaticPool
    else:
        # Managed PostgreSQL (for example Neon) closes idle connections and pauses itself; test a pooled
        # connection before using it, and do not keep one for longer than five minutes.
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 300
    return create_engine(url, **kwargs)


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
