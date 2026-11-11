from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(CORE / "src"))
sys.path.insert(0, str(CORE))

import domains_sync
from plugins.domains.backend.sync import Observation, SyncPlan


class FakeDb:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def plan(*, valid: bool = True) -> SyncPlan:
    result = SyncPlan(
        generation="generation",
        documents=1,
        observations=[Observation("one.test", "t1", "s1", "site", "r", "one.yml", "h")],
        additions=["one.test"],
    )
    if not valid:
        result.invalid.append("bad.yml")
    return result


def test_run_sync_is_plan_by_default(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(domains_sync, "prepare_sync", lambda *_: plan())
    monkeypatch.setattr(domains_sync, "apply_sync", lambda *_: (_ for _ in ()).throw(AssertionError()))
    result = domains_sync.run_sync(db, object(), object(), apply=False)
    assert result.additions == ["one.test"]
    assert db.commits == 0


def test_run_sync_applies_once_and_audits(monkeypatch):
    db = FakeDb()
    calls: list[str] = []
    monkeypatch.setattr(domains_sync, "prepare_sync", lambda *_: plan())
    monkeypatch.setattr(domains_sync, "apply_sync", lambda *_: calls.append("apply"))
    monkeypatch.setattr(domains_sync, "audit_sync", lambda *_: calls.append("audit"))
    domains_sync.run_sync(db, object(), object(), apply=True)
    assert calls == ["apply", "audit"]
    assert db.commits == 1


def test_run_sync_rejects_partial_snapshot(monkeypatch):
    db = FakeDb()
    calls: list[str] = []
    monkeypatch.setattr(domains_sync, "prepare_sync", lambda *_: plan(valid=False))
    monkeypatch.setattr(domains_sync, "mark_sync_error", lambda *_: calls.append("error"))
    result = domains_sync.run_sync(db, object(), object(), apply=True)
    assert result.ok is False
    assert calls == ["error"]
    assert db.commits == 1
