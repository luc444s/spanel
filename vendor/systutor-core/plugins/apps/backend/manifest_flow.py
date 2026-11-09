"""GitHub App Manifest registration and installation callbacks."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from systutor.core.errors import AppError
from systutor.kernel.auth.models import User

from .catalog import audit, mark_installation_suspended
from .crypto import EncryptedValue, TokenCipher
from .github import (
    GithubClient,
    GithubError,
    app_jwt_for,
    validate_installation,
    validate_manifest_app,
)
from .models import GithubInstallation, GithubProvider
from .operations import create_operation, dispatch_operation
from .public_url import install_url, manifest_action_url, parse_public_url
from .schemas import ManifestStartRequest
from .settings import AppsSettings
from .states import INSTALLATION, REGISTRATION, OneTimeStates, StateInvalid, StateUnavailable

PEM_PURPOSE = "github-app-private-key"
REDIRECT_ERROR = "/app/apps?github=error"


class ManifestFlow:
    def __init__(
        self,
        db: Session,
        *,
        settings: AppsSettings,
        states: OneTimeStates,
        github: GithubClient,
        cipher: TokenCipher,
        dispatcher: object,
        production: bool,
    ) -> None:
        self.db = db
        self.settings = settings
        self.states = states
        self.github = github
        self.cipher = cipher
        self.dispatcher = dispatcher
        self.production = production

    def _origin(self) -> str:
        try:
            return parse_public_url(
                self.settings.public_url, production=self.production
            ).origin
        except ValueError as exc:
            raise AppError(
                "Apps public URL is not configured", status_code=503,
                code="apps.not_configured",
            ) from exc

    def start(self, user: User, body: ManifestStartRequest) -> dict:
        origin = self._origin()
        provider_id = str(uuid4())
        try:
            registration_state = self.states.issue(REGISTRATION, {
                "user_id": user.id, "tenant_id": user.tenant_id,
                "owner_type": body.owner_type,
                "organization": body.organization,
                "provider_id": provider_id,
                "redirect": "/app/apps",
            })
            installation_state = self.states.issue(INSTALLATION, {
                "user_id": user.id, "tenant_id": user.tenant_id,
                "provider_id": provider_id,
                "owner_type": body.owner_type,
                "organization": body.organization,
                "redirect": "/app/apps",
            })
        except StateUnavailable as exc:
            raise AppError(
                "GitHub state store unavailable", status_code=503,
                code="apps.state_unavailable",
            ) from exc
        manifest = {
            "name": f"spanel-apps-{provider_id[:8]}",
            "url": origin,
            "redirect_url": f"{origin}/api/v1/plugins/apps/github/manifest/callback",
            "setup_url": (
                f"{origin}/api/v1/plugins/apps/github/install/callback"
                f"?state={installation_state}"
            ),
            "description": "SPanel tenant application deployments",
            "public": False,
            "request_oauth_on_install": False,
            "default_permissions": {"contents": "read", "metadata": "read"},
            "default_events": [],
        }
        audit(
            self.db, "github.manifest.started", tenant_id=user.tenant_id,
            actor=user.id, entity_id=provider_id,
            details={"owner_type": body.owner_type, "provider_id": provider_id},
        )
        self.db.commit()
        return {
            "action_url": manifest_action_url(body.owner_type, body.organization),
            "state": registration_state,
            "manifest": manifest,
        }

    def conversion(self, code: str, state: str) -> str:
        try:
            payload = self.states.consume(REGISTRATION, state)
        except StateInvalid:
            return REDIRECT_ERROR
        except StateUnavailable as exc:
            raise AppError(
                "GitHub state store unavailable", status_code=503,
                code="apps.state_unavailable",
            ) from exc
        tenant_id = str(payload.get("tenant_id", ""))
        user_id = str(payload.get("user_id", ""))
        provider_id = str(payload.get("provider_id", ""))
        user = self.db.get(User, user_id)
        if (
            user is None or not user.is_active or user.tenant_id != tenant_id
            or not provider_id or not tenant_id
        ):
            return REDIRECT_ERROR
        try:
            data = self.github.exchange_manifest(code)
            app = validate_manifest_app(data)
        except (GithubError, ValueError):
            audit(
                self.db, "github.manifest.failed", tenant_id=tenant_id,
                actor=user_id, entity_id=provider_id, result="failure",
                details={"code": "conversion_failed"},
            )
            self.db.commit()
            return REDIRECT_ERROR
        if self.db.scalar(select(GithubProvider.id).where(
            GithubProvider.github_app_id == app["github_app_id"]
        )) is not None:
            audit(
                self.db, "github.manifest.failed", tenant_id=tenant_id,
                actor=user_id, entity_id=provider_id, result="failure",
                details={"code": "app_already_registered"},
            )
            self.db.commit()
            return REDIRECT_ERROR
        encrypted = self.cipher.encrypt_scoped(
            PEM_PURPOSE, tenant_id, provider_id, app["pem"]
        )
        provider = GithubProvider(
            id=provider_id,
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            github_app_id=app["github_app_id"],
            app_slug=app["app_slug"],
            name=app["name"],
            owner_login=app["owner_login"],
            owner_type=app["owner_type"] or payload.get("owner_type"),
            private_key_ciphertext=encrypted.ciphertext,
            private_key_nonce=encrypted.nonce,
            key_version=encrypted.key_version,
            status="pending_installation",
            last_used_at=datetime.now(UTC),
        )
        self.db.add(provider)
        audit(
            self.db, "github.provider.registered", tenant_id=tenant_id,
            actor=user_id, entity_id=provider_id,
            details={
                "github_app_id": app["github_app_id"],
                "app_slug": app["app_slug"],
            },
        )
        self.db.commit()
        return install_url(app["app_slug"])

    def installation(
        self, installation_id: str, state: str, setup_action: str | None
    ) -> str:
        try:
            payload = self.states.consume(INSTALLATION, state)
        except StateInvalid:
            return REDIRECT_ERROR
        except StateUnavailable as exc:
            raise AppError(
                "GitHub state store unavailable", status_code=503,
                code="apps.state_unavailable",
            ) from exc
        tenant_id = str(payload.get("tenant_id", ""))
        user_id = str(payload.get("user_id", ""))
        provider_id = str(payload.get("provider_id", ""))
        provider = self.db.scalar(select(GithubProvider).where(
            GithubProvider.id == provider_id,
            GithubProvider.tenant_id == tenant_id,
        ))
        user = self.db.get(User, user_id)
        if (
            provider is None or user is None or not user.is_active
            or user.tenant_id != tenant_id or provider.status == "revoked"
        ):
            return REDIRECT_ERROR
        if setup_action == "cancel":
            audit(
                self.db, "github.install.cancelled", tenant_id=tenant_id,
                actor=user_id, entity_id=provider_id, result="failure",
                details={"code": "cancelled"},
            )
            self.db.commit()
            return REDIRECT_ERROR
        try:
            github_id = int(installation_id)
        except (TypeError, ValueError):
            return REDIRECT_ERROR
        if github_id <= 0:
            return REDIRECT_ERROR
        try:
            pem = self.cipher.decrypt_scoped(
                PEM_PURPOSE, tenant_id, provider.id,
                EncryptedValue(
                    provider.private_key_ciphertext, provider.private_key_nonce,
                    provider.key_version,
                ),
            )
            jwt = app_jwt_for(pem, provider.github_app_id)
            data = self.github.installation(jwt, github_id)
            verified = validate_installation(
                data, app_id=provider.github_app_id, installation_id=github_id
            )
        except Exception:
            audit(
                self.db, "github.install.failed", tenant_id=tenant_id,
                actor=user_id, entity_id=provider_id, result="failure",
                details={"code": "verification_failed"},
            )
            self.db.commit()
            return REDIRECT_ERROR
        existing = self.db.scalar(select(GithubInstallation).where(
            GithubInstallation.github_installation_id == github_id
        ))
        if existing is not None and existing.provider_id != provider.id:
            audit(
                self.db, "github.install.failed", tenant_id=tenant_id,
                actor=user_id, entity_id=provider_id, result="failure",
                details={"code": "installation_conflict"},
            )
            self.db.commit()
            return REDIRECT_ERROR
        now = datetime.now(UTC)
        installation = existing or GithubInstallation(
            provider_id=provider.id,
            tenant_id=tenant_id,
            github_installation_id=github_id,
            installed_at=now,
        )
        installation.provider_id = provider.id
        installation.tenant_id = tenant_id
        installation.account_id = verified["account_id"]
        installation.account_login = verified["account_login"]
        installation.account_type = verified["account_type"]
        installation.repository_selection = verified["repository_selection"]
        installation.permissions = verified["permissions"]
        installation.status = "active"
        installation.suspended_at = None
        installation.deleted_at = None
        provider.status = "active"
        provider.last_used_at = now
        self.db.add(installation)
        self.db.flush()
        audit(
            self.db, "github.installation.verified", tenant_id=tenant_id,
            actor=user_id, entity_id=installation.id,
            details={
                "provider_id": provider.id,
                "github_installation_id": github_id,
                "account_login": verified["account_login"],
            },
        )
        self.db.commit()
        self._queue_sync(installation, user_id, tenant_id)
        return "/app/apps?github=installed"

    def _queue_sync(
        self, installation: GithubInstallation, user_id: str, tenant_id: str
    ) -> None:
        try:
            operation, created = create_operation(
                self.db,
                application=None,
                actor_user_id=user_id,
                kind="github_sync",
                idempotency_key=f"install-sync:{uuid4()}",
                payload={},
                tenant_id=tenant_id,
                subject_type="installation",
                subject_id=installation.id,
            )
            if created:
                dispatch_operation(self.db, operation, self.dispatcher)
        except Exception:
            # The installation stays active; the status surface exposes a failed
            # sync operation and POST /installations/{id}/sync can retry it.
            self.db.rollback()


def apply_installation_observations(
    db: Session, installation: GithubInstallation, data: dict, *, now: datetime | None = None
) -> None:
    """Update a catalog installation from a verified API payload."""

    moment = now or datetime.now(UTC)
    installation.account_id = str(data.get("account_id", installation.account_id))
    installation.account_login = str(data.get("account_login", installation.account_login))
    installation.account_type = data.get("account_type", installation.account_type)
    installation.repository_selection = str(
        data.get("repository_selection", installation.repository_selection)
    )
    installation.permissions = data.get("permissions") or installation.permissions or {}
    installation.last_synced_at = moment
    if data.get("suspended"):
        mark_installation_suspended(db, installation, "suspended")
