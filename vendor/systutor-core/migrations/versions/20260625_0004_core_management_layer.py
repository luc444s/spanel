"""core management layer role activation

Revision ID: 20260625_0004
Revises: 20260623_0003
Create Date: 2026-06-25 14:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260625_0004"
down_revision = "20260623_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("roles") as batch_op:
        batch_op.add_column(
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("roles") as batch_op:
        batch_op.drop_column("is_active")
