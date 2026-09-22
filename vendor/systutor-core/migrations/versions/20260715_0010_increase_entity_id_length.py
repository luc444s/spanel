"""increase entity_id length from 100 to 255

Revision ID: 20260715_0010
Revises: 20260708_0008
Create Date: 2026-07-15 04:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_0010"
down_revision = "20260708_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("audit_logs", "entity_id", type_=sa.String(255), existing_type=sa.String(100))
    op.alter_column("event_logs", "entity_id", type_=sa.String(255), existing_type=sa.String(100))
    op.alter_column(
        "core_document_versions", "entity_id", type_=sa.String(255), existing_type=sa.String(100)
    )


def downgrade() -> None:
    op.alter_column(
        "core_document_versions", "entity_id", type_=sa.String(100), existing_type=sa.String(255)
    )
    op.alter_column("event_logs", "entity_id", type_=sa.String(100), existing_type=sa.String(255))
    op.alter_column("audit_logs", "entity_id", type_=sa.String(100), existing_type=sa.String(255))
