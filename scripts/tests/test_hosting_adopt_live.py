"""Adoption CLI against the real Docker machine.

A.SPEC 0013. The database is a throwaway SQLite file, because this suite tests
the CLI, not PostgreSQL; the WordPress verdict and the container inspection
run against the real Docker over the transport from the core `.env`.

Without the lab the destructive reset suite is skipped, but adoption is not
destructive: it only writes one ownership row and one audit row.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

RAIZ_DEL_REPO = Path(__file__).resolve().parents[2]
RAIZ_DEL_CORE = RAIZ_DEL_REPO / "vendor" / "systutor-core"
RAIZ_DE_SCRIPTS = RAIZ_DEL_REPO / "scripts"
RUTA_DEL_CLI = RAIZ_DE_SCRIPTS / "hosting_adopt.py"
RUTA_DE_MIGRACION = (
    RAIZ_DEL_CORE / "plugins" / "hosting" / "migrations" / "001_create_hosting_sites.py"
)

sys.path.insert(0, str(RAIZ_DEL_CORE / "src"))
sys.path.insert(0, str(RAIZ_DEL_CORE))
sys.path.insert(0, str(RAIZ_DE_SCRIPTS))

import systutor.kernel.models  # noqa: F401
from plugins.hosting.backend.models import HostingSite
from systutor.core.config import Settings
from systutor.core.database import Base, build_engine
from systutor.kernel.audit.models import AuditLog
from systutor.kernel.tenants.models import Tenant

LIVE = os.environ.get("SPANEL_LIVE_TESTS") == "1"
SITIO_A = "spanel-demowp-wp"
SITIO_B = "spanel-test-wp"

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="test de integracion: requiere SPANEL_LIVE_TESTS=1 y la laptop encendida",
)


def _cargar_migracion():
    import importlib.util

    spec = importlib.util.spec_from_file_location("hosting_adopt_migration", RUTA_DE_MIGRACION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "adopt_test.db"


@pytest.fixture()
def engine(db_path: Path) -> Iterator[Engine]:
    settings = Settings(
        env="test",
        database_url=f"sqlite+pysqlite:///{db_path}",
        plugins_dir=RAIZ_DEL_CORE / "plugins",
        seed_admin_email="admin@example.com",
        seed_admin_password="ChangeMe123!",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        _cargar_migracion().upgrade(db)
        db.commit()
    with Session(engine) as db:
        db.add_all(
            [
                Tenant(name="Demo Tenant", slug="demo"),
                Tenant(name="Tenant B", slug="tenant-b"),
            ]
        )
        db.commit()
    try:
        yield engine
    finally:
        engine.dispose()


def _run_cli(
    db_path: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["SYSTUTOR_DATABASE_URL"] = f"sqlite+pysqlite:///{db_path}"
    env["SYSTUTOR_PLUGINS_DIR"] = str(RAIZ_DEL_CORE / "plugins")
    return subprocess.run(
        [sys.executable, str(RUTA_DEL_CLI), *args],
        cwd=RAIZ_DEL_REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _rows(engine: Engine) -> list[HostingSite]:
    with Session(engine) as db:
        return list(db.scalars(select(HostingSite)))


def _audits(engine: Engine) -> list[AuditLog]:
    with Session(engine) as db:
        return list(db.scalars(select(AuditLog).where(AuditLog.module == "hosting")))


def test_adopcion_dry_run_no_escribe(engine: Engine, db_path: Path) -> None:
    result = _run_cli(
        db_path,
        "--container",
        SITIO_A,
        "--tenant",
        "demo",
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout
    assert _rows(engine) == []
    assert _audits(engine) == []


def test_adopcion_escribe_una_fila_y_auditoria(engine: Engine, db_path: Path) -> None:
    result = _run_cli(
        db_path,
        "--container",
        SITIO_A,
        "--tenant",
        "demo",
        "--domain",
        "demo.example",
    )
    assert result.returncode == 0, result.stderr

    rows = _rows(engine)
    assert len(rows) == 1
    assert rows[0].container_name == SITIO_A
    assert rows[0].domain == "demo.example"

    audits = _audits(engine)
    assert len(audits) == 1
    assert audits[0].action == "hosting.site.adopted"
    assert audits[0].actor_type == "system"
    assert audits[0].entity_id == rows[0].id


def test_adopcion_duplicada_falla(engine: Engine, db_path: Path) -> None:
    first = _run_cli(db_path, "--container", SITIO_A, "--tenant", "demo")
    assert first.returncode == 0, first.stderr

    second = _run_cli(db_path, "--container", SITIO_A, "--tenant", "tenant-b")
    assert second.returncode == 2, second.stdout + second.stderr
    assert "ya pertenece" in second.stdout
    assert len(_rows(engine)) == 1


def test_adopcion_rechaza_un_contenedor_que_no_es_wordpress(
    engine: Engine, db_path: Path
) -> None:
    result = _run_cli(
        db_path,
        "--container",
        "spanel-demowp-db",
        "--tenant",
        "demo",
    )
    assert result.returncode == 1
    assert "no es un sitio WordPress" in result.stdout
    assert _rows(engine) == []
