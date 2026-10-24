"""Opt-in end-to-end file operations in a disposable folder of an owned lab site."""

from __future__ import annotations

import io
import os
import secrets
import shlex
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

CORE = Path(__file__).resolve().parents[2] / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE / "src"))
sys.path.insert(0, str(CORE))

from plugins.filemanager.backend.driver import TRANSFER_LIMIT, download, operation, upload
from plugins.hosting.backend.settings import docker_env, get_hosting_settings
from plugins.hosting.backend.transport import build_docker_argv, run
from systutor.contracts.file_targets import FileTarget


def test_wordpress_filemanager_binary_roundtrip_and_zip_confinement() -> None:
    if os.getenv("SPANEL_LIVE_TESTS") != "1":
        pytest.skip("SPANEL_LIVE_TESTS=1 requerido")
    container = os.getenv("SPANEL_FILEMANAGER_TEST_CONTAINER", "").strip()
    if not container:
        pytest.skip("SPANEL_FILEMANAGER_TEST_CONTAINER requerido")
    settings = get_hosting_settings()
    target = FileTarget(container, settings.files_root, "temporary-test")
    env = docker_env(settings)
    folder = ".spanel-filemanager-test-" + secrets.token_hex(8)
    operation(target, "mkdir", env, path=folder)
    try:
        original = b"begin\x00\xffend"
        uploaded = upload(
            target, "upload", env, io.BytesIO(original), TRANSFER_LIMIT,
            path=folder + "/binary.dat",
        )
        assert uploaded["size"] == len(original)
        process, chunks = download(target, env, folder + "/binary.dat")
        assert b"".join(chunks) == original
        operation(target, "rename", env, path=folder + "/binary.dat", name="renamed.dat")
        operation(target, "zip", env, path=folder, name="archive.zip", entries=["renamed.dat"])
        operation(target, "mkdir", env, path=folder + "/extracted")
        assert operation(
            target, "extract", env, path=folder + "/archive.zip", destination=folder + "/extracted"
        )["extracted"] == 1
        process, chunks = download(target, env, folder + "/extracted/renamed.dat")
        assert b"".join(chunks) == original

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("../escape.txt", "unsafe")
        upload(
            target, "upload", env, io.BytesIO(archive.getvalue()), TRANSFER_LIMIT,
            path=folder + "/unsafe.zip",
        )
        with pytest.raises(HTTPException) as error:
            operation(target, "extract", env, path=folder + "/unsafe.zip", destination=folder)
        assert error.value.status_code == 400
        with pytest.raises(HTTPException) as error:
            operation(target, "list", env, path="../")
        assert error.value.status_code == 400

        upload(
            target, "upload", env, io.BytesIO(b"first"), TRANSFER_LIMIT,
            path=folder + "/edit.txt",
        )
        current = operation(target, "read", env, path=folder + "/edit.txt")
        upload(
            target, "write", env, io.BytesIO(b"second"), TRANSFER_LIMIT,
            path=folder + "/edit.txt", version=current["version"],
        )
        with pytest.raises(HTTPException) as error:
            upload(
                target, "write", env, io.BytesIO(b"stale"), TRANSFER_LIMIT,
                path=folder + "/edit.txt", version=current["version"],
            )
        assert error.value.status_code == 409

        link = folder + "/passwd-link"
        run(
            build_docker_argv(
                f"docker exec {shlex.quote(container)} ln -s /etc/passwd {shlex.quote(link)}",
                env=env,
            ),
            env=env,
        )
        try:
            listed = operation(target, "list", env, path=folder)["entries"]
            assert "passwd-link" not in [entry["name"] for entry in listed]
            with pytest.raises(HTTPException) as error:
                operation(target, "read", env, path=link)
            assert error.value.status_code == 400
        finally:
            run(
                build_docker_argv(
                    f"docker exec {shlex.quote(container)} rm -f {shlex.quote(link)}",
                    env=env,
                ),
                env=env,
            )
    finally:
        operation(target, "delete", env, path=folder)
