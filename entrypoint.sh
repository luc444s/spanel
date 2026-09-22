#!/bin/sh
set -e

APP_PORT=${PORT:-3000}
APP_WORKERS=${WORKERS:-2}

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

# Seed demo data if admin user doesn't exist, then clear plugin_registry
python -c "
from systutor.core.database import build_session_factory, register_model_metadata
from systutor.core.config import get_settings
from sqlalchemy import select, text
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

db.execute(text('DELETE FROM plugin_registry'))
db.commit()
db.close()
"

# Start uvicorn in background
python -m uvicorn app.main:app --host 0.0.0.0 --port "$APP_PORT" --workers "$APP_WORKERS" &
APP_PID=$!

# Wait for app and auto-enable plugins
python -c "
import os, json, urllib.request, time

port = os.environ.get('PORT', '3000')
base = f'http://localhost:{port}'
email = os.environ.get('SYSTUTOR_SEED_ADMIN_EMAIL', '')
password = os.environ.get('SYSTUTOR_SEED_ADMIN_PASSWORD', '')

for _ in range(30):
    try:
        urllib.request.urlopen(f'{base}/api/v1/system/health', timeout=2)
        break
    except Exception:
        time.sleep(1)

try:
    data = json.dumps({'email': email, 'password': password}).encode()
    req = urllib.request.Request(f'{base}/api/v1/auth/login', data=data, headers={'Content-Type': 'application/json'})
    resp = json.loads(urllib.request.urlopen(req).read())
    token = resp.get('access_token')
    if not token:
        exit(0)

    req = urllib.request.Request(f'{base}/api/v1/core/plugins', headers={'Authorization': f'Bearer {token}'})
    plugins = json.loads(urllib.request.urlopen(req).read())
    for p in plugins:
        if p['state'] == 'validated' and not p['is_enabled']:
            req = urllib.request.Request(f'{base}/api/v1/core/plugins/{p[\"plugin_id\"]}/enable', method='POST', headers={'Authorization': f'Bearer {token}'})
            urllib.request.urlopen(req)
            print(f'Enabled: {p[\"plugin_id\"]}')
except Exception as e:
    print(f'Plugin enable: {e}')
" 2>/dev/null || true

wait $APP_PID
