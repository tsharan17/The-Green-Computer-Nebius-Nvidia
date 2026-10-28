"""Email verification, optional TOTP MFA, shared rate limits, workspace settings.

Revision ID: 0009_security_operations
Revises: 0008_remote_agents
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_security_operations"
down_revision = "0008_remote_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("mfa_secret", sa.String(300), nullable=True))
    op.add_column("users", sa.Column("mfa_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("mfa_last_step", sa.BigInteger, nullable=True))
    op.add_column("users", sa.Column("mfa_recovery", sa.JSON, nullable=True))
    op.add_column("workspaces", sa.Column("settings", sa.JSON, nullable=True))
    op.create_table(
        "email_verifications",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("email", sa.String(320)), sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True)), sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )  # fmt: skip
    op.create_index("ix_email_verifications_user_id", "email_verifications", ["user_id"])
    op.create_index("ix_email_verifications_token_hash", "email_verifications", ["token_hash"])
    op.create_table(
        "rate_limits",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("window_start", sa.BigInteger, primary_key=True),
        sa.Column("count", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("rate_limits")
    op.drop_table("email_verifications")
    for c in ("mfa_recovery", "mfa_last_step", "mfa_enabled_at", "mfa_secret", "email_verified_at"):
        op.drop_column("users", c)
    op.drop_column("workspaces", "settings")
