import base64
import binascii
import os
from dataclasses import dataclass
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialCipherUnavailable(Exception):
    pass


class CredentialDecryptionError(Exception):
    pass


@dataclass(frozen=True)
class CredentialCipher:
    """Encrypt model credentials without ever serializing the master key."""

    _cipher: AESGCM | None

    @classmethod
    def from_base64_key(cls, encoded_key: str | None) -> "CredentialCipher":
        if not encoded_key:
            return cls(None)
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                "WORKBENCH_CREDENTIAL_ENCRYPTION_KEY must be a base64-encoded 32-byte key"
            ) from exc
        if len(key) != 32:
            raise ValueError(
                "WORKBENCH_CREDENTIAL_ENCRYPTION_KEY must be a base64-encoded 32-byte key"
            )
        return cls(AESGCM(key))

    @staticmethod
    def _aad(connection_id: UUID) -> bytes:
        return f"ai-workbench:model-connection:{connection_id}:v1".encode()

    def encrypt(self, connection_id: UUID, secret: str) -> str:
        if self._cipher is None:
            raise CredentialCipherUnavailable
        nonce = os.urandom(12)
        encrypted = self._cipher.encrypt(nonce, secret.encode(), self._aad(connection_id))
        return "v1:" + base64.b64encode(nonce + encrypted).decode()

    def decrypt(self, connection_id: UUID, encrypted_value: str) -> str:
        if self._cipher is None:
            raise CredentialCipherUnavailable
        try:
            version, payload = encrypted_value.split(":", 1)
            raw = base64.b64decode(payload, validate=True)
            if version != "v1" or len(raw) < 29:
                raise ValueError
            return self._cipher.decrypt(raw[:12], raw[12:], self._aad(connection_id)).decode()
        except (binascii.Error, InvalidTag, UnicodeDecodeError, ValueError) as exc:
            raise CredentialDecryptionError from exc
