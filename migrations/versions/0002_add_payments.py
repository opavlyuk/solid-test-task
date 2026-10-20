"""Add payment attempts with per-user idempotency and cart blocking.

Allow retries after confirmed failures while preventing multiple pending,
successful, or uncertain attempts for the same cart.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("cart_id", sa.Uuid(), nullable=False),
        sa.Column("payment_method_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("provider_reference", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'failed', 'unknown') "
            "AND provider_reference IS NULL) OR "
            "(status = 'succeeded' AND provider_reference IS NOT NULL)",
            name="ck_payments_result",
        ),
        sa.CheckConstraint("currency = 'USD'", name="ck_payments_currency"),
        sa.CheckConstraint(
            "amount > 0 AND amount <= 9999999999.99", name="ck_payments_amount"
        ),
        sa.ForeignKeyConstraint(
            ["cart_id"],
            ["carts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["payment_method_id"],
            ["user_payment_methods.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_payments_user_key"),
    )
    op.create_index(
        "uq_payments_blocking_cart",
        "payments",
        ["cart_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'succeeded', 'unknown')"),
    )


def downgrade() -> None:
    op.drop_table("payments")
