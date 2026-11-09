"""AES-256-GCM token envelope with identity-bound AAD."""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class EncryptedValue:
    ciphertext: bytes
    nonce: bytes
    key_version: int


class TokenCipher:
    def __init__(self, key: bytes, key_version: int = 1) -> None:
        if len(key) != 32:
            raise ValueError("Apps encryption key must contain exactly 32 bytes")
        self._cipher = AESGCM(key)
        self.key_version = key_version

    @classmethod
    def from_text(cls, value: str, key_version: int = 1) -> TokenCipher:
        try:
            key = base64.urlsafe_b64decode(value.encode())
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Apps encryption key is invalid") from exc
        return cls(key, key_version)

    @staticmethod
    def _aad(user_id: str, version: int) -> bytes:
        return f"github\0{user_id}\0{version}".encode()

    @staticmethod
    def scoped_aad(
        purpose: str, tenant_id: str, provider_id: str, version: int
    ) -> bytes:
        return f"{purpose}\0{tenant_id}\0{provider_id}\0{version}".encode()

    def encrypt(self, user_id: str, plaintext: str) -> EncryptedValue:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(
            nonce, plaintext.encode(), self._aad(user_id, self.key_version)
        )
        return EncryptedValue(ciphertext, nonce, self.key_version)

    def decrypt(self, user_id: str, value: EncryptedValue) -> str:
        plaintext = self._cipher.decrypt(
            value.nonce, value.ciphertext, self._aad(user_id, value.key_version)
        )
        return plaintext.decode()

    def encrypt_scoped(
        self, purpose: str, tenant_id: str, provider_id: str, plaintext: str
    ) -> EncryptedValue:
        nonce = os.urandom(12)
        aad = self.scoped_aad(purpose, tenant_id, provider_id, self.key_version)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode(), aad)
        return EncryptedValue(ciphertext, nonce, self.key_version)

    def decrypt_scoped(
        self,
        purpose: str,
        tenant_id: str,
        provider_id: str,
        value: EncryptedValue,
    ) -> str:
        aad = self.scoped_aad(purpose, tenant_id, provider_id, value.key_version)
        plaintext = self._cipher.decrypt(value.nonce, value.ciphertext, aad)
        return plaintext.decode()
