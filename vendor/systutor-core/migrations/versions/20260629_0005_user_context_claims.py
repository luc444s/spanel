"""user context claims for warehouse scope

Revision ID: 20260629_0005
Revises: 20260625_0004
Create Date: 2026-06-29 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260629_0005"
down_revision = "20260625_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_context_claims",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("claim_type", sa.String(length=50), nullable=False),
        sa.Column("claim_value", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "claim_type", "claim_value", name="uq_user_context_claim"),
    )
    op.create_index(
        op.f("ix_user_context_claims_tenant_id"),
        "user_context_claims",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_context_claims_user_id"),
        "user_context_claims",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_context_claims_claim_type"),
        "user_context_claims",
        ["claim_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_context_claims_claim_value"),
        "user_context_claims",
        ["claim_value"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_context_claims_claim_value"), table_name="user_context_claims")
    op.drop_index(op.f("ix_user_context_claims_claim_type"), table_name="user_context_claims")
    op.drop_index(op.f("ix_user_context_claims_user_id"), table_name="user_context_claims")
    op.drop_index(op.f("ix_user_context_claims_tenant_id"), table_name="user_context_claims")
    op.drop_table("user_context_claims")
