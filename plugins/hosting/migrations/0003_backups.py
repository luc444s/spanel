from sqlalchemy import text
from sqlalchemy.orm import Session

revision = "0003"


def upgrade(db: Session) -> None:
    db.execute(
        text(
            "ALTER TABLE hosting_site "
            "ADD COLUMN IF NOT EXISTS db_password VARCHAR(255)"
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS hosting_backup (
                id VARCHAR(36) PRIMARY KEY,
                site_id VARCHAR(36) NOT NULL,
                kind VARCHAR(16) NOT NULL,
                path VARCHAR(512) NOT NULL,
                size BIGINT,
                status VARCHAR(16) NOT NULL,
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
            )
            """
        )
    )
    db.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_hosting_backup_site "
            "ON hosting_backup (site_id)"
        )
    )


def downgrade(db: Session) -> None:
    db.execute(text("DROP TABLE IF EXISTS hosting_backup"))
    db.execute(
        text("ALTER TABLE hosting_site DROP COLUMN IF EXISTS db_password")
    )
