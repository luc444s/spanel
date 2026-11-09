from __future__ import annotations

import pytest

from plugins.apps.backend.public_url import (
    PublicUrlError,
    install_url,
    manifest_action_url,
    parse_public_url,
)


def test_public_origin_accepts_https_and_loopback_http_only_outside_production() -> None:
    origin = parse_public_url("https://panel.example/", production=True)
    assert origin.origin == "https://panel.example"
    assert origin.manifest_callback() == (
        "https://panel.example/api/v1/plugins/apps/github/manifest/callback"
    )
    assert parse_public_url("http://localhost:8000", production=False).origin == (
        "http://localhost:8000"
    )
    assert parse_public_url("http://127.0.0.1", production=False).origin == (
        "http://127.0.0.1"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "panel.example",
        "http://panel.example",
        "https://panel.example/path",
        "https://panel.example?query=1",
        "https://panel.example#fragment",
        "https://user:pass@panel.example",
        "ftp://panel.example",
    ],
)
def test_public_origin_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(PublicUrlError):
        parse_public_url(value, production=True)


def test_manifest_action_urls_are_exact_github_hosts() -> None:
    assert manifest_action_url("personal", None) == "https://github.com/settings/apps/new"
    assert manifest_action_url("organization", "acme") == (
        "https://github.com/organizations/acme/settings/apps/new"
    )
    for organization in ["../acme", "acme/settings", "ac me", ""]:
        with pytest.raises(PublicUrlError):
            manifest_action_url("organization", organization)
    with pytest.raises(PublicUrlError):
        manifest_action_url("enterprise", None)


def test_install_url_uses_slug_only() -> None:
    assert install_url("spanel-apps-1234") == (
        "https://github.com/apps/spanel-apps-1234/installations/new"
    )
    with pytest.raises(PublicUrlError):
        install_url("../../evil")
