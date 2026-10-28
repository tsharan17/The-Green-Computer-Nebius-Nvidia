"""Versioned hardware system graphs per project.

Revision ID: 0007_system_graphs
Revises: 0006_user_preferences
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_system_graphs"
down_revision = "0006_user_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_graphs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("device_id", sa.String(120)), sa.Column("schema_id", sa.String(60)),
        sa.Column("note", sa.String(1000)), sa.Column("source_artifacts", sa.JSON), sa.Column("graph", sa.JSON),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "version", name="uq_system_graphs_project_version"),
    )  # fmt: skip
    op.create_index("ix_system_graphs_workspace_id", "system_graphs", ["workspace_id"])
    op.create_index("ix_system_graphs_project_id", "system_graphs", ["project_id"])


def downgrade() -> None:
    op.drop_table("system_graphs")
