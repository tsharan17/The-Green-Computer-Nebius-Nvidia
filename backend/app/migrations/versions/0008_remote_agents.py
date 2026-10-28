"""Remote device agents and their signed run requests.

Revision ID: 0008_remote_agents
Revises: 0007_system_graphs
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_remote_agents"
down_revision = "0007_system_graphs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "remote_devices",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(120)), sa.Column("device_id", sa.String(120)), sa.Column("status", sa.String(20)),
        sa.Column("token_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("pairing_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("pairing_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("key_version", sa.Integer), sa.Column("agent_info", sa.JSON),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)), sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )  # fmt: skip
    op.create_index("ix_remote_devices_workspace_id", "remote_devices", ["workspace_id"])
    op.create_table(
        "remote_jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("remote_device_id", sa.Integer, sa.ForeignKey("remote_devices.id"), nullable=False),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("artifact_id", sa.Integer, sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("status", sa.String(20)), sa.Column("payload", sa.JSON), sa.Column("signature", sa.String(64)),
        sa.Column("result", sa.JSON, nullable=True), sa.Column("quality", sa.JSON, nullable=True),
        sa.Column("measurement_id", sa.Integer, sa.ForeignKey("measurements.id"), nullable=True),
        sa.Column("error", sa.String(1000), nullable=True),
        sa.Column("approved_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )  # fmt: skip
    op.create_index("ix_remote_jobs_workspace_id", "remote_jobs", ["workspace_id"])
    op.create_index("ix_remote_jobs_remote_device_id", "remote_jobs", ["remote_device_id"])
    op.create_index("ix_remote_jobs_project_id", "remote_jobs", ["project_id"])


def downgrade() -> None:
    op.drop_table("remote_jobs")
    op.drop_table("remote_devices")
