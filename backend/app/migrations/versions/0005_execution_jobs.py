"""Execution jobs: real runs of a project's model on an execution backend.

Revision ID: 0005_execution_jobs
Revises: 0004_plans_exports
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_execution_jobs"
down_revision = "0004_plans_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("backend", sa.String(40)), sa.Column("status", sa.String(20)),
        sa.Column("params", sa.JSON), sa.Column("result", sa.JSON), sa.Column("error", sa.String(1000), nullable=True),
        sa.Column("measurement_id", sa.Integer, sa.ForeignKey("measurements.id"), nullable=True),
        sa.Column("trace_id", sa.Integer, sa.ForeignKey("execution_traces.id"), nullable=True),
        sa.Column("seconds", sa.Float),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)),
    )  # fmt: skip
    op.create_index("ix_execution_jobs_workspace_id", "execution_jobs", ["workspace_id"])
    op.create_index("ix_execution_jobs_project_id", "execution_jobs", ["project_id"])


def downgrade() -> None:
    op.drop_table("execution_jobs")
