"""OAuth helpers: PKCE, opaque state, scope resolution and minimal HTML pages.

Only hashes of state are persisted or compared.  PKCE verifiers are persisted
encrypted by the service; this module never logs or returns them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Iterable, Mapping

from plugins.domains.backend.cloudflare.client import CloudflareValidation
from plugins.domains.backend.cloudflare.config import REQUIRED_SCOPE_NAMES

FLOW_PENDING = "pending"
FLOW_CONSUMED = "consumed"
FLOW_DENIED = "denied"
FLOW_FAILED = "failed"
FLOW_EXPIRED = "expired"
FLOW_CANCELLED = "cancelled"

_SUCCESS_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Cloudflare connected</title></head>
<body><h1>Cloudflare connected</h1>
<p>Authorization completed. You can close this window and return to the terminal.</p>
</body></html>"""

_FAILURE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Cloudflare authorization failed</title></head>
<body><h1>Cloudflare authorization failed</h1>
<p>This authorization attempt is invalid, expired or already used.</p>
</body></html>"""


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def compare_hash(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


def resolve_scope_ids(
    scopes: Iterable[Mapping[str, object]],
    required: Iterable[str] = REQUIRED_SCOPE_NAMES,
) -> list[str]:
    available = {
        str(item.get("name", "")).strip(): str(item.get("id", "")).strip()
        for item in scopes
        if str(item.get("id", "")).strip()
    }
    resolved: list[str] = []
    missing: list[str] = []
    for name in required:
        scope_id = available.get(name, "")
        if not scope_id:
            missing.append(name)
        else:
            resolved.append(scope_id)
    if missing:
        raise CloudflareValidation(
            "Cloudflare does not expose the required OAuth scopes: "
            + ", ".join(missing)
        )
    return resolved


def granted_scope_names(raw_scope: str) -> list[str]:
    return [value for value in raw_scope.split() if value]


def success_page() -> str:
    return _SUCCESS_HTML


def failure_page() -> str:
    return _FAILURE_HTML
