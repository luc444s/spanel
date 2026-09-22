#!/bin/sh
set -e

# Create tables if they don't exist
python -c "
from systutor.core.database import Base, register_model_metadata
from systutor.core.config import get_settings
from sqlalchemy import create_engine
register_model_metadata()
settings = get_settings()
engine = create_engine(settings.database_url)
Base.metadata.create_all(engine)
print('Tables ensured')
"

# Seed demo data if admin user doesn't exist
python -c "
from systutor.core.database import build_session_factory, register_model_metadata
from systutor.core.config import get_settings
from sqlalchemy import select
from systutor.api.seed import seed_demo_data
from systutor.kernel.auth.models import User
from systutor.kernel.plugins.runtime import PluginManifestRegistry, PluginRuntime

register_model_metadata()
settings = get_settings()
factory = build_session_factory(settings)
db = factory()

existing = db.scalar(select(User).where(User.email == settings.seed_admin_email))
if not existing:
    registry = PluginManifestRegistry(settings.plugins_dir)
    registry.discover()
    runtime = PluginRuntime(registry, context_builder=lambda m: None)
    runtime.load()
    loaded = [r for r in runtime.list_results() if r.manifest]
    result = seed_demo_data(db, settings, loaded)
    print('Seed created:', result['user_email'])
else:
    print('Seed already exists')
db.close()
"

exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
