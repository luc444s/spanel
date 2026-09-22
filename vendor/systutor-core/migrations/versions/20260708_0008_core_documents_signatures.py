"""core documents and signatures

Revision ID: 20260708_0008
Revises: 20260629_0005
Create Date: 2026-07-08 23:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260708_0008"
down_revision = "20260629_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "core_document_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("module", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.String(length=100), nullable=False),
        sa.Column("template_code", sa.String(length=100), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "module",
            "entity_type",
            "entity_id",
            "version_number",
            name="uq_core_document_version_entity",
        ),
    )
    op.create_index(
        op.f("ix_core_document_versions_tenant_id"),
        "core_document_versions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_core_document_versions_module"),
        "core_document_versions",
        ["module"],
        unique=False,
    )
    op.create_index(
        op.f("ix_core_document_versions_entity_type"),
        "core_document_versions",
        ["entity_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_core_document_versions_entity_id"),
        "core_document_versions",
        ["entity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_core_document_versions_status"),
        "core_document_versions",
        ["status"],
        unique=False,
    )

    op.create_table(
        "core_signature_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=False),
        sa.Column("signer_name", sa.String(length=200), nullable=True),
        sa.Column("signer_email", sa.String(length=200), nullable=True),
        sa.Column("signer_phone", sa.String(length=50), nullable=True),
        sa.Column("signer_role", sa.String(length=50), nullable=True),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("verification_channel", sa.String(length=30), nullable=False),
        sa.Column("verification_ref", sa.String(length=120), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_version_id"], ["core_document_versions.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_core_signature_sessions_tenant_id"),
        "core_signature_sessions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_core_signature_sessions_document_version_id"),
        "core_signature_sessions",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_core_signature_sessions_status"),
        "core_signature_sessions",
        ["status"],
        unique=False,
    )

    op.create_table(
        "core_signature_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("signature_session_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_type", sa.String(length=50), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["signature_session_id"], ["core_signature_sessions.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_core_signature_evidence_tenant_id"),
        "core_signature_evidence",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_core_signature_evidence_signature_session_id"),
        "core_signature_evidence",
        ["signature_session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_core_signature_evidence_signature_session_id"),
        table_name="core_signature_evidence",
    )
    op.drop_index(op.f("ix_core_signature_evidence_tenant_id"), table_name="core_signature_evidence")
    op.drop_table("core_signature_evidence")

    op.drop_index(op.f("ix_core_signature_sessions_status"), table_name="core_signature_sessions")
    op.drop_index(
        op.f("ix_core_signature_sessions_document_version_id"),
        table_name="core_signature_sessions",
    )
    op.drop_index(op.f("ix_core_signature_sessions_tenant_id"), table_name="core_signature_sessions")
    op.drop_table("core_signature_sessions")

    op.drop_index(op.f("ix_core_document_versions_status"), table_name="core_document_versions")
    op.drop_index(op.f("ix_core_document_versions_entity_id"), table_name="core_document_versions")
    op.drop_index(op.f("ix_core_document_versions_entity_type"), table_name="core_document_versions")
    op.drop_index(op.f("ix_core_document_versions_module"), table_name="core_document_versions")
    op.drop_index(op.f("ix_core_document_versions_tenant_id"), table_name="core_document_versions")
    op.drop_table("core_document_versions")
