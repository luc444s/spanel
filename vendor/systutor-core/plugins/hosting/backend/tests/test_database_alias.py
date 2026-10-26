"""A.SPEC 0034 — resolve a database host alias inside the site's networks.

No test here starts Docker or reaches the network: `pedir` is a spy, inspect
payloads are dictionaries, and the service's `_inspect` is monkeypatched.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from plugins.hosting.backend import database as database_module
from plugins.hosting.backend import service as service_module
from plugins.hosting.backend.database import (
    alias_contenedor,
    resolver_alias_de_contenedor,
)
from plugins.hosting.backend.service import HostingService
from plugins.hosting.backend.settings import HostingSettings
from systutor.core.errors import AppError
from systutor.kernel.tenants.context import TenantContext

REAL_DB = "legalattorney-clean-db"
RAW_HOST = "db"
SECRET = "no-debe-salir"
SITE_NETWORK = "legalattorney-clean_default"
FOREIGN_NETWORK = "otra_red"
RUNNING = {"State": {"Status": "running"}}
DB_RUNNING = {
    "State": {"Status": "running"},
    "NetworkSettings": {"Networks": {"proxy": {"Aliases": []}}},
}


def site_info(host: str = RAW_HOST, networks: tuple[str, ...] = (SITE_NETWORK,)) -> dict:
    return {
        "State": {"Status": "running"},
        "Config": {
            "Env": [
                f"WORDPRESS_DB_HOST={host}",
                "WORDPRESS_DB_NAME=legalattorney_clean",
                "WORDPRESS_DB_USER=wp_clean",
                f"WORDPRESS_DB_PASSWORD={SECRET}",
            ]
        },
        "NetworkSettings": {
            "Networks": {name: {"Aliases": ["legalattorney-clean"]} for name in networks}
        },
    }


def candidato(name: str, networks: dict[str, list[str]]) -> dict:
    return {
        "name": name,
        "networks": {red: {"Aliases": aliases} for red, aliases in networks.items()},
    }


def context() -> TenantContext:
    return TenantContext("t1", None, "u1", (), None)


def site(kind: str = "wordpress") -> SimpleNamespace:
    return SimpleNamespace(
        id="s1", tenant_id="t1", container_name="legalattorney-clean", kind=kind
    )


class FakeClient:
    def __init__(self) -> None:
        self.logins: list[tuple[str, str, str | None]] = []

    def login(self, user: str, password: str, server: str | None = None) -> dict[str, str]:
        self.logins.append((user, password, server))
        return {"pma": "opaque"}


class RunSpy:
    returncode = 0

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, env, timeout=30, input=None):  # noqa: ANN001
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, "", "")

    @property
    def joined(self) -> str:
        return " ".join(" ".join(call) for call in self.calls)


def build_service(monkeypatch, infos: dict[str, dict]):
    service = HostingService(
        db=None,  # type: ignore[arg-type]
        settings=HostingSettings(
            docker_transport="local",
            phpmyadmin_arbitrary=True,
            phpmyadmin_db_network="spanel-db",
        ),
    )
    inspected: list[str] = []

    def fake_inspect(name: str) -> dict | None:
        inspected.append(name)
        return infos.get(name)

    monkeypatch.setattr(service, "_inspect", fake_inspect)
    monkeypatch.setattr(service, "_require_site", lambda _site: (site_info(), True))
    audits: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        service,
        "_audit_site",
        lambda _site, _ctx, action, details=None: audits.append((action, details or {})),
    )
    spy = RunSpy()
    monkeypatch.setattr(service_module, "run", spy)
    return service, inspected, spy, audits


# --- pure resolver -------------------------------------------------------


def test_a_single_alias_returns_the_container_name() -> None:
    candidatos = [
        candidato(REAL_DB, {SITE_NETWORK: ["db", "legalattorney-clean-db"]}),
        candidato("otro", {SITE_NETWORK: ["otro"]}),
    ]
    assert alias_contenedor("db", candidatos) == REAL_DB


def test_an_unknown_alias_returns_none() -> None:
    candidatos = [candidato(REAL_DB, {SITE_NETWORK: ["otros"]})]
    assert alias_contenedor("db", candidatos) is None


def test_two_containers_with_the_same_alias_are_ambiguous() -> None:
    candidatos = [
        candidato(REAL_DB, {SITE_NETWORK: ["db"]}),
        candidato("legalattorney-clean-db-2", {SITE_NETWORK: ["db"]}),
    ]
    assert alias_contenedor("db", candidatos) is None


def test_an_alias_on_a_foreign_network_is_ignored() -> None:
    candidatos = [candidato(REAL_DB, {FOREIGN_NETWORK: ["db"]})]
    assert (
        alias_contenedor(
            "db", candidatos, redes_del_sitio={SITE_NETWORK}
        )
        is None
    )


def test_the_io_resolver_only_scans_the_site_networks(monkeypatch) -> None:
    commands: list[str] = []

    def fake_pedir(_env: dict[str, str], comando: str) -> str:
        commands.append(comando)
        if "network inspect" in comando:
            assert FOREIGN_NETWORK not in comando
            return json.dumps({"id1": {"Name": REAL_DB}})
        return json.dumps(
            {
                SITE_NETWORK: {"Aliases": ["db", "legalattorney-clean-db"]},
                FOREIGN_NETWORK: {"Aliases": ["db"]},
            }
        )

    monkeypatch.setattr(database_module, "pedir", fake_pedir)

    resuelto = resolver_alias_de_contenedor({}, site_info(), "db")

    assert resuelto == REAL_DB
    assert any("network inspect" in command for command in commands)
    assert all(FOREIGN_NETWORK not in command for command in commands)


# --- service -------------------------------------------------------------


def test_an_existing_container_skips_the_alias_scan(monkeypatch) -> None:
    service, inspected, _spy, _audits = build_service(
        monkeypatch, {"spanel-demo-db": RUNNING}
    )
    scanned: list[str] = []
    monkeypatch.setattr(
        service_module,
        "resolver_alias_de_contenedor",
        lambda _env, _info, host: scanned.append(host),
    )

    raw, host, database, user, _password = service._database_binding_for(
        site(), site_info("spanel-demo-db")
    )

    assert (raw, host, database, user) == (
        "spanel-demo-db",
        "spanel-demo-db",
        "legalattorney_clean",
        "wp_clean",
    )
    assert inspected == ["spanel-demo-db"]
    assert scanned == []


def test_the_alias_is_resolved_but_the_raw_host_stays(monkeypatch) -> None:
    service, inspected, _spy, _audits = build_service(monkeypatch, {})
    monkeypatch.setattr(
        service_module,
        "resolver_alias_de_contenedor",
        lambda _env, _info, host: REAL_DB if host == RAW_HOST else None,
    )

    raw, host, _database, _user, _password = service._database_binding_for(
        site(), site_info(RAW_HOST)
    )

    assert raw == RAW_HOST
    assert host == REAL_DB
    assert inspected == [RAW_HOST]


def test_credentials_keep_db_but_exec_against_the_real_container(monkeypatch) -> None:
    service, _inspected, _spy, _audits = build_service(monkeypatch, {})
    monkeypatch.setattr(
        service_module,
        "resolver_alias_de_contenedor",
        lambda _env, _info, host: REAL_DB if host == RAW_HOST else None,
    )
    captured: dict[str, str] = {}

    def fake_prefix(_env, db, user, password, database):  # noqa: ANN001
        captured.update(db=db, user=user, password=password, database=database)
        return "wp_"

    monkeypatch.setattr(service_module, "prefijo_de_tablas", fake_prefix)

    credentials, db_container, prefix = service._credentials(site(), site_info())

    assert credentials["host"] == RAW_HOST
    assert "WORDPRESS_DB_HOST=db\n" in credentials["_contenido"]
    assert db_container == REAL_DB
    assert captured["db"] == REAL_DB
    assert prefix == "wp_"


def test_phpmyadmin_login_receives_the_resolved_container(monkeypatch) -> None:
    service, inspected, spy, audits = build_service(
        monkeypatch, {REAL_DB: DB_RUNNING}
    )
    monkeypatch.setattr(
        service_module,
        "resolver_alias_de_contenedor",
        lambda _env, _info, host: REAL_DB if host == RAW_HOST else None,
    )
    client = FakeClient()

    service.phpmyadmin_login_credentials(site(), context(), client)

    assert client.logins == [("wp_clean", SECRET, REAL_DB)]
    assert inspected == [RAW_HOST, REAL_DB]
    assert spy.joined.endswith(f"network connect spanel-db {REAL_DB}")
    details = audits[-1][1]
    assert details["host"] == REAL_DB
    assert SECRET not in str(details)
    assert SECRET not in spy.joined


def test_an_unresolvable_alias_keeps_the_controlled_error(monkeypatch) -> None:
    service, _inspected, spy, _audits = build_service(monkeypatch, {})
    monkeypatch.setattr(
        service_module, "resolver_alias_de_contenedor", lambda _env, _info, _host: None
    )
    client = FakeClient()

    with pytest.raises(AppError) as error:
        service.phpmyadmin_login_credentials(site(), context(), client)

    assert error.value.status_code == 409
    assert error.value.code == "hosting_phpmyadmin_unavailable"
    assert client.logins == []
    assert spy.calls == []
