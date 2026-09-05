from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.core.credentials import CredentialCipher
from app.services.model_connections import OpenAICompatibleConnectionTester

TEST_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def connection():
    connection_id = uuid4()
    cipher = CredentialCipher.from_base64_key(TEST_KEY)
    return SimpleNamespace(
        id=connection_id,
        base_url="https://models.example/v1",
        credential_ref=None,
        credential_ciphertext=cipher.encrypt(connection_id, "PRIVATE"),
        model_name="gpt-test",
        timeout_seconds=3,
    )


def build_tester(handler, cipher=None):
    transport = httpx.MockTransport(handler)
    return OpenAICompatibleConnectionTester(
        lambda **kwargs: httpx.Client(transport=transport, **kwargs),
        cipher=cipher or CredentialCipher.from_base64_key(TEST_KEY),
    )


def test_tester_success_and_secret_is_request_only():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer PRIVATE"
        return httpx.Response(200, json={"data": [{"id": "gpt-test"}]})

    result = build_tester(handler).test(connection())
    assert result.success
    assert result.code == "ok"
    assert "PRIVATE" not in result.model_dump_json()


@pytest.mark.parametrize(
    ("handler", "code"),
    [
        (lambda _: httpx.Response(403, text="PRIVATE"), "authentication_failed"),
        (lambda _: httpx.Response(307, headers={"Location": "http://PRIVATE"}), "invalid_response"),
        (lambda _: httpx.Response(200, content=b"PRIVATE"), "invalid_response"),
        (
            lambda _: httpx.Response(200, json={"data": [{"id": "another-model"}]}),
            "model_not_found",
        ),
    ],
)
def test_tester_normalizes_provider_failures(handler, code):
    result = build_tester(handler).test(connection())
    assert not result.success
    assert result.code == code
    assert "PRIVATE" not in result.model_dump_json()


def test_tester_handles_missing_secret_and_network_failure():
    missing = connection()
    missing.credential_ciphertext = None
    result = build_tester(lambda _: pytest.fail("request must not be sent")).test(missing)
    assert result.code == "credential_not_found"

    def timeout(_):
        raise httpx.ConnectTimeout("PRIVATE")

    result = build_tester(timeout).test(connection())
    assert result.code == "provider_unreachable"
    assert "PRIVATE" not in result.model_dump_json()


def test_tester_rejects_tampered_ciphertext():
    tampered = connection()
    tampered.credential_ciphertext += "A"
    result = build_tester(lambda _: pytest.fail("request must not be sent")).test(tampered)
    assert result.code == "credential_decryption_failed"


def test_tester_reports_missing_master_key():
    result = build_tester(
        lambda _: pytest.fail("request must not be sent"),
        cipher=CredentialCipher.from_base64_key(None),
    ).test(connection())
    assert result.code == "credential_encryption_unavailable"


def test_tester_supports_legacy_environment_reference(monkeypatch):
    legacy = connection()
    legacy.credential_ciphertext = None
    legacy.credential_ref = "env://LEGACY_MODEL_API_KEY"
    monkeypatch.setenv("LEGACY_MODEL_API_KEY", "LEGACY_PRIVATE")

    def handler(request):
        assert request.headers["Authorization"] == "Bearer LEGACY_PRIVATE"
        return httpx.Response(200, json={"data": [{"id": "gpt-test"}]})

    assert build_tester(handler).test(legacy).success
