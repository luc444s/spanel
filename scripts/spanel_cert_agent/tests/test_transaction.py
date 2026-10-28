"""Instalación transaccional, rollback, sync y concurrencia (A.SPEC 0036)."""

from __future__ import annotations

import pytest
from spanel_cert_agent import transaction
from spanel_cert_agent.health import register_install_failure
from spanel_cert_agent.installer import install_paths
from spanel_cert_agent.operations import rollback_current, sync_hostname
from spanel_cert_agent.state import read_json, try_file_lock, write_state
from spanel_cert_agent.transaction import (
    InstallBusy,
    current_generation,
    previous_generation,
    transactional_install,
)
from support import (
    FakeCommands,
    FakeConnector,
    base_config,
    make_lineage,
    prepare_default,
)

HOST = "mail.systutor.com"
FORBIDDEN = ("certbot", "doveadm", "maildir", "passwd", "userdb", "chpasswd")


def _install(config, commands, *, verifier=None, content="cert"):
    lineage = make_lineage(config, HOST, content=content)
    return transactional_install(
        config, HOST, lineage, runner=commands, verifier=verifier
    )


def _permissive(_stage: str) -> bool:
    return True


def test_default_verifier_uses_serial_after_activation(tmp_path):
    """El serial esperado se lee tras activar; si se captura antes, toda
    activación se revierte por comparar contra el certificado viejo."""

    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    cert_path = install_paths(config, HOST)[0]
    commands.serials[str(cert_path)] = "OLD"
    first = _install(config, commands, verifier=_permissive)
    assert first.ok is True

    commands.serials[str(cert_path)] = "NEW"
    connector = FakeConnector({HOST: "NEW"})
    verifier = transaction.default_verifier(config, HOST, connector=connector)
    outcome = _install(config, commands, verifier=verifier, content="nuevo")

    assert outcome.ok is True
    assert outcome.rolled_back is False
    assert outcome.generation != first.generation
    assert current_generation(config, HOST) == outcome.generation


def test_transactional_install_activates_and_reloads_without_certbot(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    commands.serials[str(install_paths(config, HOST)[0])] = "NEW"

    outcome = _install(config, commands, verifier=_permissive)

    assert outcome.ok is True
    assert outcome.changed is True
    assert outcome.generation is not None
    assert current_generation(config, HOST) == outcome.generation
    cert, key = install_paths(config, HOST)
    assert cert.read_text(encoding="utf-8") == "cert"
    assert key.exists()
    assert HOST in config.postfix.sni_map_path.read_text(encoding="utf-8")
    assert f"local_name {HOST}" in config.dovecot.local_name_path.read_text(encoding="utf-8")
    assert commands.called("postmap")
    assert commands.called("postfix")
    assert commands.called("dovecot")
    for name in FORBIDDEN:
        assert not commands.called(name)
    assert outcome.warnings == []


def test_validation_failure_keeps_active_generation(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    first = _install(config, commands, verifier=_permissive)
    original = install_paths(config, HOST)[0].read_bytes()

    bad = FakeCommands(key_matches=False)
    second = _install(config, bad, verifier=_permissive, content="otro")

    assert second.ok is False
    assert second.changed is False
    assert current_generation(config, HOST) == first.generation
    assert install_paths(config, HOST)[0].read_bytes() == original
    assert current_generation(config, HOST) == first.generation


def _fail_once(stage_name: str):
    calls: list[str] = []

    def verifier(stage: str) -> bool:
        calls.append(stage)
        return not (stage == stage_name and calls.count(stage_name) == 1)

    return verifier, calls


def test_postfix_failure_rolls_back_both_and_reverifies(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    first = _install(config, commands, verifier=_permissive)
    original_map = config.postfix.sni_map_path.read_text(encoding="utf-8")
    dovecot_before = commands.names().count("dovecot")

    verifier, calls = _fail_once("postfix")
    second = _install(config, commands, verifier=verifier, content="nuevo")

    assert second.ok is False
    assert second.rolled_back is True
    assert current_generation(config, HOST) == first.generation
    assert install_paths(config, HOST)[0].read_text(encoding="utf-8") == "cert"
    assert config.postfix.sni_map_path.read_text(encoding="utf-8") == original_map
    assert commands.names().count("dovecot") > dovecot_before
    assert "all" in calls[calls.index("postfix") + 1 :]


def test_postfix_command_failure_rolls_back_both(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    first = _install(config, commands, verifier=_permissive)
    dovecot_before = commands.names().count("dovecot")

    commands.fail_commands = {"postfix"}
    second = _install(config, commands, verifier=_permissive, content="nuevo")

    assert second.ok is False
    assert second.rolled_back is True
    assert current_generation(config, HOST) == first.generation
    assert commands.names().count("dovecot") >= dovecot_before + 1


def test_dovecot_command_failure_rolls_back_both(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    first = _install(config, commands, verifier=_permissive)
    postfix_before = commands.names().count("postfix")

    commands.fail_commands = {"dovecot"}
    second = _install(config, commands, verifier=_permissive, content="nuevo")

    assert second.ok is False
    assert second.rolled_back is True
    assert current_generation(config, HOST) == first.generation
    # El rollback vuelve a recargar Postfix y Dovecot, no solo el que falló.
    assert commands.names().count("postfix") >= postfix_before + 2
    assert commands.names().count("dovecot") >= 2


def test_backup_restores_snippet_and_map(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    _install(config, commands, verifier=_permissive)
    original_map = config.postfix.sni_map_path.read_text(encoding="utf-8")
    config.postfix.sni_map_path.write_text("mapa-roto\n", encoding="utf-8")

    verifier, _calls = _fail_once("postfix")
    outcome = _install(config, commands, verifier=verifier, content="nuevo")

    assert outcome.rolled_back is True
    # El backup se toma después del cambio manual; restore devuelve ese estado.
    assert config.postfix.sni_map_path.read_text(encoding="utf-8") == "mapa-roto\n"
    assert original_map


def test_prune_failure_does_not_roll_back(tmp_path, monkeypatch):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()

    def broken_prune(*_args, **_kwargs):
        raise OSError("poda rota")

    monkeypatch.setattr(transaction, "_prune", broken_prune)
    outcome = _install(config, commands, verifier=_permissive)

    assert outcome.ok is True
    assert outcome.changed is True
    assert outcome.warnings


def test_second_concurrent_install_exits_without_changes(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    lock = config.locks_dir / f"{HOST}.lock"
    with try_file_lock(lock) as acquired:
        assert acquired is True
        with pytest.raises(InstallBusy):
            _install(config, commands, verifier=_permissive)
    assert current_generation(config, HOST) is None
    assert not install_paths(config, HOST)[0].exists()


def test_sync_only_installs_when_fingerprint_changes(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    lineage = make_lineage(config, HOST, content="v1")
    commands.serials[str(install_paths(config, HOST)[0])] = "NEW"

    first = sync_hostname(config, HOST, runner=commands, verifier=_permissive)
    assert first.ok is True
    assert first.changed is True
    generation = current_generation(config, HOST)

    again = sync_hostname(config, HOST, runner=commands, verifier=_permissive)
    assert again.ok is True
    assert again.changed is False
    assert current_generation(config, HOST) == generation

    (lineage / "fullchain.pem").write_text("v2", encoding="utf-8")
    changed = sync_hostname(config, HOST, runner=commands, verifier=_permissive)
    assert changed.ok is True
    assert changed.changed is True
    assert current_generation(config, HOST) != generation
    assert commands.called("postfix") and commands.called("dovecot")


def test_sync_respects_backoff_without_changing_certificate(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    _install(config, commands, verifier=_permissive)
    generation = current_generation(config, HOST)
    make_lineage(config, HOST, content="v2")

    state = read_json(config.state_path) or {}
    health = state.get("health") if isinstance(state.get("health"), dict) else {}
    register_install_failure(config, health, HOST, error="fallo previo")
    state["health"] = health
    write_state(config, state)

    outcome = sync_hostname(config, HOST, runner=commands, verifier=_permissive)
    assert outcome.ok is False
    assert outcome.changed is False
    assert "backoff" in (outcome.error or "")
    assert current_generation(config, HOST) == generation


def test_explicit_rollback_restores_previous_generation(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    first = _install(config, commands, verifier=_permissive, content="v1")
    second = _install(config, commands, verifier=_permissive, content="v2")
    assert previous_generation(config, HOST) == first.generation
    assert second.generation != first.generation

    outcome = rollback_current(config, HOST, runner=commands, verifier=_permissive)

    assert outcome.ok is True
    assert outcome.changed is True
    assert current_generation(config, HOST) == first.generation
    assert install_paths(config, HOST)[0].read_text(encoding="utf-8") == "v1"
    assert commands.called("postfix") and commands.called("dovecot")


def test_sync_without_lineage_fails_and_alerts_do_not_use_smtp(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    delivered: list[dict] = []

    outcome = sync_hostname(
        config, HOST, runner=commands, sender=delivered.append
    )

    assert outcome.ok is False
    assert "linaje" in (outcome.error or "")
    assert not commands.called("certbot")
    assert not any(name in ("sendmail", "postfix", "dovecot") for name in commands.names())


def test_install_writes_no_account_or_maildir_commands(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()

    _install(config, commands, verifier=_permissive)

    joined = " ".join(" ".join(call) for call in commands.calls).lower()
    for forbidden in FORBIDDEN + ("newalias", "saslpasswd"):
        assert forbidden not in joined
    assert "maildir" not in joined


def test_health_install_metadata_is_metadata_only(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    _install(config, commands, verifier=_permissive)

    state = read_json(config.state_path) or {}
    serialized = str(state)
    assert "PRIVATE KEY" not in serialized
    assert "BEGIN" not in serialized
