"""Record which version of the terms and privacy policy each account accepted, and when.

Revision ID: 0010_terms_acceptance
Revises: 0009_security_operations
Create Date: 2026-10-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_terms_acceptance"
down_revision = "0009_security_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "terms_acceptances",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("document", sa.String(40), nullable=False), sa.Column("version", sa.String(40), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "document", "version", name="uq_terms_acceptance"),
    )  # fmt: skip
    op.create_index("ix_terms_acceptances_user_id", "terms_acceptances", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_terms_acceptances_user_id", table_name="terms_acceptances")
    op.drop_table("terms_acceptances")
