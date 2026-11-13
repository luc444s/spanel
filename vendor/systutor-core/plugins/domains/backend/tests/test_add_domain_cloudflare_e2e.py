"""E2E flow (controlled): Add domain when Cloudflare is active -> plan/apply provisioning.

Exercises the 0075 provisioning endpoints as used by the Domains "Add domain"
UI flow: preview plan, apply explicit DNS-only provisioning, and verify
idempotency/readback. Uses fake Cloudflare transport (no public DNS mutation).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from systutor.kernel.audit.models import AuditLog

# Reuse the router test harness and fakes.
try:
    from .test_cloudflare_router import build_app, context
except ImportError:  # pragma: no cover - direct invocation fallback
    from test_cloudflare_router import build_app, context


def test_add_domain_cloudflare_active_plan_apply_flow(tmp_path: Path, monkeypatch) -> None:
    app, fake, db, holder, _cipher, engine = build_app(tmp_path, monkeypatch)
    path = "/api/v1/plugins/domains/dns/cloudflare/provision"
    body = {"domain": "newsite.example.com", "ipv4": "203.0.113.10", "www": True, "replace": False}
    try:
        with TestClient(app) as client:
            holder["context"] = context("t1", "domains.dns.cloudflare")

            # 1) Preview plan (read-only) before mutating public DNS.
            planned = client.post(f"{path}/plan", json=body)
            assert planned.status_code == 200
            plan = planned.json()
            assert plan["domain"] == "newsite.example.com"
            assert plan["ipv4"] == "203.0.113.10"
            assert plan["www"] is True
            assert plan["drift"] is True
            assert all(r["proxied"] is False for r in plan["desired_records"])
            assert {r["type"] for r in plan["desired_records"]} == {"A", "CNAME"}

            # 2) Explicit apply.
            applied = client.post(f"{path}/apply", json=body)
            assert applied.status_code == 200
            result = applied.json()
            assert result["applied"] is True
            assert all(r["proxied"] is False for r in result.get("records", []))

            # 3) Re-plan converges (idempotent / safe to retry UI flow).
            converged = client.post(f"{path}/plan", json=body)
            assert converged.status_code == 200
            assert converged.json()["drift"] is False

            # 4) Audit recorded without leaking requested content.
            provisioned = (
                db.query(AuditLog)
                .filter(AuditLog.action == "domains.dns.cloudflare_provisioned")
                .all()
            )
            assert provisioned
            assert "203.0.113.10" not in str(provisioned[-1].details)

        assert all(
            mutation[3] is None or mutation[3].get("proxied") is False
            for mutation in fake.mutations
        )
    finally:
        db.close()
        engine.dispose()


def test_add_domain_cloudflare_inactive_or_unauthorized_is_rejected(tmp_path: Path, monkeypatch) -> None:
    app, _fake, db, holder, _cipher, engine = build_app(tmp_path, monkeypatch)
    path = "/api/v1/plugins/domains/dns/cloudflare/provision/plan"
    body = {"domain": "newsite.example.com", "ipv4": "203.0.113.10", "www": False, "replace": False}
    try:
        with TestClient(app) as client:
            # No Cloudflare permission -> cannot configure/provision from Add domain.
            holder["context"] = context("t1", "domains.dns.read")
            assert client.post(path, json=body).status_code == 403
    finally:
        db.close()
        engine.dispose()
