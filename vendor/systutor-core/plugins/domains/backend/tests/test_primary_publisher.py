"""The domains plugin adapts ProxyOperations to the site domain contract."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from plugins.domains.backend.proxy import ProxyError
from plugins.domains.backend.publisher import ProxySitePublisher
from systutor.contracts.site_domain_publisher import (
    get_site_domain_publisher,
    register_site_domain_publisher,
)


class FakeOperations:
    def __init__(self, fail_remove: bool = False) -> None:
        self.added: list[tuple[str, str, dict]] = []
        self.removed: list[str] = []
        self.fail_remove = fail_remove

    def add(self, container: str, domain: str, **kwargs) -> int:
        self.added.append((container, domain, kwargs))
        return 0

    def remove(self, domain: str) -> int:
        if self.fail_remove:
            raise ProxyError(f"el dominio {domain} no está gestionado por spanel")
        self.removed.append(domain)
        return 0


def test_publish_writes_route_without_wait_or_wordpress_rewrite() -> None:
    operations = FakeOperations()
    publisher = ProxySitePublisher(operations)  # type: ignore[arg-type]
    publisher.publish("spanel-pagina-web", "pagina.spanel")
    assert operations.added == [
        (
            "spanel-pagina-web",
            "pagina.spanel",
            {"wait": 0, "set_wordpress_url": False},
        )
    ]


def test_unpublish_removes_route() -> None:
    operations = FakeOperations()
    ProxySitePublisher(operations).unpublish("pagina.spanel")  # type: ignore[arg-type]
    assert operations.removed == ["pagina.spanel"]


def test_unpublish_is_idempotent_when_route_is_gone() -> None:
    ProxySitePublisher(FakeOperations(fail_remove=True)).unpublish("pagina.spanel")  # type: ignore[arg-type]


def test_other_proxy_errors_still_surface() -> None:
    class Broken(FakeOperations):
        def remove(self, domain: str) -> int:
            raise ProxyError("transporte caído")

    with pytest.raises(ProxyError, match="transporte"):
        ProxySitePublisher(Broken()).unpublish("pagina.spanel")  # type: ignore[arg-type]


def test_registration_is_idempotent_and_duplicate_type_conflicts() -> None:
    app = FastAPI()
    first = ProxySitePublisher(FakeOperations())  # type: ignore[arg-type]
    register_site_domain_publisher(app, "domains", first)
    register_site_domain_publisher(app, "domains", first)
    assert get_site_domain_publisher(app) is first

    class OtherPublisher(FakeOperations):
        def publish(self, container: str, domain: str) -> None:
            pass

        def unpublish(self, domain: str) -> None:
            pass

    with pytest.raises(ValueError, match="Duplicate"):
        register_site_domain_publisher(app, "domains", OtherPublisher())  # type: ignore[arg-type]
