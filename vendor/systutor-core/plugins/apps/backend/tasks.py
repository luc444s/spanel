"""Dramatiq entrypoint for the dedicated serialized Apps worker."""

from __future__ import annotations

import logging

from sqlalchemy import select

from systutor.contracts.site_domain_publisher import get_site_domain_publisher
from systutor.core.config import get_settings
from systutor.core.database import build_session_factory
from systutor.kernel.tasks.broker import configure_dramatiq_broker

from .crypto import TokenCipher
from .dispatcher import DramatiqDispatcher
from .github import UrllibGithubClient
from .models import Operation
from .operations import reconcile
from .settings import AppsSettings
from .worker import AppsWorker, build_actor

settings = AppsSettings.from_env()
core_settings = get_settings()
configure_dramatiq_broker(core_settings)
session_factory = build_session_factory(core_settings)
cipher = TokenCipher.from_text(
    settings.read_secret(settings.encryption_key_file), settings.key_version
)
route_cleanup = None
route_refresh = None
try:
    from app.main import app

    publisher = get_site_domain_publisher(app)
    route_cleanup = getattr(app.state, "domain_routes_cleanup", None)
    route_refresh = getattr(app.state, "domain_routes_refresh", None)
except Exception:
    publisher = None
worker = AppsWorker(
    session_factory, cipher, publisher=publisher, github=UrllibGithubClient(),
    route_cleanup=route_cleanup, route_refresh=route_refresh,
)


def _recover() -> None:
    with session_factory() as db:
        reconcile(db, DramatiqDispatcher())
        active = set(db.scalars(select(Operation.id).where(
            Operation.status.in_(("queued", "running"))
        )).all())
    pending = settings.state_root / "pending"
    if pending.exists():
        for path in pending.glob("*.env"):
            if path.stem not in active:
                path.unlink(missing_ok=True)


_recover()
logger = logging.getLogger(__name__)
try:
    worker.reconcile_routes()
except Exception:  # noqa: BLE001 - el worker debe arrancar aunque falle el proxy
    logger.warning("apps_route_reconcile_failed", exc_info=True)
apps_operation = build_actor(worker.run)

__all__ = ["apps_operation"]
