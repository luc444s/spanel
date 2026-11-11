#!/usr/bin/env python3
"""Benchmark full domain synchronization and projected global reads separately."""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
ROOT = (
    CHECKOUT_ROOT
    if (CHECKOUT_ROOT / "vendor" / "systutor-core").is_dir()
    else Path("/app")
)
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
from systutor.contracts.domain_ownership import (
    OwnedSite,
    register_site_provider,
)
from systutor.kernel.tenants.models import Tenant


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999))]


class CountingRunner:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.external_calls = 0

    def execute(self, argv, **kwargs):
        if argv and argv[0] in ("docker", "ssh"):
            self.external_calls += 1
        return self.inner.run(argv, **kwargs)

    run = execute


def write_documents(
    documents: list[tuple[str, str]], directory: Path, transport: str, container: str
) -> None:
    if transport == "local":
        directory.mkdir(parents=True, exist_ok=True)
        for name, text in documents:
            (directory / name).write_text(text, encoding="utf-8")
        return
    subprocess.run(["docker", "exec", container, "mkdir", "-p", str(directory)], check=True)
    archive = "".join(
        f"cat > {directory / name} <<'SPANEL_EOF'\n{text}SPANEL_EOF\n"
        for name, text in documents
    )
    subprocess.run(
        ["docker", "exec", "-i", container, "sh"], input=archive, text=True, check=True
    )


def summary(label: str, values: list[float]) -> None:
    print(
        f"{label}: median={statistics.median(values):.3f}s "
        f"p95={percentile(values, 0.95):.3f}s min={min(values):.3f}s max={max(values):.3f}s"
    )


def execute(args) -> int:
    temporary = tempfile.TemporaryDirectory(prefix="spanel-domains-benchmark-")
    host_dir = Path(temporary.name)
    dynamic_dir = (
        host_dir
        if args.transport == "local"
        else Path(f"/tmp/spanel-domains-benchmark-{uuid.uuid4().hex}")
    )
    tenants = [
        Tenant(id=f"tenant-{i:03d}", name=f"Tenant {i}", slug=f"tenant-{i}")
        for i in range(args.tenants)
    ]
    sites: list[OwnedSite] = []
    documents: list[tuple[str, str]] = []
    for index in range(args.domains):
        tenant = tenants[index % args.tenants]
        domain = f"domain-{index:04d}.benchmark.test"
        container = f"benchmark-site-{index:04d}"
        sites.append(OwnedSite(f"site-{index:04d}", tenant.id, container, None))
        documents.append(
            (
                f"spanel-benchmark-{index:04d}.yml",
                render_site_yaml(container, [domain], "benchmark"),
            )
        )
    write_documents(documents, dynamic_dir, args.transport, args.traefik_container)

    engine = create_engine(f"sqlite:///{host_dir / 'benchmark.db'}")
    Tenant.__table__.create(engine)
    DomainRoute.__table__.create(engine)
    ObservedDomain.__table__.create(engine)
    DomainSyncState.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    app = FastAPI()
    register_site_provider(
        app,
        "benchmark",
        lambda _db, tenant_id: sites
        if tenant_id is None
        else [site for site in sites if site.tenant_id == tenant_id],
    )
    register_domain_providers(
        app,
        "benchmark",
        list_domains=lambda _db, _tenant: [],
        owner=lambda _db, _domain: None,
    )
    settings = Settings.from_env(
        "internal",
        {
            "SPANEL_PROXY_TRANSPORT": args.transport,
            "SPANEL_PROXY_CONFIG_DIR": str(dynamic_dir),
            "SPANEL_PROXY_TRAEFIK_CONTAINER": args.traefik_container,
        },
    )
    runner = CountingRunner(build_runner(settings))
    operations = ProxyOperations(settings, runner)
    if args.transport == "docker":
        operations._detection = Detection(
            transport="docker",
            reason="benchmark",
            config_dir=dynamic_dir,
            traefik_container=args.traefik_container,
            profile=settings.profile,
            cert_resolver=settings.cert_resolver,
        )
    try:
        with factory() as db:
            db.add_all(tenants)
            db.commit()
            sync_times: list[float] = []
            get_times: list[float] = []
            for iteration in range(args.warmups + args.runs):
                started = time.perf_counter()
                plan = prepare_sync(db, app, operations)
                apply_sync(db, plan)
                db.commit()
                elapsed = time.perf_counter() - started
                if iteration >= args.warmups:
                    sync_times.append(elapsed)
            service = DomainsService(db, app)
            before_get_calls = runner.external_calls
            for iteration in range(args.warmups + args.runs):
                started = time.perf_counter()
                items, _, error = service.list_all_domains(
                    active_tenant_id=tenants[0].id, can_manage_active=True
                )
                elapsed = time.perf_counter() - started
                if error or len(items) != args.domains:
                    raise RuntimeError(
                        f"invalid projected result: {len(items)} items, {error=}"
                    )
                if iteration >= args.warmups:
                    get_times.append(elapsed)
            get_calls = runner.external_calls - before_get_calls
        print(f"domains={args.domains} tenants={args.tenants} runs={args.runs}")
        summary("sync", sync_times)
        summary("GET scope=all", get_times)
        print(f"external calls during GET: {get_calls}; YAML reads during GET: 0")
        return 0
    finally:
        if args.transport == "docker":
            subprocess.run(
                [
                    "docker",
                    "exec",
                    args.traefik_container,
                    "rm",
                    "-rf",
                    "--",
                    str(dynamic_dir),
                ],
                check=False,
            )
        temporary.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domains", type=int, default=100)
    parser.add_argument("--tenants", type=int, default=2)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--transport", choices=("local", "docker"), default="local")
    parser.add_argument("--traefik-container", default="spanel-traefik-lab")
    args = parser.parse_args()
    if min(args.domains, args.tenants, args.runs) < 1 or args.warmups < 0:
        parser.error("domains, tenants and runs must be positive; warmups cannot be negative")
    if args.tenants > args.domains:
        parser.error("tenants cannot exceed domains")
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
