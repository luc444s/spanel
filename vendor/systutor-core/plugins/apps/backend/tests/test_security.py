from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag

from plugins.apps.backend.crypto import EncryptedValue, TokenCipher


def test_cipher_is_purpose_tenant_provider_and_version_bound() -> None:
    cipher = TokenCipher(b"x" * 32)
    encrypted = cipher.encrypt_scoped("github-app-private-key", "t1", "p1", "pem-canary")
    assert b"pem-canary" not in encrypted.ciphertext
    assert cipher.decrypt_scoped(
        "github-app-private-key", "t1", "p1", encrypted
    ) == "pem-canary"
    for args in [
        ("other-purpose", "t1", "p1"),
        ("github-app-private-key", "t2", "p1"),
        ("github-app-private-key", "t1", "p2"),
    ]:
        with pytest.raises(InvalidTag):
            cipher.decrypt_scoped(*args, encrypted)
    with pytest.raises(InvalidTag):
        cipher.decrypt_scoped(
            "github-app-private-key", "t1", "p1",
            EncryptedValue(encrypted.ciphertext, encrypted.nonce, 2),
        )


def test_cipher_rejects_tampering() -> None:
    cipher = TokenCipher(b"x" * 32)
    encrypted = cipher.encrypt_scoped("github-app-private-key", "t1", "p1", "pem")
    tampered = EncryptedValue(encrypted.ciphertext[:-1] + b"x", encrypted.nonce, 1)
    with pytest.raises(InvalidTag):
        cipher.decrypt_scoped("github-app-private-key", "t1", "p1", tampered)


def test_cipher_accepts_only_aes_256_key() -> None:
    text = base64.urlsafe_b64encode(b"x" * 32).decode()
    assert TokenCipher.from_text(text).key_version == 1
    with pytest.raises(ValueError):
        TokenCipher(b"short")
