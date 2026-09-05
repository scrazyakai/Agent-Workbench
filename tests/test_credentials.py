import base64
from uuid import uuid4

import pytest

from app.core.credentials import (
    CredentialCipher,
    CredentialCipherUnavailable,
    CredentialDecryptionError,
)

KEY = base64.b64encode(b"a" * 32).decode()
OTHER_KEY = base64.b64encode(b"b" * 32).decode()


@pytest.mark.parametrize("value", ["not-base64", base64.b64encode(b"short").decode()])
def test_rejects_invalid_master_key(value):
    with pytest.raises(ValueError, match="base64-encoded 32-byte key"):
        CredentialCipher.from_base64_key(value)


def test_unconfigured_cipher_cannot_encrypt_or_decrypt():
    cipher = CredentialCipher.from_base64_key(None)
    with pytest.raises(CredentialCipherUnavailable):
        cipher.encrypt(uuid4(), "secret")
    with pytest.raises(CredentialCipherUnavailable):
        cipher.decrypt(uuid4(), "v1:invalid")


def test_round_trip_uses_random_nonce_and_connection_bound_aad():
    cipher = CredentialCipher.from_base64_key(KEY)
    connection_id = uuid4()
    first = cipher.encrypt(connection_id, "secret")
    second = cipher.encrypt(connection_id, "secret")

    assert first.startswith("v1:")
    assert first != second
    assert cipher.decrypt(connection_id, first) == "secret"
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(uuid4(), first)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: "v2:" + value.split(":", 1)[1],
        lambda value: value[:-1] + ("A" if value[-1] != "A" else "B"),
        lambda _: "v1:not-base64",
    ],
)
def test_rejects_unsupported_or_tampered_ciphertext(mutator):
    connection_id = uuid4()
    encrypted = CredentialCipher.from_base64_key(KEY).encrypt(connection_id, "secret")
    with pytest.raises(CredentialDecryptionError):
        CredentialCipher.from_base64_key(KEY).decrypt(connection_id, mutator(encrypted))


def test_wrong_master_key_cannot_decrypt():
    connection_id = uuid4()
    encrypted = CredentialCipher.from_base64_key(KEY).encrypt(connection_id, "secret")
    with pytest.raises(CredentialDecryptionError):
        CredentialCipher.from_base64_key(OTHER_KEY).decrypt(connection_id, encrypted)
