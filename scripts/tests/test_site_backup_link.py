"""Tests offline del enlace desechable de A.SPEC 0048."""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import site_backup_link as link


def backup_zip(tmp_path: Path, *, kind: str = "static") -> Path:
    path = tmp_path / f"{kind}-sitio-20261107-040000.zip"
    files = {"site/html/index.html": b"<h1>respaldo</h1>"}
    if kind == "wp":
        files["database/backup.sql"] = b"CREATE TABLE wp_posts (id int);\n"
    entries = {
        name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        for name, data in files.items()
    }
    manifest = {
        "kind": kind,
        "container": "sitio",
        "timestamp": "20261107-040000",
        "db_host": "db:mariadb" if kind == "wp" else None,
        "entries": entries,
        "total_size": sum(len(data) for data in files.values()),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, data in files.items():
            zipped.writestr(name, data)
        zipped.writestr("manifest.json", json.dumps(manifest))
    return path


def running_server(zip_path: Path, ttl: float = 5.0):
    token = "t" * 43
    state = link.DownloadState(
        zip_path=zip_path,
        token_digest=link.DownloadState.digest(token),
        expires_at=time.monotonic() + ttl,
    )
    server = link.OneTimeServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=link.serve, args=(server,))
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/download/{token}"
    return state, thread, url


def test_head_does_not_consume_and_get_downloads_exact_zip(tmp_path: Path):
    path = backup_zip(tmp_path)
    expected = path.read_bytes()
    state, thread, url = running_server(path)

    head = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=2)
    assert head.status == 200
    assert int(head.headers["Content-Length"]) == len(expected)
    assert head.headers["Cache-Control"] == "no-store"
    assert not state.claimed

    response = urllib.request.urlopen(url, timeout=2)
    assert response.status == 200
    assert response.headers["Content-Type"] == "application/zip"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.read() == expected
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert state.claimed and state.completed


def test_wrong_token_is_404_and_does_not_consume(tmp_path: Path):
    path = backup_zip(tmp_path)
    state, thread, url = running_server(path)
    wrong = url.rsplit("/", 1)[0] + "/incorrecto"
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(wrong, timeout=2)
    assert caught.value.code == 404
    assert not state.claimed

    assert urllib.request.urlopen(url, timeout=2).read() == path.read_bytes()
    thread.join(timeout=2)


def test_claim_is_atomic_under_concurrency(tmp_path: Path):
    path = backup_zip(tmp_path)
    token = "token-concurrente"
    state = link.DownloadState(
        zip_path=path,
        token_digest=link.DownloadState.digest(token),
        expires_at=time.monotonic() + 10,
    )
    barrier = threading.Barrier(16)
    results: list[str] = []
    result_lock = threading.Lock()

    def claim() -> None:
        barrier.wait()
        result = state.claim(token)
        with result_lock:
            results.append(result)

    workers = [threading.Thread(target=claim) for _ in range(16)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert results.count("active") == 1
    assert results.count("consumed") == 15


def test_expired_token_cannot_be_claimed(tmp_path: Path):
    path = backup_zip(tmp_path)
    clock = lambda: 20.0
    token = "expirado"
    state = link.DownloadState(
        zip_path=path,
        token_digest=link.DownloadState.digest(token),
        expires_at=19.0,
        now=clock,
    )
    assert state.status(token) == "expired"
    assert state.claim(token) == "expired"
    assert not state.claimed


def test_invalid_or_tampered_zip_is_rejected_before_listen(tmp_path: Path, capsys):
    path = backup_zip(tmp_path)
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(broken, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            target.writestr(name, b"alterado" if name.startswith("site/") else data)

    assert link.main(["--zip", str(broken), "--port", "0", "--ttl", "1"]) == link.EXIT_ERROR
    assert "invalid ZIP" in capsys.readouterr().err


@pytest.mark.parametrize("ttl", ["0", "3601"])
def test_ttl_bounds(tmp_path: Path, ttl: str, capsys):
    path = backup_zip(tmp_path)
    assert link.main(["--zip", str(path), "--ttl", ttl]) == link.EXIT_ERROR
    assert "--ttl" in capsys.readouterr().err


def test_non_local_bind_requires_explicit_public_origin(tmp_path: Path, capsys):
    path = backup_zip(tmp_path)
    assert link.main(["--zip", str(path), "--bind", "0.0.0.0", "--port", "0"]) == 1
    assert "--public-base-url" in capsys.readouterr().err


def test_public_origin_rejects_paths_or_credentials():
    with pytest.raises(link.LinkError):
        link._public_base(
            "https://user:pass@example.com/x", "0.0.0.0", 80, tls_enabled=True
        )


def test_public_plain_http_requires_explicit_insecure_opt_in():
    with pytest.raises(link.LinkError, match="Public HTTP"):
        link._public_base("http://example.com:8765", "0.0.0.0", 8765)
    assert (
        link._public_base(
            "http://example.com:8765",
            "0.0.0.0",
            8765,
            allow_insecure_http=True,
        )
        == "http://example.com:8765"
    )


def test_public_https_requires_tls_server_mode():
    with pytest.raises(link.LinkError, match="origin http"):
        link._public_base("https://example.com", "0.0.0.0", 443)
    assert (
        link._public_base(
            "https://example.com", "0.0.0.0", 443, tls_enabled=True
        )
        == "https://example.com"
    )
