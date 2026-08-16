from sqlalchemy import text
from sqlalchemy.orm import Session

revision = "0001"


def upgrade(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS hosting_domain (
                id VARCHAR(36) PRIMARY KEY,
                site_id VARCHAR(36) NOT NULL,
                fqdn VARCHAR(255) NOT NULL UNIQUE,
                ssl_status VARCHAR(32) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
            )
            """
        )
    )


def downgrade(db: Session) -> None:
    db.execute(text("DROP TABLE IF EXISTS hosting_domain"))
