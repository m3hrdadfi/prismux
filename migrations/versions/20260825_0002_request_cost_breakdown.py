"""Persist input and output cost components for request history.

Revision ID: 20260825_0002
Revises: 20260824_0001
"""
from alembic import op

revision = "20260825_0002"
down_revision = "20260824_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE prismux.requests ADD COLUMN input_cost DOUBLE PRECISION")
    op.execute("ALTER TABLE prismux.requests ADD COLUMN output_cost DOUBLE PRECISION")
    op.execute("ALTER TABLE prismux.request_stats ADD COLUMN input_cost DOUBLE PRECISION")
    op.execute("ALTER TABLE prismux.request_stats ADD COLUMN output_cost DOUBLE PRECISION")


def downgrade() -> None:
    op.execute("ALTER TABLE prismux.request_stats DROP COLUMN output_cost")
    op.execute("ALTER TABLE prismux.request_stats DROP COLUMN input_cost")
    op.execute("ALTER TABLE prismux.requests DROP COLUMN output_cost")
    op.execute("ALTER TABLE prismux.requests DROP COLUMN input_cost")
