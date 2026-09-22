"""runtime outbox and plugin registry errors

Revision ID: 20260622_0002
Revises: 20260620_0001
Create Date: 2026-06-22 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260622_0002"
down_revision = "20260620_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plugin_registry",
        sa.Column("error_message", sa.String(length=500), nullable=True),
    )

    op.create_table(
        "event_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_log_id", sa.String(length=36), nullable=False),
        sa.Column("event_name", sa.String(length=150), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("correlation_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["event_log_id"], ["event_logs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_log_id"),
    )
    op.create_index("ix_event_outbox_created_at", "event_outbox", ["created_at"], unique=False)
    op.create_index(
        "ix_event_outbox_correlation_id",
        "event_outbox",
        ["correlation_id"],
        unique=False,
    )
    op.create_index("ix_event_outbox_event_name", "event_outbox", ["event_name"], unique=False)
    op.create_index("ix_event_outbox_status", "event_outbox", ["status"], unique=False)
    op.create_index("ix_event_outbox_tenant_id", "event_outbox", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_event_outbox_tenant_id", table_name="event_outbox")
    op.drop_index("ix_event_outbox_status", table_name="event_outbox")
    op.drop_index("ix_event_outbox_event_name", table_name="event_outbox")
    op.drop_index("ix_event_outbox_correlation_id", table_name="event_outbox")
    op.drop_index("ix_event_outbox_created_at", table_name="event_outbox")
    op.drop_table("event_outbox")
    op.drop_column("plugin_registry", "error_message")
