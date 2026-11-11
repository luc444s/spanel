"""Pure validation and resolution of static profiles (A.SPEC 0042).

No Docker daemon, no database, no settings factory: everything the caller
passes in, everything testable. The 422 of every invalid shape comes from
the schemas calling these validators; the service resolves a profile id
against the `hosting_static_profiles` table.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# A tag is optional; no spaces, no shell metacharacters, no flags like
# `--privileged` smuggled in as the image name.
IMAGE_RE = re.compile(r"^[A-Za-z0-9._/-]+(:[\w.-]+)?$")
PROFILE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")

HEALTH_MODES = ("marker", "http_200")

BUILTIN_ID = "nginx"


class ProfileValidationError(ValueError):
    """An invalid profile shape; schemas translate it into a 422."""


@dataclass(frozen=True)
class ProfileSpec:
    """The resolved runtime of one profile, ready for the provisioner."""

    id: str
    name: str = ""
    image: str = "nginx:alpine"
    web_root: str = "/usr/share/nginx/html"
    container_port: int = 80
    health_mode: str = "marker"
    env: dict[str, str] = field(default_factory=dict)
    command: str | None = None


def spec_from_row(row) -> ProfileSpec:
    """A row of `hosting_static_profiles` becomes the runtime spec."""
    env: dict[str, str] = {}
    if row.env_json:
        try:
            loaded = json.loads(row.env_json)
        except ValueError as exc:
            raise ProfileValidationError(
                "The profile environment variables are invalid"
            ) from exc
        if isinstance(loaded, dict):
            env = {k: v for k, v in loaded.items() if isinstance(k, str) and isinstance(v, str)}
    return ProfileSpec(
        id=row.id,
        name=row.name,
        image=row.image,
        web_root=row.web_root,
        container_port=int(row.container_port),
        health_mode=row.health_mode,
        env=env,
        command=row.command,
    )


def validate_profile_id(value: str) -> str:
    value = (value or "").strip()
    if not PROFILE_ID_RE.fullmatch(value):
        raise ProfileValidationError("The profile id is invalid")
    return value


def validate_profile_image(value: str) -> str:
    value = (value or "").strip()
    if (
        not value
        or value.startswith("-")
        or "--" in value
        or " " in value
        or not IMAGE_RE.fullmatch(value)
    ):
        raise ProfileValidationError("The profile image is invalid")
    return value


def validate_profile_web_root(value: str) -> str:
    value = (value or "").strip()
    if (
        not value.startswith("/")
        or ".." in value.split("/")
        or len(value) > 1
        and value != value.rstrip("/")
    ):
        raise ProfileValidationError("The web root must be an absolute path")
    return value


def validate_profile_port(value: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ProfileValidationError("The profile port is invalid") from exc
    if not 1 <= port <= 65535:
        raise ProfileValidationError("The profile port is out of range")
    return port


def validate_profile_health_mode(value: str) -> str:
    value = (value or "").strip()
    if value not in HEALTH_MODES:
        raise ProfileValidationError("The health check mode is invalid")
    return value


def validate_profile_env(value: dict[str, str] | None) -> dict[str, str] | None:
    """A plain `{name: value}` object of strings; NULL stays NULL."""
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) and key.strip()
        for key, item in value.items()
    ):
        raise ProfileValidationError(
            "The environment variables must be an object of strings"
        )
    return dict(value)


def validate_profile_command(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def validate_profile(
    *,
    image: str,
    web_root: str,
    container_port: int,
    health_mode: str,
    env_json: dict[str, str] | None = None,
    command: str | None = None,
    profile_id: str | None = None,
) -> dict[str, object]:
    """Normalized columns or `ProfileValidationError` (422 via the schema)."""
    data: dict[str, object] = {
        "image": validate_profile_image(image),
        "web_root": validate_profile_web_root(web_root),
        "container_port": validate_profile_port(container_port),
        "health_mode": validate_profile_health_mode(health_mode),
        "env_json": validate_profile_env(env_json),
        "command": validate_profile_command(command),
    }
    if profile_id is not None:
        data["id"] = validate_profile_id(profile_id)
    return data


def marker_for(profile_id: str) -> str:
    """The HTML marker the seed writes and the health check demands."""
    return f'data-spanel-profile="{profile_id}"'
