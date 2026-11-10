# ruff: noqa: E501
"""Apps plugin registration and lazy infrastructure resources."""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from plugins.apps.backend.crypto import TokenCipher
from plugins.apps.backend.dispatcher import DramatiqDispatcher
from plugins.apps.backend.github import GithubClient, UrllibGithubClient
from plugins.apps.backend.models import Application, Deployment
from plugins.apps.backend.public_url import PublicUrlError, parse_public_url
from plugins.apps.backend.router import router
from plugins.apps.backend.settings import AppsSettings
from plugins.apps.backend.states import OneTimeStates
from systutor.contracts.domain_ownership import OwnedSite, register_site_provider
from systutor.sdk.context import PluginContext


@dataclass
class AppsResources:
    settings: AppsSettings
    dispatcher: object
    github: GithubClient
    _cipher: TokenCipher | None = None
    _states: OneTimeStates | None = None

    def cipher(self) -> TokenCipher:
        if self._cipher is None:
            value = self.settings.read_secret(self.settings.encryption_key_file)
            self._cipher = TokenCipher.from_text(value, self.settings.key_version)
        return self._cipher

    def states(self) -> OneTimeStates:
        if self._states is None:
            import redis

            self._states = OneTimeStates(redis.Redis.from_url(self.settings.redis_url))
        return self._states


def resolve_apps_sites(db: Session, tenant_id: str | None) -> list[OwnedSite]:
    """Stable service identities for Domains routes.

    The site id is ``<application_id>:<service>`` and never a container name.
    Domains resolves the active container from the deployment manifest every
    time it publishes or reads a route, so redeploys can replace containers
    without changing route ownership.
    """
    stmt = select(Application).where(Application.deleted_at.is_(None))
    if tenant_id is not None:
        stmt = stmt.where(Application.tenant_id == tenant_id)
    result: list[OwnedSite] = []
    for app in db.scalars(stmt):
        if not app.active_deployment_id:
            continue
        deployment = db.get(Deployment, app.active_deployment_id)
        containers = (deployment.manifest or {}).get("containers", {}) if deployment else {}
        for service, container in sorted(containers.items()):
            if not container:
                continue
            result.append(
                OwnedSite(
                    f"{app.id}:{service}",
                    app.tenant_id,
                    container,
                    app.domain if service == app.ingress_service else None,
                )
            )
    return result


def register(context: PluginContext) -> None:
    settings = AppsSettings.from_env()
    production = os.environ.get("SYSTUTOR_ENV") == "production"
    if production and settings.public_url:
        try:
            parse_public_url(settings.public_url, production=True)
        except PublicUrlError as exc:
            raise RuntimeError(str(exc)) from exc
    context.register_router(router)
    app = context.router_registry
    if app is not None:
        app.state.apps_resources = AppsResources(
            settings, DramatiqDispatcher(), UrllibGithubClient()
        )
        register_site_provider(app, "apps", resolve_apps_sites)
