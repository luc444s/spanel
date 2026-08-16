from sqlalchemy import text
from sqlalchemy.orm import Session

revision = "0004"


def upgrade(db: Session) -> None:
    db.execute(
        text(
            "ALTER TABLE hosting_site "
            "ADD COLUMN IF NOT EXISTS admin_email VARCHAR(255)"
        )
    )


def downgrade(db: Session) -> None:
    db.execute(
        text("ALTER TABLE hosting_site DROP COLUMN IF EXISTS admin_email")
    )
