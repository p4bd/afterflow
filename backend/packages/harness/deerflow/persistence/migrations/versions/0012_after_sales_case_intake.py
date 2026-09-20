"""Persist complaint intake and resumable case state.

Revision ID: 0012_after_sales_case_intake
Revises: 0011_case_event_seq_unique
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_after_sales_case_intake"
down_revision: str | Sequence[str] | None = "0011_case_event_seq_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("service_cases"):
        return
    with op.batch_alter_table("service_cases") as batch:
        batch.alter_column("order_id", existing_type=sa.String(64), nullable=True)
        batch.alter_column("issue_type", existing_type=sa.String(32), nullable=True)
        batch.add_column(sa.Column("complaint_text", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("customer_expectation", sa.String(32), nullable=True))
        batch.add_column(sa.Column("next_step", sa.String(64), nullable=False, server_default="review_decision"))
        batch.add_column(sa.Column("reply_draft", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("service_cases") as batch:
        batch.drop_column("reply_draft")
        batch.drop_column("next_step")
        batch.drop_column("customer_expectation")
        batch.drop_column("complaint_text")
        batch.alter_column("issue_type", existing_type=sa.String(32), nullable=False)
        batch.alter_column("order_id", existing_type=sa.String(64), nullable=False)
