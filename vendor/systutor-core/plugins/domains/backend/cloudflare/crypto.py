"""AES-256-GCM envelopes bound to purpose and tenant/flow identity.

Nonces are always random.  The AAD includes the purpose, the identity (tenant,
flow or platform client) and the envelope key version, so a ciphertext copied
to another row or another purpose cannot be decrypted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from plugins.domains.backend.cloudflare.config import CloudflareConfig


class CloudflareCryptoError(ValueError):
    """Decryption or envelope validation failed. The message has no secrets."""


@dataclass(frozen=True)
class EncryptedValue:
    ciphertext: bytes
    nonce: bytes
    key_version: int


class CloudflareCipher:
    def __init__(self, key: bytes, key_version: int = 1) -> None:
        if len(key) != 32:
            raise CloudflareCryptoError(
                "the Cloudflare encryption key must contain exactly 32 bytes"
            )
        if key_version < 1:
            raise CloudflareCryptoError("the Cloudflare key version must be positive")
        self._cipher = AESGCM(key)
        self.key_version = key_version

    @classmethod
    def from_config(cls, config: CloudflareConfig) -> CloudflareCipher:
        return cls(config.load_key(), config.key_version)

    @staticmethod
    def scoped_aad(purpose: str, identity: str, version: int) -> bytes:
        if not purpose or not identity or "\x00" in purpose or "\x00" in identity:
            raise CloudflareCryptoError("the encryption scope is invalid")
        return f"{purpose}\0{identity}\0{version}".encode()

    def encrypt(self, purpose: str, identity: str, plaintext: str) -> EncryptedValue:
        if not plaintext:
            raise CloudflareCryptoError("refusing to encrypt an empty secret")
        nonce = os.urandom(12)
        aad = self.scoped_aad(purpose, identity, self.key_version)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode(), aad)
        return EncryptedValue(ciphertext, nonce, self.key_version)

    def decrypt(self, purpose: str, identity: str, value: EncryptedValue) -> str:
        aad = self.scoped_aad(purpose, identity, value.key_version)
        try:
            plaintext = self._cipher.decrypt(value.nonce, value.ciphertext, aad)
        except (InvalidTag, ValueError) as exc:
            raise CloudflareCryptoError("the encrypted secret cannot be decrypted") from exc
        return plaintext.decode()
