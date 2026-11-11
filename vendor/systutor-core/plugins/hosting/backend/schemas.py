"""Request and response contracts for the hosting routes.

The requests use `extra="forbid"` on purpose: a client cannot smuggle a
`tenant_id`, a container name or any other Docker selector into the body. The
container is always resolved from the owned `hosting_sites` row.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .profiles import (
    validate_profile_command,
    validate_profile_env,
    validate_profile_id,
    validate_profile_image,
    validate_profile_port,
    validate_profile_web_root,
)


class CreateSiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["wordpress", "static"] = "wordpress"
    name: str = Field(min_length=3, max_length=100)
    domain: str = Field(min_length=4, max_length=253)
    # Only meaningful for `kind="static"`; the tenant picks an id from the
    # curated catalog, never an image or runtime parameter of its own (I3).
    profile: str = "nginx"

    @field_validator("profile")
    @classmethod
    def valid_profile(cls, value: str, info) -> str:
        value = value.strip()
        if info.data.get("kind") == "wordpress" and value != "nginx":
            raise ValueError("The profile is only accepted for static sites")
        if not value or len(value) > 32:
            raise ValueError("Invalid profile")
        import re

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
            raise ValueError("Invalid profile")
        return value


    @field_validator("domain")
    @classmethod
    def valid_domain(cls, value: str) -> str:
        import re

        value = value.strip().lower()
        if not re.fullmatch(
            r"(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+",
            value,
        ):
            raise ValueError("Invalid domain")
        return value


class CreateSiteResponse(BaseModel):
    site: HostingSiteSummary
    admin_password: str | None = None


class SiteLogsResponse(BaseModel):
    logs: str


class SiteConfigResponse(BaseModel):
    """The editable configuration plus the NAMES of the secrets that exist.

    Secret values are never returned. `editable_env` only carries whitelisted
    keys, so the client cannot echo back the database credentials.
    """

    site_id: str
    container_name: str
    editable_env: dict[str, str]
    secret_names: list[str]
    editable_keys: list[str]


class SiteConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    env: dict[str, str] = Field(default_factory=dict)


class SecretRotationResponse(BaseModel):
    site_id: str
    container_name: str
    rotated: list[str]


class SiteDatabaseResponse(BaseModel):
    """The site's own database credential, returned by an explicit POST.

    It is the user WordPress already uses, scoped to this site's database. The
    route sends `no-store`; the password never reaches an audit row or a URL.
    """

    site_id: str
    container_name: str
    host: str
    database: str
    user: str
    password: str
    url: str | None


class PhpMyAdminSessionResponse(BaseModel):
    url: str
    expires_in: int


class HostingSiteSummary(BaseModel):
    id: str
    container_name: str
    domain: str | None
    status: str
    managed: bool
    kind: str = "wordpress"
    profile: str | None = None


class HostingSiteListResponse(BaseModel):
    sites: list[HostingSiteSummary]


class BackupExportResponse(BaseModel):
    url: str
    expires_in: int
    filename: str


class BackupImportResponse(BaseModel):
    site_id: str
    container_name: str
    restored: bool


class WordPressUserSummary(BaseModel):
    id: int
    login: str
    email: str
    status: int
    role: str


class WordPressUserListResponse(BaseModel):
    site_id: str
    container_name: str
    users: list[WordPressUserSummary]


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str | None = Field(default=None, min_length=8)


class ResetPasswordResponse(BaseModel):
    site_id: str
    user_id: int
    login: str
    generated: bool
    password: str | None = None


# --- A.SPEC 0042: profiles ------------------------------------------------


class StaticProfileSummary(BaseModel):
    """A profile for the UI. NEVER carries env_json values, only their names."""

    id: str
    name: str
    image: str
    web_root: str
    container_port: int
    health_mode: str
    env_keys: list[str] = []
    command: str | None = None
    builtin: bool = False


class StaticProfileListResponse(BaseModel):
    profiles: list[StaticProfileSummary]


class StaticProfileCreate(BaseModel):
    """Every invalid shape becomes a Pydantic 422 (all cases in the spec)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(min_length=1, max_length=120)
    image: str
    web_root: str
    container_port: int = 80
    health_mode: Literal["marker", "http_200"] = "marker"
    env_json: dict[str, str] | None = None
    command: str | None = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return validate_profile_id(value)

    @field_validator("image")
    @classmethod
    def valid_image(cls, value: str) -> str:
        return validate_profile_image(value)

    @field_validator("web_root")
    @classmethod
    def valid_web_root(cls, value: str) -> str:
        return validate_profile_web_root(value)

    @field_validator("container_port")
    @classmethod
    def valid_port(cls, value: int) -> int:
        return validate_profile_port(value)

    @field_validator("env_json")
    @classmethod
    def valid_env(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return validate_profile_env(value)

    @field_validator("command")
    @classmethod
    def valid_command(cls, value: str | None) -> str | None:
        return validate_profile_command(value)


class StaticProfileUpdate(BaseModel):
    """Partial update; only the PROVIDED fields are validated and changed."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    image: str | None = None
    web_root: str | None = None
    container_port: int | None = None
    health_mode: Literal["marker", "http_200"] | None = None
    env_json: dict[str, str] | None = None
    command: str | None = None

    @field_validator("image")
    @classmethod
    def valid_image(cls, value: str | None) -> str | None:
        return None if value is None else validate_profile_image(value)

    @field_validator("web_root")
    @classmethod
    def valid_web_root(cls, value: str | None) -> str | None:
        return None if value is None else validate_profile_web_root(value)

    @field_validator("container_port")
    @classmethod
    def valid_port(cls, value: int | None) -> int | None:
        return None if value is None else validate_profile_port(value)

    @field_validator("env_json")
    @classmethod
    def valid_env(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return validate_profile_env(value)

    @field_validator("command")
    @classmethod
    def valid_command(cls, value: str | None) -> str | None:
        return validate_profile_command(value)
