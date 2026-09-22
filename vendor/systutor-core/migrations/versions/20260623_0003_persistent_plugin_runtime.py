"""persistent plugin runtime

Revision ID: 20260623_0003
Revises: 20260622_0002
Create Date: 2026-06-23 03:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260623_0003"
down_revision = "20260622_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plugin_registry") as batch_op:
        batch_op.add_column(sa.Column("state", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("migration_version", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_error", sa.String(length=500), nullable=True))

    op.execute("UPDATE plugin_registry SET state = status")
    op.execute("UPDATE plugin_registry SET last_error = error_message")
    op.execute(
        "UPDATE plugin_registry SET installed_at = created_at "
        "WHERE state IN ('installed', 'enabled', 'disabled')"
    )
    op.execute("UPDATE plugin_registry SET enabled_at = updated_at WHERE state = 'enabled'")
    op.execute("UPDATE plugin_registry SET disabled_at = updated_at WHERE state = 'disabled'")

    with op.batch_alter_table("plugin_registry") as batch_op:
        batch_op.alter_column("state", existing_type=sa.String(length=30), nullable=False)
        batch_op.alter_column(
            "is_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.false(),
        )
        batch_op.drop_column("status")
        batch_op.drop_column("error_message")


def downgrade() -> None:
    with op.batch_alter_table("plugin_registry") as batch_op:
        batch_op.add_column(sa.Column("status", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("error_message", sa.String(length=500), nullable=True))

    op.execute("UPDATE plugin_registry SET status = state")
    op.execute("UPDATE plugin_registry SET error_message = last_error")

    with op.batch_alter_table("plugin_registry") as batch_op:
        batch_op.alter_column("status", existing_type=sa.String(length=30), nullable=False)
        batch_op.drop_column("last_error")
        batch_op.drop_column("disabled_at")
        batch_op.drop_column("enabled_at")
        batch_op.drop_column("installed_at")
        batch_op.drop_column("migration_version")
        batch_op.drop_column("state")
        batch_op.alter_column(
            "is_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.true(),
        )
