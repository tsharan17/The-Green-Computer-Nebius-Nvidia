"""Design plans (versioned selections with their analysis) and export records.

Revision ID: 0004_plans_exports
Revises: 0003_execution_traces
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_plans_exports"
down_revision = "0003_execution_traces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "design_plans",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("project_version_id", sa.Integer, sa.ForeignKey("project_versions.id"), nullable=True),
        sa.Column("source_run_id", sa.Integer, sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("name", sa.String(200)), sa.Column("version", sa.Integer), sa.Column("note", sa.String(1000)),
        sa.Column("changes", sa.JSON), sa.Column("analysis", sa.JSON),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )  # fmt: skip
    op.create_index("ix_design_plans_workspace_id", "design_plans", ["workspace_id"])
    op.create_index("ix_design_plans_project_id", "design_plans", ["project_id"])
    op.create_table(
        "exports",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("design_plans.id"), nullable=True),
        sa.Column("kind", sa.String(40)), sa.Column("filename", sa.String(255)),
        sa.Column("media_type", sa.String(120)), sa.Column("label", sa.String(30)),
        sa.Column("size_bytes", sa.BigInteger), sa.Column("sha256", sa.String(64)),
        sa.Column("blob_key", sa.String(300), nullable=True), sa.Column("validation", sa.JSON),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )  # fmt: skip
    op.create_index("ix_exports_workspace_id", "exports", ["workspace_id"])
    op.create_index("ix_exports_project_id", "exports", ["project_id"])


def downgrade() -> None:
    op.drop_table("exports")
    op.drop_table("design_plans")
