"""Pruebas de `spanel-repair.py` (A.SPEC 0044) — offline, sin Docker ni red.

Todo se inyecta: docker CLI (fixtures de dos servers "distintos"), DNS,
openssl, IP pública y reloj. Lo que se fija: autodetección con cero flags
(I1), dry-run por defecto (I2), cooldown (I3), parches atómicos con .bak
(I4), DNS mal → nunca restart (I5), y la forma del output JSON (I7).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "spanel_repair", SCRIPTS / "spanel-repair.py"
)
assert _SPEC and _SPEC.loader
sr = importlib.util.module_from_spec(_SPEC)
sys.modules["spanel_repair"] = sr
_SPEC.loader.exec_module(sr)

# --------------------------------------------------------------------------- #
# Fixtures: dos servers distintos (I1) — forma real de spanel-*.yml publicados
# --------------------------------------------------------------------------- #

SERVER_A_SITE = """# managed-by: spanel
http:
  routers:
    spanel-lucas-http:
      rule: Host(`lucas.spanel`)
      entryPoints:
      - web
      middlewares:
      - spanel-lucas-redirect
      service: spanel-lucas
      priority: 200
    spanel-lucas:
      rule: Host(`core.systutor.com`)
      entryPoints:
      - websecure
      service: spanel-lucas
      tls:
        certResolver: le-http
      priority: 100
  middlewares:
    spanel-lucas-redirect:
      redirectScheme:
        scheme: https
        permanent: true
  services:
    spanel-lucas:
      loadBalancer:
        servers:
        - url: http://spanel-lucas-wp:80
"""

SERVER_B_SITE = """# managed-by: spanel
http:
  routers:
    spanel-maria-http:
      rule: Host(`maria.net`) || Host(`www.maria.net`)
      entryPoints:
      - web
      service: spanel-maria
      priority: 200
    spanel-maria:
      rule: Host(`maria.net`)
      entryPoints: [websecure]
      service: spanel-maria
      priority: 100
  services:
    spanel-maria:
      loadBalancer:
        servers:
        - url: http://maria-app:80
"""

TRAEFIK_YML_A = """entryPoints:
  web:
    address: :80
  websecure:
    address: :443
providers:
  file:
    directory: /etc/traefik/dynamic
certificatesResolvers:
  le-http:
    acme:
      email: ops@systutor.com
      storage: /letsencrypt/acme.json
      httpChallenge:
        entryPoint: web
"""

TRAEFIK_YML_B = """providers:
  file:
    directory: /srv/traefik/dyn
certificatesResolvers:
  zerossl-prod:
    acme:
      storage: /acme.json
"""


class FakeDocker:
    """`docker ps/inspect/exec` por server, sin daemon real."""

    def __init__(self, ps_lines: list[str], inspect: dict, traefik_yml: str):
        self.ps_lines = ps_lines
        self.inspect = inspect
        self.traefik_yml = traefik_yml
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> sr.Result:
        self.calls.append(args)
        if args[:2] == ["ps", "--format"]:
            if args[2] == "{{.Names}}":
                return sr.Result("\n".join(line.split("\t")[0] for line in self.ps_lines))
            return sr.Result("\n".join(self.ps_lines))
        if args[0] == "inspect":
            return sr.Result(json.dumps([self.inspect]))
        if args[:2] == ["exec", "-"]:
            pass  # no debería ocurrir; exec lleva el container como args[1]
        if args[0] == "exec" and args[3].endswith(("traefik.yml", "traefik.yaml")):
            return sr.Result(self.traefik_yml)
        if args[0] == "restart":
            self.restarts = getattr(self, "restarts", 0) + 1
            return sr.Result("")
        return sr.Result("", "unexpected", 1)


def inspect_a(container: str = "proxy-edge") -> dict:
    """traefik de server A: flag de directory en Args."""
    return {
        "Name": f"/{container}",
        "Args": [
            "--providers.file.directory=/etc/traefik/dynamic",
            "--entrypoints.web.address=:80",
        ],
        "Config": {"Cmd": ["traefik"]},
        "Mounts": [
            {"Source": "/srv/a/traefik.yml", "Destination": "/etc/traefik/traefik.yml"}
        ],
    }


def inspect_b(container: str = "edge2") -> dict:
    """traefik de server B: directory SOLO en el traefik.yml montado (sin Args)."""
    return {
        "Name": f"/{container}",
        "Args": [],
        "Config": {"Cmd": ["/entrypoint.sh", "traefik"]},
        "Mounts": [
            {"Source": "/data/b/traefik.yml", "Destination": "/traefik.yml"},
            {"Source": "/data/b/dyn", "Destination": "/srv/traefik/dyn"},
        ],
    }


def tooling_for(server: str) -> tuple[sr.Tooling, FakeDocker]:
    if server == "a":
        docker = FakeDocker(
            [
                "proxy-edge\ttraefik:v3.0\ttraefik --x",
                "spanel-lucas-wp\twordpress\tapache2-foreground",
            ],
            inspect_a(),
            TRAEFIK_YML_A,
        )
        dns_map = {"core.systutor.com": "198.51.100.7", "lucas.spanel": "198.51.100.7"}
        public_ip = "198.51.100.7"
    else:
        docker = FakeDocker(["edge2\ttraefik\t/entrypoint.sh traefik"], inspect_b(), TRAEFIK_YML_B)
        dns_map = {"maria.net": "203.0.113.9"}
        public_ip = "203.0.113.9"
    tooling = sr.Tooling(
        docker=docker,
        dns=lambda host: dns_map.get(host),
        tls_issuer=lambda host: "issuer=CN=TRAEFIK DEFAULT CERT (Self Signed)",
        public_ip=lambda: public_ip,
        now=lambda: 1_000_000.0,
    )
    return tooling, docker


# --------------------------------------------------------------------------- #
# Autodetección (I1): dos servers distintos, cero flags
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("server,container,cdir,resolver", [
    ("a", "proxy-edge", "/etc/traefik/dynamic", "le-http"),
    ("b", "edge2", "/srv/traefik/dyn", "zerossl-prod"),
])
def test_detect_two_servers_zero_flags(server, container, cdir, resolver):
    tooling, _ = tooling_for(server)
    det = sr.detect(tooling)
    assert det.traefik_container == container
    assert det.config_dir == cdir
    assert det.resolvers == [resolver]


def test_detect_no_traefik_returns_none():
    docker = FakeDocker(["nginx\tnginx\tnginx -g'], []"], {"Name": "/nginx"}, "")
    tooling = sr.Tooling(docker=docker)
    assert sr.detect(tooling) is None


def test_detect_no_dir_raises():
    docker = FakeDocker(
        ["edge\ttraefik\ttraefik"],
        {"Name": "/edge", "Args": [], "Config": {"Cmd": []}, "Mounts": []},
        "entryPoints:\n  web:\n    address: :80\n",
    )
    with pytest.raises(sr.RepairError):
        sr.detect(sr.Tooling(docker=docker))


def test_public_ip_disabled_falls_back():
    """Sin socket útil (IP None) R2 con mismatch es PENDIENTE, no crash."""
    tooling, _ = tooling_for("a")
    tooling.public_ip = lambda: None
    tooling.dns = lambda host: "10.0.0.99"
    args = sr.build_parser().parse_args(["--config-dir", "/tmp/nonexistent-x"])
    findings, code = sr.check_host(args, tooling)
    assert code == sr.EXIT_PENDING
    assert any("A record" in f.action or "dynamic-config dir" in f.action for f in findings)


# --------------------------------------------------------------------------- #
# Parser de routers
# --------------------------------------------------------------------------- #


def test_parse_routers_a():
    routers = sr.parse_routers(SERVER_A_SITE)
    names = {r.name: r for r in routers}
    assert set(names) == {"spanel-lucas-http", "spanel-lucas"}
    assert names["spanel-lucas-http"].entrypoints == ["web"]
    assert names["spanel-lucas"].entrypoints == ["websecure"]
    assert sr.resolver_of(names["spanel-lucas"]) == "le-http"


def test_parse_routers_b_flow_and_or():
    routers = sr.parse_routers(SERVER_B_SITE)
    by = {r.name: r for r in routers}
    assert by["spanel-maria-http"].entrypoints == ["web"]
    assert by["spanel-maria"].entrypoints == ["websecure"]
    assert sr.resolver_of(by["spanel-maria"]) is None  # R3 candidato


# --------------------------------------------------------------------------- #
# R1: exclusión de challenge
# --------------------------------------------------------------------------- #


def test_r1_detect_and_patch(tmp_path):
    path = tmp_path / "spanel-lucas.yml"
    path.write_text(SERVER_A_SITE, encoding="utf-8")
    original = path.read_text()
    findings, patched = sr.rule_r1(path, SERVER_A_SITE, fix=True)
    assert patched == [path]
    fixed = [f for f in findings if f.status == "fixed"]
    assert len(fixed) == 1
    new_text = path.read_text()
    assert "!(PathPrefix(`/.well-known/acme-challenge`))" in new_text
    # I4: .bak creado y reversibilidad exacta
    backs = list(tmp_path.glob("spanel-lucas.yml.bak.*"))
    assert len(backs) == 1
    assert backs[0].read_text() == original
    # preserva formato/comentarios
    assert new_text.startswith("# managed-by: spanel")
    assert "priority: 200" in new_text
    # idempotente: segundo run no reparchea
    _findings2, patched2 = sr.rule_r1(path, path.read_text(), fix=True)
    assert patched2 == []


def test_r1_dry_run_planned_no_write(tmp_path):
    path = tmp_path / "spanel-lucas.yml"
    path.write_text(SERVER_A_SITE, encoding="utf-8")
    findings, patched = sr.rule_r1(path, SERVER_A_SITE, fix=False)
    assert not patched
    assert all(f.status == "planned" for f in findings)
    assert path.read_text() == SERVER_A_SITE


def test_r1_multi_or_rule_gets_parens(tmp_path):
    path = tmp_path / "spanel-maria.yml"
    path.write_text(SERVER_B_SITE, encoding="utf-8")
    _, patched = sr.rule_r1(path, SERVER_B_SITE, fix=True)
    assert patched == [path]
    text = path.read_text()
    assert "(Host(`maria.net`) || Host(`www.maria.net`)) && !(PathPrefix" in text


# --------------------------------------------------------------------------- #
# R2: cert default (I5: DNS mal → jamás restart)
# --------------------------------------------------------------------------- #


def test_r2_dns_match_would_restart(tmp_path):
    path = tmp_path / "spanel-lucas.yml"
    path.write_text(SERVER_A_SITE, encoding="utf-8")
    tooling, _ = tooling_for("a")
    findings = sr.rule_r2(
        path, SERVER_A_SITE, tooling, sr.public_ip_value(tooling, _args())
    )
    assert any("restart" in f.action and f.rule == "RESTART" for f in findings)


def test_r2_dns_mismatch_no_restart(tmp_path):
    path = tmp_path / "spanel-lucas.yml"
    path.write_text(SERVER_A_SITE, encoding="utf-8")
    tooling, docker = tooling_for("a")
    tooling.dns = lambda host: "192.0.2.44"  # A record apuntando mal
    args = sr.build_parser().parse_args(["--config-dir", str(tmp_path)])
    findings = sr.rule_r2(path, SERVER_A_SITE, tooling, sr.public_ip_value(tooling, args))
    assert not docker.calls or docker.calls[-1][0] != "restart"
    pendings = [f for f in findings if f.status == "pendiente" and f.rule == "R2"]
    assert pendings and "A record" in pendings[0].action


def _args():
    return sr.build_parser().parse_args([])


def test_r2_healthy_issuer_ok(tmp_path):
    path = tmp_path / "spanel-lucas.yml"
    path.write_text(SERVER_A_SITE, encoding="utf-8")
    tooling, _ = tooling_for("a")
    tooling.tls_issuer = lambda host: "issuer=C=US, O=Let's Encrypt, CN=R11"
    findings = sr.rule_r2(path, SERVER_A_SITE, tooling, "198.51.100.7")
    assert not [f for f in findings if f.rule == "RESTART"]


# --------------------------------------------------------------------------- #
# R3: websecure sin certResolver
# --------------------------------------------------------------------------- #


def test_r3_patch_inserts_tls_before_block_end(tmp_path):
    path = tmp_path / "spanel-maria.yml"
    path.write_text(SERVER_B_SITE, encoding="utf-8")
    _findings, patched = sr.rule_r3(path, SERVER_B_SITE, "zerossl-prod", fix=True)
    assert patched == [path]
    text = path.read_text()
    assert "tls:" in text and "certResolver: zerossl-prod" in text
    # el bloque insertado cae dentro del router (antes de `services:`)
    tls_idx = text.index("    tls:")
    assert tls_idx > text.index("    spanel-maria:\n      rule")
    assert tls_idx < text.index("  services:")
    backs = list(tmp_path.glob("*.bak.*"))
    assert backs


def test_r3_no_resolver_available_is_pendiente(tmp_path):
    path = tmp_path / "spanel-maria.yml"
    path.write_text(SERVER_B_SITE, encoding="utf-8")
    findings, patched = sr.rule_r3(path, SERVER_B_SITE, None, fix=True)
    assert patched == []
    assert all(f.status == "pendiente" for f in findings)


def test_r3_dry_run(tmp_path):
    path = tmp_path / "spanel-maria.yml"
    path.write_text(SERVER_B_SITE, encoding="utf-8")
    findings, patched = sr.rule_r3(path, SERVER_B_SITE, "zerossl-prod", fix=False)
    assert patched == []
    assert all(f.status == "planned" for f in findings)


# --------------------------------------------------------------------------- #
# R4: huérfanos → .orphans, nunca borrar
# --------------------------------------------------------------------------- #


def test_r4_moves_orphan_to_orphans_dir(tmp_path):
    path = tmp_path / "spanel-ghost.yml"
    path.write_text(SERVER_A_SITE, encoding="utf-8")
    finding = sr.rule_r4(path, SERVER_A_SITE, ["otro-app"], tmp_path, fix=True)
    assert finding.status == "fixed"
    assert not path.exists()
    assert (tmp_path / ".orphans" / "spanel-ghost.yml").exists()


def test_r4_alive_container_ok(tmp_path):
    path = tmp_path / "spanel-lucas.yml"
    path.write_text(SERVER_A_SITE, encoding="utf-8")
    finding = sr.rule_r4(path, SERVER_A_SITE, ["spanel-lucas-wp"], tmp_path, fix=False)
    assert finding.status == "ok"


# --------------------------------------------------------------------------- #
# Ciclo completo: check vs fix, restart, cooldown (I2/I3)
# --------------------------------------------------------------------------- #


class Ctx:
    def __init__(self, tmp_path, server="a"):
        self.tmp = tmp_path
        self.tooling, self.docker = tooling_for(server)
        self.config = tmp_path / "dyn"
        self.config.mkdir(exist_ok=True)
        (self.config / "spanel-lucas.yml").write_text(SERVER_A_SITE, encoding="utf-8")

    def args(self, **kw):
        argv = ["--config-dir", str(self.config)] + [
            item for k, v in kw.items() for item in ([f"--{k.replace('_', '-')}"] if v is True else [f"--{k.replace('_', '-')}", str(v)])
        ]
        return sr.build_parser().parse_args(argv)


def test_check_dry_run_writes_nothing_and_no_restart(tmp_path):
    ctx = Ctx(tmp_path)
    args = ctx.args()
    _findings, code = sr.check_host(args, ctx.tooling)
    assert code == sr.EXIT_PENDING
    # ninguna escritura: ni .bak, ni state, ni orphans
    assert not list(ctx.config.glob("*.bak.*"))
    assert not (ctx.config / ".spanel-repair-state.json").exists()
    assert not any(call[0] == "restart" for call in ctx.docker.calls)


def test_fix_patches_and_restarts_once_with_cooldown(tmp_path):
    ctx = Ctx(tmp_path)
    args = ctx.args(fix=True)
    _findings, code = sr.check_host(args, ctx.tooling)
    assert code == sr.EXIT_PENDING
    assert [c for c in ctx.docker.calls if c[0] == "restart"]
    assert list(ctx.config.glob("*.bak.*"))  # .bak de los parches
    # re-ejecución inmediata: cooldown activo → NO segundo restart (I3)
    restarts_before = len([c for c in ctx.docker.calls if c[0] == "restart"])
    sr.check_host(args, ctx.tooling)
    restarts_after = len([c for c in ctx.docker.calls if c[0] == "restart"])
    assert restarts_before == restarts_after
    # tras vencer el cooldown vuelve a poder reiniciar
    ctx.tooling.now = lambda: 5_000_000.0
    (ctx.config / "spanel-lucas.yml").write_text(SERVER_A_SITE, encoding="utf-8")
    sr.check_host(args, ctx.tooling)
    assert len([c for c in ctx.docker.calls if c[0] == "restart"]) == restarts_before + 1


def test_main_json_shape(tmp_path, capsys):
    ctx = Ctx(tmp_path)
    argv = ["--json", "--log-file", str(tmp_path / "rep.log")]
    code = sr.main(argv, ctx.tooling)
    out = json.loads(capsys.readouterr().out)
    assert code == out["exit"] == sr.EXIT_PENDING
    assert set(out["findings"][0]) == {"rule", "target", "status", "action", "detail", "ts"}
    log_lines = (tmp_path / "rep.log").read_text().strip().splitlines()
    assert json.loads(log_lines[0])  # JSON-lines en el log con --json


def test_main_no_traefik_no_crash(tmp_path, capsys):
    docker = FakeDocker(["web\tnginx\tnginx"], {"Name": "/web"}, "")
    tooling = sr.Tooling(docker=docker)
    argv = ["--json", "--log-file", str(tmp_path / "rep.log")]
    code = sr.main(argv, tooling)
    out = json.loads(capsys.readouterr().out)
    assert code == out["exit"] == sr.EXIT_PENDING
    assert any("traefik" in f["action"] for f in out["findings"])


def test_main_json_ok_exit_zero(tmp_path, capsys):
    ctx = Ctx(tmp_path)
    # site sano: exclusión presente, resolver presente, container vivo, issuer real
    healthy = SERVER_A_SITE.replace(
        "rule: Host(`lucas.spanel`)",
        f"rule: (Host(`lucas.spanel`)) && {sr.CHALLENGE_EXCLUSION}",
    )
    (ctx.config / "spanel-lucas.yml").write_text(healthy, encoding="utf-8")
    ctx.tooling.tls_issuer = lambda host: "issuer=C=US O=Let's Encrypt CN=R11"
    code = sr.main(["--json", "--log-file", str(tmp_path / "l.log"), "--config-dir", str(ctx.config)], ctx.tooling)
    out = json.loads(capsys.readouterr().out)
    assert code == out["exit"] == sr.EXIT_OK


if __name__ == "__main__":  # standalone-runnable como test_mail_cert_live.py
    sys.exit(pytest.main([__file__, "-q"]))
