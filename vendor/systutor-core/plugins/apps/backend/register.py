# ruff: noqa: E501
"""Apps plugin registration and lazy infrastructure resources."""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from plugins.apps.backend.crypto import TokenCipher
from plugins.apps.backend.dispatcher import DramatiqDispatcher
from plugins.apps.backend.models import Application, Deployment
from plugins.apps.backend.oauth import GithubHttp, OAuthFlow, RedisStateStore, UrllibGithubHttp
from plugins.apps.backend.router import router
from plugins.apps.backend.settings import AppsSettings
from systutor.contracts.domain_ownership import OwnedSite, register_site_provider
from systutor.sdk.context import PluginContext


@dataclass
class AppsResources:
    settings: AppsSettings
    dispatcher: object
    github_http: GithubHttp
    _cipher: TokenCipher | None = None

    def cipher(self) -> TokenCipher:
        if self._cipher is None:
            value = self.settings.read_secret(self.settings.encryption_key_file)
            self._cipher = TokenCipher.from_text(value, self.settings.key_version)
        return self._cipher

    def oauth(self) -> OAuthFlow:
        import redis

        secret = self.settings.read_secret(self.settings.github_client_secret_file)
        return OAuthFlow(
            RedisStateStore(redis.Redis.from_url(self.settings.redis_url)), self.github_http,
            self.settings.github_client_id, secret, self.settings.oauth_callback_url,
        )


def resolve_apps_sites(db: Session, tenant_id: str | None) -> list[OwnedSite]:
    stmt = select(Application).where(Application.deleted_at.is_(None))
    if tenant_id is not None:
        stmt = stmt.where(Application.tenant_id == tenant_id)
    result: list[OwnedSite] = []
    for app in db.scalars(stmt):
        if not app.active_deployment_id or not app.ingress_service:
            continue
        deployment = db.get(Deployment, app.active_deployment_id)
        containers = (deployment.manifest or {}).get("containers", {}) if deployment else {}
        container = containers.get(app.ingress_service)
        if container:
            result.append(OwnedSite(app.id, app.tenant_id, container, app.domain))
    return result


def register(context: PluginContext) -> None:
    settings = AppsSettings.from_env()
    if os.environ.get("SYSTUTOR_ENV") == "production":
        if not settings.oauth_callback_url.startswith("https://") or "*" in settings.oauth_callback_url:
            raise RuntimeError("Apps production OAuth callback must be an exact HTTPS URL")
    context.register_router(router)
    app = context.router_registry
    if app is not None:
        app.state.apps_resources = AppsResources(
            settings, DramatiqDispatcher(), UrllibGithubHttp()
        )
        register_site_provider(app, "apps", resolve_apps_sites)
