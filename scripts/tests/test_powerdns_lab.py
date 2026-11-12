from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/powerdns-lab.sh"
COMPOSE = ROOT / "scripts/powerdns-lab/compose.yml"
SCHEMA = ROOT / "scripts/powerdns-lab/init/001-schema.sql"
LIVE = ROOT / "scripts/tests/powerdns_lab_live.sh"


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [str(SCRIPT), *args], cwd=ROOT, env=merged, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def install_fake_tools(tmp_path: Path, docker_body: str, ss_body: str = "exit 0") -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    (bin_dir / "docker").write_text(
        "#!/usr/bin/env bash\n" + f"printf '%s\\n' \"$*\" >> {log!s}\n" + docker_body + "\n"
    )
    (bin_dir / "dig").write_text("#!/usr/bin/env bash\nexit 0\n")
    (bin_dir / "ss").write_text("#!/usr/bin/env bash\n" + ss_body + "\n")
    for path in bin_dir.iterdir():
        path.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "POWERDNS_LAB_STATE_DIR": str(tmp_path / "state"),
    }
    return env, log


def test_plan_is_read_only_and_uses_safe_defaults(tmp_path: Path) -> None:
    state = tmp_path / "state"
    result = run("plan", env={"HOME": str(tmp_path), "POWERDNS_LAB_STATE_DIR": str(state)})
    assert result.returncode == 0, result.stderr
    assert "127.0.0.1:5300 TCP and UDP" in result.stdout
    assert "API/PostgreSQL host ports: none" in result.stdout
    assert "not physical redundancy" in result.stdout
    assert not state.exists()


def test_compose_pins_images_and_publishes_only_dns() -> None:
    text = COMPOSE.read_text()
    assert "powerdns/pdns-auth-49:4.9.18@sha256:" in text
    assert "postgres:16.10-bookworm@sha256:" in text
    assert '127.0.0.1:${POWERDNS_LAB_DNS_PORT:-5300}:53/tcp' in text
    assert '127.0.0.1:${POWERDNS_LAB_DNS_PORT:-5300}:53/udp' in text
    ports_block = text.split("    ports:\n", 1)[1].split("    networks:\n", 1)[0]
    assert "5432" not in ports_block and "8081" not in ports_block
    assert "com.spanel.powerdns-lab: \"true\"" in text
    assert "--gpgsql-password" not in text
    assert "--api-key" not in text
    assert 'entrypoint: ["pdns_server"]' in text
    assert "${POWERDNS_LAB_STATE_DIR}/pdns.conf:/etc/powerdns/pdns.conf:ro" in text
    assert 'test: ["CMD", "pdns_control", "rping"]' in text
    assert "  pdns-postgres:\n" in text
    assert "  postgres:\n" not in text
    assert "gpgsql-host=pdns-postgres" in SCRIPT.read_text()


def test_up_refuses_tcp_udp_conflict_before_compose_mutation(tmp_path: Path) -> None:
    docker = """
if [[ "$1" == inspect ]]; then exit 1; fi
if [[ "$1 $2" == "network inspect" || "$1 $2" == "volume inspect" ]]; then exit 1; fi
exit 0
"""
    env, log = install_fake_tools(tmp_path, docker, 'echo "LISTEN owner fixture"')
    result = run("up", env=env)
    assert result.returncode != 0
    assert "selected DNS port is occupied" in result.stderr
    assert "compose --project-name" not in log.read_text()
    assert not (tmp_path / "state").exists()


def test_up_refuses_same_name_foreign_container(tmp_path: Path) -> None:
    docker = """
if [[ "$1" == info || "$1 $2" == "compose version" ]]; then exit 0; fi
if [[ "$1" == inspect ]]; then
  if [[ "$*" == *"-f"* ]]; then printf '\\n'; fi
  exit 0
fi
exit 1
"""
    env, log = install_fake_tools(tmp_path, docker)
    result = run("up", env=env)
    assert result.returncode != 0
    assert "refusing foreign container" in result.stderr
    assert not any(" rm " in f" {line} " for line in log.read_text().splitlines())
    assert "compose --project-name" not in log.read_text()


def test_generated_secret_permissions_and_no_values_in_output(tmp_path: Path) -> None:
    docker = """
if [[ "$1" == inspect ]]; then exit 1; fi
if [[ "$1 $2" == "network inspect" || "$1 $2" == "volume inspect" ]]; then exit 1; fi
if [[ "$*" == *" compose "*" up "* || "$1" == compose && "$*" == *" up "* ]]; then exit 1; fi
exit 0
"""
    env, _ = install_fake_tools(tmp_path, docker)
    result = run("up", env=env)
    state = tmp_path / "state"
    values = []
    for name in ("postgres-password", "api-key"):
        secret = (state / name).read_text().strip()
        values.append(secret)
        assert stat.S_IMODE((state / name).stat().st_mode) == 0o600
        assert secret not in result.stdout
        assert secret not in result.stderr
    config = state / "pdns.conf"
    config_text = config.read_text()
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert f"gpgsql-password={values[0]}" in config_text
    assert f"api-key={values[1]}" in config_text
    assert not any(value in result.stdout or value in result.stderr for value in values)


def test_process_command_text_contains_no_secret_flags() -> None:
    compose_text = COMPOSE.read_text()
    script_text = SCRIPT.read_text()
    assert "--gpgsql-password" not in compose_text
    assert "--api-key" not in compose_text
    assert "--gpgsql-password" not in script_text
    assert "--api-key" not in script_text
    assert 'entrypoint: ["pdns_server"]' in compose_text
    assert "NULL::INT" in script_text


def test_schema_is_readable_by_postgres_after_remote_transfer() -> None:
    assert stat.S_IMODE(SCHEMA.stat().st_mode) & 0o444 == 0o444
    live_text = LIVE.read_text()
    assert "chmod 644" in live_text
    assert "scripts/powerdns-lab/init/001-schema.sql" in live_text


def test_cleanup_has_no_broad_docker_commands() -> None:
    text = SCRIPT.read_text()
    forbidden = ("docker system prune", "docker volume prune", "docker network prune", "docker rm $(", "docker volume rm $(")
    assert not any(item in text for item in forbidden)
    assert 'docker rm -f "$AUTH"' in text
    assert 'docker rm -f "$DB"' in text
    assert 'docker network rm "$NETWORK"' in text
    assert 'docker volume rm "$VOLUME"' in text


def test_purge_removes_owned_config_but_refuses_foreign_state_file(tmp_path: Path) -> None:
    docker = """
if [[ "$1" == info || "$1 $2" == "compose version" ]]; then exit 0; fi
if [[ "$1" == inspect || "$1 $2" == "network inspect" || "$1 $2" == "volume inspect" ]]; then exit 1; fi
exit 0
"""
    env, _ = install_fake_tools(tmp_path, docker)
    state = tmp_path / "state"
    state.mkdir()
    (state / ".owner").write_text("spanel-powerdns-lab\n")
    for name in ("postgres-password", "api-key", "pdns.conf"):
        (state / name).write_text("owned\n")
    foreign = state / "foreign-file"
    foreign.write_text("leave me\n")
    result = run("down", "--purge", "--yes", env=env)
    assert result.returncode != 0
    assert "contains foreign files" in result.stderr
    assert not (state / "pdns.conf").exists()
    assert foreign.read_text() == "leave me\n"


@pytest.mark.parametrize("value", ["0", "65536", "abc", "53/tcp"])
def test_invalid_port_fails_even_for_plan(value: str, tmp_path: Path) -> None:
    result = run("plan", env={"POWERDNS_LAB_DNS_PORT": value, "HOME": str(tmp_path)})
    assert result.returncode != 0
