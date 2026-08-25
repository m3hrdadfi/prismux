"""Store model rates per one million tokens and correct historical costs.

Revision ID: 20260825_0003
Revises: 20260825_0002
"""
from alembic import op

revision = "20260825_0003"
down_revision = "20260825_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve the configured numeric values: a stored 0.35 now means
    # $0.35 per 1M instead of the previously assumed $0.35 per 1K.
    op.execute("ALTER TABLE prismux.model_pricing RENAME COLUMN input_per_1k TO input_per_1m")
    op.execute("ALTER TABLE prismux.model_pricing RENAME COLUMN output_per_1k TO output_per_1m")

    # Every historical priced request used the same rate values with a
    # denominator that was 1,000x too small. Correct both detailed and
    # statistics retention tables atomically with the schema migration.
    for table in ("requests", "request_stats"):
        op.execute(
            f"""UPDATE prismux.{table}
                SET input_cost = input_cost / 1000.0,
                    output_cost = output_cost / 1000.0,
                    estimated_cost = estimated_cost / 1000.0
                WHERE input_cost IS NOT NULL
                   OR output_cost IS NOT NULL"""
        )


def downgrade() -> None:
    for table in ("requests", "request_stats"):
        op.execute(
            f"""UPDATE prismux.{table}
                SET input_cost = input_cost * 1000.0,
                    output_cost = output_cost * 1000.0,
                    estimated_cost = estimated_cost * 1000.0
                WHERE input_cost IS NOT NULL
                   OR output_cost IS NOT NULL"""
        )
    op.execute("ALTER TABLE prismux.model_pricing RENAME COLUMN output_per_1m TO output_per_1k")
    op.execute("ALTER TABLE prismux.model_pricing RENAME COLUMN input_per_1m TO input_per_1k")
