"""User presentation preferences (display mode, onboarding progress).

Revision ID: 0006_user_preferences
Revises: 0005_execution_jobs
Create Date: 2026-10-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_user_preferences"
down_revision = "0005_execution_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("preferences", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("users", "preferences")
