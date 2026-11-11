#!/usr/bin/env python3
"""Script de prueba para A.SPEC 0007: ¿llegamos al Docker de la otra maquina?

Este es el primer test de verdad del plugin `hosting`. Todavia no hay plugin, no
hay base de datos y no hay endpoint. Solo se prueban tres cosas, en este orden:

    1. que el archivo .env exista y tenga lo necesario
    2. que la conexion SSH entre y que del otro lado haya un daemon de Docker
    3. que `docker ps` responda y quels contenedores hay

Y de paso, revisar si alguno de esos contenedores parece WordPress.

No se hardcodea ninguna credencial: todo sale del entorno.

Uso:
    python3 scripts/probe_docker.py
    python3 scripts/probe_docker.py --env-file otro.env
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "vendor" / "systutor-core"
DEFAULT_ENV_FILE = CORE_ROOT / ".env"
# `plugins` lives in the core root; `systutor` lives in its `src`. Both go
# first so a checkout does not silently import an editable install that points
# at another clone.
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))

from plugins.hosting.backend.discovery import (  # pyright: ignore[reportMissingImports]
    destinos_de_montaje,
    detectar_capa1,
    detectar_todos,
    entorno_de_inspect,
)
from plugins.hosting.backend.transport import (  # pyright: ignore[reportMissingImports]
    build_docker_argv,
    load_env_file,
    run,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()

    if not args.env_file.is_file():
        print(f"FAILED: environment file does not exist: {args.env_file}")
        return 1

    env = load_env_file(args.env_file)
    print(f"Environment: {args.env_file}")
    print(f"Transport: {env.get('SPANEL_DOCKER_TRANSPORT', 'ssh')}")

    faltan = [
        clave
        for clave in ("SPANEL_DOCKER_SSH_HOST", "SPANEL_DOCKER_SSH_USER", "MAIL_SERVER_PASSWORD")
        if not env.get(clave)
    ]
    if faltan:
        print(f"FAILED: missing data in .env: {', '.join(faltan)}")
        return 1

    destino = f"{env['SPANEL_DOCKER_SSH_USER']}@{env['SPANEL_DOCKER_SSH_HOST']}"
    print(f"Target:    {destino}\n")

    # Test 1: there is a Docker daemon on the other side.
    print("1) Docker on the other side")
    resultado = run(
        build_docker_argv("docker version --format '{{.Server.Version}}'", env=env), env=env
    )
    if resultado.returncode != 0:
        print(f"   FAILED: {resultado.stderr.strip() or 'no error message'}")
        if resultado.returncode == 124:
            print("   If the machine is on Tailscale, the connection never arrived.")
            print("   Check whether Tailscale is running before blaming the script.")
        return 1
    print(f"   OK. Daemon version: {resultado.stdout.strip()}\n")

    # Test 2: the container list.
    print("2) Container list")
    # '{{json .}}' instead of 'json': the short form is new in Docker 27 and
    # more fragile. The long form has existed for a long time and carries the
    # same information.
    resultado = run(build_docker_argv("docker ps -a --format '{{json .}}'", env=env), env=env)
    if resultado.returncode != 0:
        print(f"   FAILED: {resultado.stderr.strip() or 'no error message'}")
        return 1

    contenedores: list[dict] = []
    for linea in resultado.stdout.splitlines():
        if not linea.strip():
            continue
        try:
            contenedores.append(json.loads(linea))
        except json.JSONDecodeError:
            print(f"   WARN: line is not JSON, skipping it: {linea[:80]}")

    if not contenedores:
        print("   There are no containers. Docker works, but it is empty.")
        return 0

    print(f"   OK. {len(contenedores)} containers.\n")

    # Test 3: which of those look like WordPress.
    print("3) Which ones look like WordPress")
    print("-" * 72)
    for container in contenedores:
        nombre = (container.get("Names") or "(no name)").split(",")[0]
        imagen = container.get("Image") or "(no image)"
        estado = container.get("State") or "?"
        barato = detectar_capa1(container)
        marca = "WP  " if barato.es_sitio else "    "
        print(f"{marca}{nombre[:28]:<28} {estado:<9} {imagen[:24]:<24} {barato.por_que}")

    print("-" * 72)

    # Second pass: ONLY the ones that passed the prefilter, now with
    # `docker inspect`, which does return the real mounts and variables. One
    # call per candidate, and there are few: the whole list already came from
    # a single `docker ps` call.
    detalles: dict[str, object] = {}

    def inspeccionar(nombre: str) -> object:
        detalle = run(
            build_docker_argv(f"docker inspect --format '{{{{json .}}}}' {nombre}", env=env),
            env=env,
        )
        if detalle.returncode != 0:
            print(f"   WARN: {nombre}: could not inspect ({detalle.stderr.strip()[:60]})")
            return {}
        try:
            info = json.loads(detalle.stdout)
        except json.JSONDecodeError:
            print(f"   WARN: {nombre}: inspect returned something that is not JSON")
            return {}
        detalles[nombre] = info
        return info

    print("\n4) Candidate details (docker inspect)")
    candidatos = detectar_todos(contenedores, inspeccionar)
    for nombre, candidato in candidatos:
        if not candidato.es_sitio:
            print(f"   {nombre}: discarded. {candidato.por_que}")
            continue
        gestion = "yes" if candidato.gestionable else "no (spanel.managed excludes it)"
        print(
            f"   {nombre} [{candidato.senal}] {candidato.por_que}. "
            f"Can we touch it? {gestion}"
        )
        montajes = ", ".join(destinos_de_montaje(detalles.get(nombre, {}))) or "(none)"
        base = entorno_de_inspect(detalles.get(nombre, {})).get("WORDPRESS_DB_HOST")
        print(f"      mounts:     {montajes}")
        print(f"      database:   {base or '(WORDPRESS_DB_HOST is missing)'}")

    sitios = [nombre for nombre, candidato in candidatos if candidato.es_sitio]
    print(f"\n{len(sitios)} of {len(contenedores)} containers are WordPress sites.")
    if not sitios:
        print("If you expected to see WordPress here, the list above shows where it looks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
