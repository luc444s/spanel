"""A.SPEC 0033 — pure network decisions and the idempotent attach path.

No test here starts Docker or reaches the network: the transport is a spy and
the inspect payloads are plain dictionaries.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from plugins.hosting.backend import provisioner as provisioner_module
from plugins.hosting.backend import service as service_module
from plugins.hosting.backend.network import (
    connect_args,
    multi_database_enabled,
    needs_connection,
    networks_of,
    validate_network_name,
)
from plugins.hosting.backend.provisioner import Provisioner, SiteNames
from plugins.hosting.backend.service import HostingService
from plugins.hosting.backend.settings import HostingSettings
from plugins.hosting.backend.transport import Fallo
from systutor.core.errors import AppError
from systutor.kernel.tenants.context import TenantContext

DB_HOST = "spanel-demo-db"
SECRET = "no-debe-salir"
RUNNING = {"State": {"Status": "running"}}


def info_with(*networks: str) -> dict:
    return {
        "State": {"Status": "running"},
        "NetworkSettings": {"Networks": {name: {"IPAddress": "172.18.0.2"} for name in networks}},
    }


def context() -> TenantContext:
    return TenantContext("t1", None, "u1", (), None)


def site(kind: str = "wordpress") -> SimpleNamespace:
    return SimpleNamespace(
        id="s1", tenant_id="t1", container_name="spanel-demo-wp", kind=kind
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


def build(monkeypatch, *, arbitrary: bool, network: str, db_info: dict):
    service = HostingService(
        db=None,  # type: ignore[arg-type]
        settings=HostingSettings(
            docker_transport="local",
            phpmyadmin_arbitrary=arbitrary,
            phpmyadmin_db_network=network,
        ),
    )
    monkeypatch.setattr(service, "_require_site", lambda _site: (RUNNING, True))
    monkeypatch.setattr(
        service,
        "_database_binding_for",
        lambda _site, _info: (f"{DB_HOST}:3306", DB_HOST, "wp_demo", "wp_user", SECRET),
    )
    monkeypatch.setattr(service, "_inspect", lambda _name: db_info)
    audits: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        service,
        "_audit_site",
        lambda _site, _ctx, action, details=None: audits.append((action, details or {})),
    )
    spy = RunSpy()
    monkeypatch.setattr(service_module, "run", spy)
    return service, spy, audits


# --- pure module ---------------------------------------------------------


def test_networks_of_reads_the_inspect_payload() -> None:
    assert set(networks_of(info_with("proxy", "spanel-db"))) == {"proxy", "spanel-db"}
    assert networks_of(None) == {}
    assert networks_of({}) == {}
    assert networks_of({"NetworkSettings": {"Networks": None}}) == {}


def test_needs_connection_only_when_the_interface_is_missing() -> None:
    assert needs_connection(info_with("proxy"), "spanel-db") is True
    assert needs_connection(info_with("proxy", "spanel-db"), "spanel-db") is False


@pytest.mark.parametrize("network", ["", "  ", "spanel db", "tcp://spanel-db", "-spanel-db"])
def test_network_names_with_spaces_or_a_scheme_are_rejected(network: str) -> None:
    with pytest.raises(ValueError):
        validate_network_name(network)


@pytest.mark.parametrize("network", ["spanel-db", "mariadb_internal", "proxy"])
def test_docker_network_names_are_accepted(network: str) -> None:
    assert validate_network_name(network) == network


def test_both_settings_are_required_for_multi_database() -> None:
    assert multi_database_enabled(True, "spanel-db") is True
    assert multi_database_enabled(False, "spanel-db") is False
    assert multi_database_enabled(True, "") is False


def test_connect_args_cannot_carry_an_option_or_a_credential() -> None:
    assert connect_args("spanel-db", DB_HOST) == (
        "network",
        "connect",
        "spanel-db",
        DB_HOST,
    )
    with pytest.raises(ValueError):
        connect_args("spanel-db", "--help")


# --- service -------------------------------------------------------------


def test_without_arbitrary_the_login_sends_no_server(monkeypatch) -> None:
    service, spy, audits = build(
        monkeypatch, arbitrary=False, network="spanel-db", db_info=info_with("proxy")
    )
    client = FakeClient()
    service.phpmyadmin_login_credentials(site(), context(), client)
    assert client.logins == [("wp_user", SECRET, None)]
    assert spy.calls == []
    assert audits[-1][1].get("network") is None


def test_with_an_empty_network_the_login_sends_no_server(monkeypatch) -> None:
    service, spy, _ = build(
        monkeypatch, arbitrary=True, network="", db_info=info_with("proxy")
    )
    client = FakeClient()
    service.phpmyadmin_login_credentials(site(), context(), client)
    assert client.logins == [("wp_user", SECRET, None)]
    assert spy.calls == []


def test_missing_network_is_attached_once_for_that_database(monkeypatch) -> None:
    service, spy, audits = build(
        monkeypatch, arbitrary=True, network="spanel-db", db_info=info_with("proxy")
    )
    client = FakeClient()
    service.phpmyadmin_login_credentials(site(), context(), client)
    assert client.logins == [("wp_user", SECRET, DB_HOST)]
    assert len(spy.calls) == 1
    assert spy.joined.endswith(f"network connect spanel-db {DB_HOST}")
    details = audits[-1][1]
    assert details["database"] == "wp_demo"
    assert details["host"] == DB_HOST
    assert details["network"] == "spanel-db"
    assert SECRET not in str(details)


def test_an_already_attached_database_runs_no_docker(monkeypatch) -> None:
    service, spy, audits = build(
        monkeypatch,
        arbitrary=True,
        network="spanel-db",
        db_info=info_with("proxy", "spanel-db"),
    )
    client = FakeClient()
    service.phpmyadmin_login_credentials(site(), context(), client)
    assert client.logins == [("wp_user", SECRET, DB_HOST)]
    assert spy.calls == []
    assert "network" not in audits[-1][1]


def test_an_invalid_network_fails_before_any_docker_command(monkeypatch) -> None:
    service, spy, _ = build(
        monkeypatch, arbitrary=True, network="tcp://spanel-db", db_info=info_with("proxy")
    )
    with pytest.raises(AppError) as error:
        service.phpmyadmin_login_credentials(site(), context(), FakeClient())
    assert error.value.status_code == 409
    assert spy.calls == []


def test_a_failed_connect_is_reported_without_the_password(monkeypatch) -> None:
    service, spy, _ = build(
        monkeypatch, arbitrary=True, network="spanel-db", db_info=info_with("proxy")
    )
    spy.returncode = 1
    client = FakeClient()
    with pytest.raises(AppError) as error:
        service.phpmyadmin_login_credentials(site(), context(), client)
    assert error.value.status_code == 409
    assert SECRET not in error.value.message
    assert client.logins == []


def test_a_static_site_never_inspects_or_connects_a_database(monkeypatch) -> None:
    service, spy, _ = build(
        monkeypatch, arbitrary=True, network="spanel-db", db_info=info_with("proxy")
    )
    client = FakeClient()
    with pytest.raises(AppError) as error:
        service.phpmyadmin_login_credentials(site("static"), context(), client)
    assert error.value.code == "hosting_site_wrong_kind"
    assert spy.calls == []
    assert client.logins == []


# --- provisioner ---------------------------------------------------------


def provisioner(arbitrary: bool, network: str) -> Provisioner:
    return Provisioner(
        HostingSettings(
            docker_transport="local",
            phpmyadmin_arbitrary=arbitrary,
            phpmyadmin_db_network=network,
        )
    )


def spy_create(monkeypatch, provisioner_instance, commands, *, fail_on_connect=False):
    def command(*args, input=None):  # noqa: ANN001
        if fail_on_connect and args[:2] == ("network", "connect"):
            raise Fallo("connect failed")
        commands.append(args)
        return ""

    monkeypatch.setattr(provisioner_instance, "command", command)
    monkeypatch.setattr(provisioner_instance, "preflight", lambda names: None)
    monkeypatch.setattr(
        provisioner_module, "escribir_archivo_remoto_en", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        provisioner_module,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )


@pytest.mark.parametrize(
    "arbitrary,network",
    [(True, ""), (False, "spanel-db"), (False, "")],
)
def test_provisioning_only_attaches_when_both_settings_are_on(
    monkeypatch, arbitrary: bool, network: str
) -> None:
    commands: list[tuple] = []
    instance = provisioner(arbitrary, network)
    spy_create(monkeypatch, instance, commands)
    instance.create(SiteNames("demo"), "t1", "demo.spanel", "admin@demo.spanel")
    assert not any(args[:2] == ("network", "connect") for args in commands)


def test_the_new_database_is_attached_after_it_is_created(monkeypatch) -> None:
    commands: list[tuple] = []
    instance = provisioner(True, "spanel-db")
    spy_create(monkeypatch, instance, commands)
    instance.create(SiteNames("demo"), "t1", "demo.spanel", "admin@demo.spanel")

    connect = ("network", "connect", "spanel-db", "spanel-demo-db")
    assert connect in commands
    db_run = next(
        index
        for index, args in enumerate(commands)
        if args[:4] == ("run", "-d", "--name", "spanel-demo-db")
    )
    wp_run = next(
        index
        for index, args in enumerate(commands)
        if args[:4] == ("run", "-d", "--name", "spanel-demo-wp")
    )
    connect_index = commands.index(connect)
    assert db_run < connect_index < wp_run


def test_a_failed_attach_follows_the_existing_cleanup_path(monkeypatch) -> None:
    commands: list[tuple] = []
    instance = provisioner(True, "spanel-db")
    spy_create(monkeypatch, instance, commands, fail_on_connect=True)
    cleaned: list[str] = []
    original_cleanup = instance.cleanup

    def cleanup(names, tenant_id, resources=None):  # noqa: ANN001
        cleaned.append(names.db)
        return original_cleanup(names, tenant_id, resources)

    monkeypatch.setattr(instance, "cleanup", cleanup)
    monkeypatch.setattr(instance, "_owned", lambda *args: False)

    with pytest.raises(Fallo):
        instance.create(SiteNames("demo"), "t1", "demo.spanel", "admin@demo.spanel")
    assert cleaned == ["spanel-demo-db"]
