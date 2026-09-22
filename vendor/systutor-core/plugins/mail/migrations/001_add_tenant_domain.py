from sqlalchemy import text

revision = "001_add_tenant_domain"


def upgrade(db):
    db.execute(text("""
        ALTER TABLE tenants
        ADD COLUMN IF NOT EXISTS domain VARCHAR(255)
    """))
    db.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_tenants_domain'
            ) THEN
                ALTER TABLE tenants ADD CONSTRAINT uq_tenants_domain UNIQUE (domain);
            END IF;
        END$$;
    """))


def downgrade(db):
    db.execute(text("ALTER TABLE tenants DROP CONSTRAINT IF EXISTS uq_tenants_domain"))
    db.execute(text("ALTER TABLE tenants DROP COLUMN IF EXISTS domain"))
