from __future__ import annotations

import base64
import stat
from pathlib import Path

import pytest

from scripts.configure_apps import (
    ConfigureError,
    compose_commands,
    configure,
    detect_public_url,
    ensure_key,
    main,
    parse_env,
    render_env,
    validate_public_url,
    write_env,
)

COMPOSE = """
services:
  app:
    labels:
      - "traefik.http.routers.spanel.rule=Host(`spanel.example.com`)"
      - "traefik.http.routers.spanel-http.rule=Host(`spanel.example.com`)"
"""


def test_detects_unique_traefik_host_and_respects_precedence() -> None:
    assert detect_public_url(None, {}, COMPOSE) == "https://spanel.example.com"
    assert detect_public_url(
        None, {"SPANEL_APPS_PUBLIC_URL": "https://configured.example"}, COMPOSE,
    ) == "https://configured.example"
    assert detect_public_url(
        "https://explicit.example/", {"SPANEL_APPS_PUBLIC_URL": "https://old.example"}, COMPOSE,
    ) == "https://explicit.example"


def test_detection_fails_closed_for_zero_or_multiple_hosts() -> None:
    with pytest.raises(ConfigureError, match="Could not detect"):
        detect_public_url(None, {}, "services: {}")
    with pytest.raises(ConfigureError, match="Multiple"):
        detect_public_url(None, {}, COMPOSE + "\n# Host(`other.example.com`)\n")


@pytest.mark.parametrize(
    "value",
    [
        "http://remote.example.com",
        "https://user:pass@example.com",
        "https://example.com/path",
        "ftp://example.com",
    ],
)
def test_public_url_rejects_unsafe_origins(value: str) -> None:
    with pytest.raises(ConfigureError):
        validate_public_url(value)


def test_public_url_allows_loopback_for_development() -> None:
    assert validate_public_url("http://127.0.0.1:5173/") == "http://127.0.0.1:5173"


def test_key_is_32_bytes_private_and_reused(tmp_path: Path) -> None:
    key = tmp_path / "private" / "apps_key"
    assert ensure_key(key) is True
    decoded = base64.urlsafe_b64decode(key.read_text().strip())
    assert len(decoded) == 32
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700
    original = key.read_bytes()
    assert ensure_key(key) is False
    assert key.read_bytes() == original


def test_invalid_or_symlink_key_fails(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid"
    invalid.write_text("not-a-key", encoding="ascii")
    with pytest.raises(ConfigureError):
        ensure_key(invalid)
    target = tmp_path / "target"
    target.write_text(base64.urlsafe_b64encode(b"x" * 32).decode(), encoding="ascii")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ConfigureError, match="non-symlink"):
        ensure_key(link)


def test_render_env_preserves_unrelated_values_and_removes_duplicates() -> None:
    canary = "MAIL_PASSWORD=do-not-print-or-change"
    original = (
        "# production\n"
        f"{canary}\n"
        "SPANEL_APPS_PUBLIC_URL=https://old.example\n"
        "SPANEL_APPS_PUBLIC_URL=https://duplicate.example\n"
        "POSTGRES_DB=spanel\n"
    )
    values = {
        "SPANEL_APPS_PUBLIC_URL": "https://new.example",
        "SPANEL_APPS_ENCRYPTION_KEY_PATH": "/srv/spanel/secrets/apps_key",
        "SPANEL_APPS_ENCRYPTION_KEY_FILE": "/run/secrets/apps_key",
        "SPANEL_APPS_STATE_ROOT": "/var/lib/spanel/apps",
        "SPANEL_APPS_KEY_VERSION": "1",
    }
    rendered = render_env(original, values)
    assert canary in rendered
    assert "POSTGRES_DB=spanel" in rendered
    assert rendered.count("SPANEL_APPS_PUBLIC_URL=") == 1
    assert "https://old.example" not in rendered
    assert parse_env(rendered) | {} == parse_env(rendered)
    for key, value in values.items():
        assert parse_env(rendered)[key] == value


def test_write_env_creates_private_backup_and_atomic_target(tmp_path: Path) -> None:
    env = tmp_path / ".env.docker"
    env.write_text("SECRET=canary\n", encoding="utf-8")
    env.chmod(0o644)
    backup = write_env(env, "SECRET=canary\nNEW=value\n")
    assert backup is not None
    assert backup.read_text(encoding="utf-8") == "SECRET=canary\n"
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_compose_command_sequence_never_uses_down(tmp_path: Path) -> None:
    commands = compose_commands(
        tmp_path / ".env.docker", tmp_path / "docker-compose.yml",
        restart=True, build=True,
    )
    assert commands[0][-2:] == ["config", "--quiet"]
    assert [command[-1] for command in commands[1:]] == ["app", "apps-worker"]
    assert "--build" in commands[1]
    assert all("down" not in command for command in commands)


def test_configure_orders_app_health_then_worker(tmp_path: Path) -> None:
    env = tmp_path / ".env.docker"
    env.write_text("POSTGRES_DB=spanel\n", encoding="utf-8")
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(COMPOSE, encoding="utf-8")
    events: list[str] = []

    def runner(command: list[str]) -> None:
        events.append("config" if "config" in command else command[-1])

    configure(
        env_file=env, compose_file=compose, key_path=tmp_path / "secrets/apps_key",
        public_url=None, restart=True, build=True, dry_run=False,
        runner=runner, waiter=lambda: events.append("healthy"),
    )
    assert events == ["config", "app", "healthy", "apps-worker"]
    assert parse_env(env.read_text(encoding="utf-8"))["SPANEL_APPS_PUBLIC_URL"] == (
        "https://spanel.example.com"
    )


def test_dry_run_changes_nothing_and_runs_no_commands(tmp_path: Path) -> None:
    env = tmp_path / ".env.docker"
    original = "POSTGRES_DB=spanel\n"
    env.write_text(original, encoding="utf-8")
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(COMPOSE, encoding="utf-8")
    key = tmp_path / "secrets/apps_key"
    commands: list[list[str]] = []
    configure(
        env_file=env, compose_file=compose, key_path=key, public_url=None,
        restart=True, build=True, dry_run=True, runner=commands.append,
    )
    assert env.read_text(encoding="utf-8") == original
    assert not key.exists()
    assert commands == []


def test_cli_never_prints_existing_key_value(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = tmp_path / ".env.docker"
    env.write_text("POSTGRES_DB=spanel\n", encoding="utf-8")
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(COMPOSE, encoding="utf-8")
    key = tmp_path / "apps_key"
    canary = base64.urlsafe_b64encode(b"secret-canary-material-32-bytes!"[:32]).decode()
    key.write_text(canary, encoding="ascii")
    assert main([
        "--env-file", str(env), "--compose-file", str(compose),
        "--key-path", str(key), "--dry-run",
    ]) == 0
    captured = capsys.readouterr()
    assert canary not in captured.out
    assert canary not in captured.err
