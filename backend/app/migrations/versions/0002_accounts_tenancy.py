"""Accounts, workspaces, project versions, artifacts, audit log and usage; owner columns.

Additive only. Existing data is kept: every project gets version 1 from its current spec, and data
created before accounts existed (projects, uploaded models, custom hardware, user measurements and
the calibrations fitted on them) moves to a "Local workspace" that the first account created on
this database claims. Library measurements committed with the app stay shared (workspace NULL).

Revision ID: 0002_accounts_tenancy
Revises: 0001_baseline
Create Date: 2026-10-19
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0002_accounts_tenancy"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

OWNED = ("projects", "custom_models", "measurements", "calibrations", "benchmark_jobs",
         "hardware_drafts")  # fmt: skip


def add_fk_column(table: str, column: str, ref: str, index: bool = True) -> None:
    """Nullable integer FK column. SQLite cannot ALTER constraints and batch mode would drop
    unnamed UNIQUE constraints, so on SQLite the reference is declared inline."""
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER REFERENCES {ref}(id)")
    else:
        op.add_column(table, sa.Column(column, sa.Integer, nullable=True))
        op.create_foreign_key(f"fk_{table}_{column}", table, ref, [column], ["id"])
    if index:
        op.create_index(f"ix_{table}_{column}", table, [column])


def _ts(name: str = "created_at", nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _pk() -> sa.Column:
    return sa.Column("id", sa.Integer, primary_key=True, autoincrement=True)


def upgrade() -> None:
    op.create_table(
        "users", _pk(), sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(120)), sa.Column("password_hash", sa.String(300)),
        sa.Column("is_active", sa.Boolean), _ts(), _ts("last_login_at"),
        _ts("password_changed_at"), sa.UniqueConstraint("email", name="uq_users_email"),
    )  # fmt: skip
    op.create_index("ix_users_email", "users", ["email"])
    op.create_table(
        "workspaces", _pk(), sa.Column("name", sa.String(120)), sa.Column("personal", sa.Boolean),
        sa.Column("legacy", sa.Boolean), _ts(),
    )  # fmt: skip
    op.create_table(
        "memberships", _pk(),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(20)), _ts(),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_membership"),
    )  # fmt: skip
    op.create_index("ix_memberships_workspace_id", "memberships", ["workspace_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_table(
        "auth_sessions", _pk(),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False), sa.Column("kind", sa.String(10)),
        sa.Column("label", sa.String(120)), sa.Column("user_agent", sa.String(300)),
        sa.Column("ip", sa.String(64)), _ts(), _ts("last_seen_at"), _ts("expires_at"),
        _ts("revoked_at"), sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )  # fmt: skip
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"])
    op.create_table(
        "password_resets", _pk(),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False), _ts(), _ts("expires_at"),
        _ts("used_at"), sa.UniqueConstraint("token_hash", name="uq_password_resets_token_hash"),
    )  # fmt: skip
    op.create_index("ix_password_resets_user_id", "password_resets", ["user_id"])
    op.create_index("ix_password_resets_token_hash", "password_resets", ["token_hash"])
    op.create_table(
        "project_versions", _pk(),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False), sa.Column("spec", sa.JSON),
        sa.Column("spec_hash", sa.String(64)), sa.Column("note", sa.String(500)),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True), _ts(),
        sa.UniqueConstraint("project_id", "version", name="uq_project_version"),
    )  # fmt: skip
    op.create_index("ix_project_versions_project_id", "project_versions", ["project_id"])
    op.create_table(
        "artifacts", _pk(),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("kind", sa.String(40)), sa.Column("filename", sa.String(255)),
        sa.Column("content_type", sa.String(120)), sa.Column("size_bytes", sa.BigInteger),
        sa.Column("sha256", sa.String(64)), sa.Column("blob_key", sa.String(300), nullable=True),
        sa.Column("status", sa.String(20)), sa.Column("validation", sa.JSON),
        sa.Column("uploaded_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True), _ts(),
        _ts("deleted_at"),
    )  # fmt: skip
    op.create_index("ix_artifacts_workspace_id", "artifacts", ["workspace_id"])
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])
    op.create_table(
        "audit_log", _pk(),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(60)), sa.Column("target_type", sa.String(40)),
        sa.Column("target_id", sa.String(80)), sa.Column("detail", sa.JSON),
        sa.Column("ip", sa.String(64)), _ts(),
    )  # fmt: skip
    op.create_index("ix_audit_log_workspace_id", "audit_log", ["workspace_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_table(
        "usage_events", _pk(),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("kind", sa.String(40)), sa.Column("quantity", sa.Float),
        sa.Column("unit", sa.String(20)), sa.Column("detail", sa.JSON), _ts(),
    )  # fmt: skip
    op.create_index("ix_usage_events_workspace_id", "usage_events", ["workspace_id"])
    op.create_index("ix_usage_events_kind", "usage_events", ["kind"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])

    for t in OWNED:
        add_fk_column(t, "workspace_id", "workspaces")
    add_fk_column("projects", "created_by", "users", index=False)
    op.add_column("projects", sa.Column("current_version", sa.Integer, nullable=False,
                                        server_default="1"))  # fmt: skip
    op.add_column("projects", sa.Column("archived", sa.Boolean, nullable=False,
                                        server_default=sa.false()))  # fmt: skip
    add_fk_column("runs", "version_id", "project_versions")
    add_fk_column("runs", "user_id", "users", index=False)
    add_fk_column("custom_models", "artifact_id", "artifacts", index=False)

    _migrate_data()


def _library_external_ids() -> set[str]:
    """external_id of every measurement committed with the app (these stay shared)."""
    from app.engine.measure_parsers import MeasurementParseError, parse_bench_json

    out: set[str] = set()
    folder = Path(__file__).resolve().parents[2] / "data" / "measurements"
    for f in sorted(folder.glob("*.json")):
        try:
            out.update(r.external_id for r in parse_bench_json(f.read_text("utf-8")) if r.external_id)
        except MeasurementParseError:
            continue
    return out


def _migrate_data() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    projects = bind.execute(sa.text("SELECT id, spec FROM projects")).all()
    versions = sa.table(
        "project_versions", sa.column("project_id"), sa.column("version"), sa.column("spec", sa.JSON),
        sa.column("spec_hash"), sa.column("note"), sa.column("created_at"),
    )  # fmt: skip
    for pid, spec in projects:
        spec = json.loads(spec) if isinstance(spec, str) else spec
        digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        bind.execute(versions.insert().values(project_id=pid, version=1, spec=spec, spec_hash=digest,
                                              note="imported from before versioning", created_at=now))  # fmt: skip

    library = _library_external_ids()
    user_meas = [
        mid for mid, ext in bind.execute(sa.text("SELECT id, external_id FROM measurements")).all()
        if ext not in library
    ]
    counts = {t: bind.execute(sa.text(f"SELECT COUNT(*) FROM {t}")).scalar() or 0
              for t in ("projects", "custom_models", "benchmark_jobs", "hardware_drafts")}  # fmt: skip
    if not any(counts.values()) and not user_meas:
        return
    ws = sa.table("workspaces", sa.column("id"), sa.column("name"), sa.column("personal"),
                  sa.column("legacy"), sa.column("created_at"))  # fmt: skip
    ws_id = bind.execute(
        ws.insert().values(name="Local workspace (data from before accounts)", personal=False,
                           legacy=True, created_at=now).returning(ws.c.id)  # fmt: skip
    ).scalar_one()
    for t in ("projects", "custom_models", "benchmark_jobs", "hardware_drafts"):
        bind.execute(sa.text(f"UPDATE {t} SET workspace_id = :w"), {"w": ws_id})
    if user_meas:
        for mid in user_meas:
            bind.execute(sa.text("UPDATE measurements SET workspace_id = :w WHERE id = :i"),
                         {"w": ws_id, "i": mid})  # fmt: skip
        # Fits may include those measurements: they become private too. Library fits are
        # recomputed at start-up from library data only.
        bind.execute(sa.text("UPDATE calibrations SET workspace_id = :w"), {"w": ws_id})


def downgrade() -> None:
    raise NotImplementedError("0002 is not reversible without losing account data")
