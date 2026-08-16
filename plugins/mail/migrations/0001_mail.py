from sqlalchemy import text
from sqlalchemy.orm import Session

revision = "0001"


def upgrade(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS mail_domain (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36) NOT NULL,
                domain VARCHAR(255) NOT NULL,
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS mailbox (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36) NOT NULL,
                domain_id VARCHAR(36) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
            )
            """
        )
    )


def downgrade(db: Session) -> None:
    db.execute(text("DROP TABLE IF EXISTS mailbox"))
    db.execute(text("DROP TABLE IF EXISTS mail_domain"))
