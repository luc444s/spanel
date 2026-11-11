from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE / "src"))
sys.path.insert(0, str(CORE))

from plugins.domains.backend.models import (
    DomainRoute,
    DomainSyncState,
    ObservedDomain,
)
from plugins.domains.backend.proxy import (
    ProxyOperations,
    Settings,
    build_runner,
    render_site_yaml,
)
from plugins.domains.backend.proxy.detect import Detection
from plugins.domains.backend.service import DomainsService
from plugins.domains.backend.sync import apply_sync, prepare_sync
from systutor.contracts.domain_namespace import register_domain_providers
from systutor.contracts.domain_ownership import OwnedSite, register_site_provider
from systutor.kernel.tenants.models import Tenant

LIVE = os.environ.get("SPANEL_LIVE_TESTS") == "1"
TRAEFIK = "spanel-traefik-lab"


def database_and_registry() -> tuple[Session, FastAPI]:
    engine = create_engine("sqlite://")
    for table in (
        Tenant.__table__,
        DomainRoute.__table__,
        ObservedDomain.__table__,
        DomainSyncState.__table__,
    ):
        table.create(engine)
    db = Session(engine)
    db.add(Tenant(id="t1", name="One", slug="one"))
    db.commit()
    app = FastAPI()
    register_site_provider(
        app, "test", lambda _db, _tenant: [OwnedSite("s1", "t1", "site-one", None)]
    )
    register_domain_providers(
        app, "test", list_domains=lambda _db, _tenant: [], owner=lambda _db, _domain: None
    )
    return db, app


def assert_projected_read_survives_proxy_block(
    db: Session, app: FastAPI, operations: ProxyOperations
) -> None:
    plan = prepare_sync(db, app, operations)
    apply_sync(db, plan)
    db.commit()

    def blocked(*_args, **_kwargs):
        raise PermissionError("proxy blocked")

    operations.runner.run = blocked  # type: ignore[method-assign]
    service = DomainsService(db, app)
    service._operations = lambda: operations  # type: ignore[method-assign]
    items, _, error = service.list_all_domains(
        active_tenant_id="t1", can_manage_active=True
    )
    assert [item.domain for item in items] == ["one.test"]
    assert error is None


def test_yaml_sync_database_global_read_survives_proxy_block(tmp_path: Path) -> None:
    dynamic = tmp_path / "dynamic"
    dynamic.mkdir()
    (dynamic / "spanel-one.yml").write_text(
        render_site_yaml("site-one", ["one.test"], "test"), encoding="utf-8"
    )
    db, app = database_and_registry()
    settings = Settings.from_env(
        "internal",
        {"SPANEL_PROXY_TRANSPORT": "local", "SPANEL_PROXY_CONFIG_DIR": str(dynamic)},
    )
    operations = ProxyOperations(settings, build_runner(settings))
    assert_projected_read_survives_proxy_block(db, app, operations)


@pytest.mark.skipif(not LIVE, reason="requires SPANEL_LIVE_TESTS=1")
def test_live_docker_snapshot_isolated_and_cleaned_up() -> None:
    running = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", TRAEFIK],
        capture_output=True,
        text=True,
        check=False,
    )
    if running.returncode != 0 or running.stdout.strip() != "true":
        pytest.skip(f"{TRAEFIK} is unavailable: {running.stderr.strip()}")

    dynamic = Path(f"/tmp/spanel-domains-e2e-{uuid.uuid4().hex}")
    yaml = render_site_yaml("site-one", ["one.test"], "test")
    setup = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            TRAEFIK,
            "sh",
            "-c",
            'mkdir -p "$1" && cat > "$1/spanel-one.yml"',
            "sh",
            str(dynamic),
        ],
        input=yaml,
        text=True,
        capture_output=True,
        check=False,
    )
    assert setup.returncode == 0, setup.stderr
    try:
        db, app = database_and_registry()
        settings = Settings.from_env(
            "internal",
            {
                "SPANEL_PROXY_TRANSPORT": "docker",
                "SPANEL_PROXY_CONFIG_DIR": str(dynamic),
                "SPANEL_PROXY_TRAEFIK_CONTAINER": TRAEFIK,
            },
        )
        operations = ProxyOperations(settings, build_runner(settings))
        operations._detection = Detection(
            transport="docker",
            reason="isolated live E2E",
            config_dir=dynamic,
            traefik_container=TRAEFIK,
            profile=settings.profile,
            cert_resolver=settings.cert_resolver,
        )
        assert_projected_read_survives_proxy_block(db, app, operations)
    finally:
        subprocess.run(
            ["docker", "exec", TRAEFIK, "rm", "-rf", "--", str(dynamic)],
            capture_output=True,
            text=True,
            check=False,
        )
