"""Baseline: the schema of release 0.7.0 (created by `create_all` before migrations existed).

Each table is created only if missing, so a database from 0.7.0 is adopted as-is and a new
database gets the same starting point.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _ts(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True))


def upgrade() -> None:
    have = set(sa.inspect(op.get_bind()).get_table_names())

    def create(name: str, *cols, indexes: tuple[str, ...] = ()) -> None:
        if name in have:
            return
        op.create_table(name, *cols)
        for col in indexes:
            op.create_index(f"ix_{name}_{col}", name, [col])

    pk = lambda: sa.Column("id", sa.Integer, primary_key=True, autoincrement=True)  # noqa: E731

    create("projects", pk(), sa.Column("name", sa.String(200)), sa.Column("spec", sa.JSON),
           _ts(), _ts("updated_at"))  # fmt: skip
    create("runs", pk(), sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
           sa.Column("kind", sa.String(20)), sa.Column("engine_version", sa.String(40)),
           sa.Column("result", sa.JSON), _ts(), indexes=("project_id",))  # fmt: skip
    create("custom_models", pk(), sa.Column("name", sa.String(200)), sa.Column("profile", sa.JSON),
           _ts())  # fmt: skip
    create("measurements", pk(), sa.Column("device_id", sa.String(80)),
           sa.Column("model_id", sa.String(80)), sa.Column("precision", sa.String(10)),
           sa.Column("batch", sa.Integer), sa.Column("external_id", sa.String(80), nullable=True),
           sa.Column("record", sa.JSON), _ts(), sa.UniqueConstraint("external_id"),
           indexes=("device_id", "model_id"))  # fmt: skip
    create("calibrations", pk(), sa.Column("device_id", sa.String(80)),
           sa.Column("precision", sa.String(10)), sa.Column("version", sa.Integer),
           sa.Column("accepted", sa.Boolean), sa.Column("calibration", sa.JSON),
           sa.Column("report", sa.JSON), _ts(), indexes=("device_id",))  # fmt: skip
    create("benchmark_jobs", pk(), sa.Column("job_name", sa.String(120)),
           sa.Column("job_id", sa.String(200)), sa.Column("device_id", sa.String(80)),
           sa.Column("model_id", sa.String(80)), sa.Column("precision", sa.String(10)),
           sa.Column("batches", sa.JSON), sa.Column("state", sa.String(20)),
           sa.Column("ingested", sa.Boolean), sa.Column("measurement_ids", sa.JSON),
           sa.Column("error", sa.String(600), nullable=True), _ts(), _ts("updated_at"))  # fmt: skip
    create("agent_runs", pk(), sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
           sa.Column("user_message", sa.String(4000)), sa.Column("reply", sa.String(8000)),
           sa.Column("events", sa.JSON), sa.Column("guardrail_ok", sa.Boolean), _ts(),
           indexes=("project_id",))  # fmt: skip
    create("hardware_drafts", pk(), sa.Column("query", sa.String(200)),
           sa.Column("status", sa.String(20)), sa.Column("profile", sa.JSON),
           sa.Column("evidence", sa.JSON), sa.Column("missing", sa.JSON),
           sa.Column("device_id", sa.String(80), nullable=True), _ts())  # fmt: skip


def downgrade() -> None:
    for t in ("hardware_drafts", "agent_runs", "benchmark_jobs", "calibrations", "measurements",
              "custom_models", "runs", "projects"):  # fmt: skip
        op.drop_table(t)
