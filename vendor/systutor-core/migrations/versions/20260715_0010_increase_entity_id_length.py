"""increase entity_id length from 100 to 255

Revision ID: 20260715_0010
Revises: 20260708_0008
Create Date: 2026-07-15 04:10:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "20260715_0010"
down_revision = "20260708_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("audit_logs", "event_logs", "core_document_versions"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "entity_id", type_=sa.String(255), existing_type=sa.String(100)
            )


def downgrade() -> None:
    for table_name in ("core_document_versions", "event_logs", "audit_logs"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "entity_id", type_=sa.String(100), existing_type=sa.String(255)
            )
