from sqlalchemy import text
from sqlalchemy.orm import Session

revision = "0001"


def upgrade(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS hosting_site (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36) NOT NULL,
                branch_id VARCHAR(36),
                stack VARCHAR(32) NOT NULL,
                name VARCHAR(255) NOT NULL,
                container_name VARCHAR(255) NOT NULL UNIQUE,
                domains_json TEXT NOT NULL DEFAULT '[]',
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
            )
            """
        )
    )
    db.execute(
        text("CREATE INDEX IF NOT EXISTS ix_hosting_site_tenant ON hosting_site (tenant_id)")
    )


def downgrade(db: Session) -> None:
    db.execute(text("DROP TABLE IF EXISTS hosting_site"))
