"""Binary-safe Docker/SSH PHP driver for an authorized file target."""

from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException

from systutor.contracts.file_targets import FileTarget

SCRIPT = Path(__file__).with_name("container.php").read_text(encoding="utf-8").removeprefix("<?php")
TRANSFER_LIMIT = 50 * 1024 * 1024
EDIT_LIMIT = 1024 * 1024


def _docker_command(target: FileTarget, env: dict[str, str], encoded: str) -> str:
    # All user-supplied fields are encoded into a single quoted argv element.
    if target.runtime == "volume":
        # Static sites run nginx:alpine, with no PHP and no www-data user. Use
        # the already-required wp-cli image (PHP + ZipArchive) as a disposable
        # helper that mounts the target's volumes, so the fixed script is the
        # same for both kinds of site.
        helper = env.get("SPANEL_FILEMANAGER_HELPER_IMAGE", "").strip() or "wordpress:cli"
        return (
            f"docker run --rm -i --network none --user 0 "
            f"--volumes-from {shlex.quote(target.container)} "
            f"--entrypoint php {shlex.quote(helper)} -r "
            f"{shlex.quote(SCRIPT)} {shlex.quote(encoded)}"
        )
    return (
        f"docker exec -i -u www-data {shlex.quote(target.container)} php -r "
        f"{shlex.quote(SCRIPT)} {shlex.quote(encoded)}"
    )


def _command(target: FileTarget, op: str, env: dict[str, str], **arguments: object) -> list[str]:
    encoded = base64.b64encode(
        json.dumps({"root": target.root, "op": op, "limit": TRANSFER_LIMIT, **arguments}).encode()
    ).decode()
    command = _docker_command(target, env, encoded)
    if env.get("SPANEL_DOCKER_TRANSPORT", "ssh") == "local":
        return ["sh", "-c", command]
    return [
        "sshpass",
        "-e",
        "ssh",
        "-p",
        env.get("SPANEL_DOCKER_SSH_PORT", "22"),
        "-o",
        f"StrictHostKeyChecking={env.get('SPANEL_DOCKER_SSH_STRICT_HOST_KEY', 'yes')}",
        "-o",
        "LogLevel=ERROR",
        f"{env.get('SPANEL_DOCKER_SSH_USER', '')}@{env.get('SPANEL_DOCKER_SSH_HOST', '')}",
        command,
    ]


def start(
    target: FileTarget, op: str, env: dict[str, str], **arguments: object
) -> subprocess.Popen[bytes]:
    child_env = dict(os.environ)
    if env.get("SPANEL_DOCKER_TRANSPORT") != "local":
        child_env["SSHPASS"] = env.get("SPANEL_DOCKER_SSH_PASSWORD") or env.get(
            "MAIL_SERVER_PASSWORD", ""
        )
    return subprocess.Popen(
        _command(target, op, env, **arguments),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
    )


def _error(stderr: bytes) -> HTTPException:
    try:
        message = json.loads(stderr.decode())
        code = int(message["status"])
        if code in (400, 403, 404, 409, 413):
            return HTTPException(code, str(message["message"]))
    except (UnicodeError, ValueError, KeyError, TypeError):
        pass
    return HTTPException(502, "No se pudo acceder a los archivos del contenedor")


def operation(target: FileTarget, op: str, env: dict[str, str], **arguments: object) -> dict:
    process = start(target, op, env, **arguments)
    try:
        stdout, stderr = process.communicate(timeout=60)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise HTTPException(504, "El contenedor no respondió") from exc
    if process.returncode:
        raise _error(stderr)
    try:
        return json.loads(stdout)
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(502, "Respuesta inválida del contenedor") from exc


def upload(
    target: FileTarget,
    op: str,
    env: dict[str, str],
    source: BinaryIO,
    limit: int,
    **arguments: object,
) -> dict:
    process = start(target, op, env, **arguments)
    assert process.stdin is not None
    count = 0
    try:
        while chunk := source.read(65536):
            count += len(chunk)
            if count > limit:
                raise HTTPException(413, "Archivo demasiado grande")
            try:
                process.stdin.write(chunk)
            except BrokenPipeError:
                # The container can reject before reading all input (409/403).
                # Wait and surface its JSON error instead of a generic 500.
                process.stdin.close()
                stderr = process.stderr.read() if process.stderr else b""
                process.wait(timeout=30)
                raise _error(stderr) from None
        process.stdin.close()
        assert process.stdout is not None and process.stderr is not None
        stdout, stderr = process.stdout.read(), process.stderr.read()
        process.wait(timeout=60)
        if process.returncode:
            raise _error(stderr)
        return json.loads(stdout)
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe:
                pipe.close()


def download(
    target: FileTarget, env: dict[str, str], path: str
) -> tuple[subprocess.Popen[bytes], Iterator[bytes]]:
    process = start(target, "download", env, path=path)
    assert process.stdin is not None and process.stdout is not None
    process.stdin.close()
    stream = process.stdout

    def chunks() -> Iterator[bytes]:
        try:
            while data := stream.read(65536):
                yield data
        finally:
            close_process(process)

    return process, chunks()


def close_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait()
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe:
            pipe.close()
