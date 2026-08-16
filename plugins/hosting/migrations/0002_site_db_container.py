from sqlalchemy import text
from sqlalchemy.orm import Session

revision = "0002"


def upgrade(db: Session) -> None:
    db.execute(
        text(
            "ALTER TABLE hosting_site "
            "ADD COLUMN IF NOT EXISTS db_container_name VARCHAR(255)"
        )
    )


def downgrade(db: Session) -> None:
    db.execute(
        text("ALTER TABLE hosting_site DROP COLUMN IF EXISTS db_container_name")
    )
