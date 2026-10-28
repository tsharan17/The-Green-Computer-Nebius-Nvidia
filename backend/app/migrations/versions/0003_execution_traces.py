"""Execution traces (digital twin v1): predicted and measured, immutable.

Revision ID: 0003_execution_traces
Revises: 0002_accounts_tenancy
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_execution_traces"
down_revision = "0002_accounts_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_traces",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version_id", sa.Integer, sa.ForeignKey("project_versions.id"), nullable=True),
        sa.Column("kind", sa.String(12)), sa.Column("provenance", sa.String(20)),
        sa.Column("source", sa.String(120)), sa.Column("schema_id", sa.String(60)),
        sa.Column("label", sa.String(200)), sa.Column("config", sa.JSON), sa.Column("summary", sa.JSON),
        sa.Column("trace", sa.JSON),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )  # fmt: skip
    op.create_index("ix_execution_traces_workspace_id", "execution_traces", ["workspace_id"])
    op.create_index("ix_execution_traces_project_id", "execution_traces", ["project_id"])


def downgrade() -> None:
    op.drop_table("execution_traces")
