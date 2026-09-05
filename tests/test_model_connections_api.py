import json
from uuid import uuid4

import httpx
from sqlalchemy import select

from app.db.models import ModelConnection
from app.services.model_connections import OpenAICompatibleConnectionTester


def payload(name="primary", **overrides):
    value = {
        "name": name,
        "provider": "openai_compatible",
        "model_name": "gpt-test",
        "base_url": "https://models.example/v1/",
        "api_key": "PRIVATE",
        "timeout_seconds": 5,
        "enabled": True,
    }
    value.update(overrides)
    return value


def create(client, name="primary", **overrides):
    response = client.post("/v1/model-connections", json=payload(name, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_crud_filters_and_no_delete(client):
    first = create(client)
    create(client, "secondary", enabled=False)
    assert first["base_url"] == "https://models.example/v1"
    assert first["credential_configured"] is True
    assert "api_key" not in first
    assert "credential_ciphertext" not in first
    assert client.get(f"/v1/model-connections/{first['id']}").json() == first
    assert client.get("/v1/model-connections", params={"enabled": True}).json()["total"] == 1
    assert client.get("/v1/model-connections", params={"name": "ary"}).json()["total"] == 2
    updated = client.patch(
        f"/v1/model-connections/{first['id']}",
        json={"enabled": False, "model_name": "gpt-next"},
    )
    assert updated.status_code == 200
    assert not updated.json()["enabled"]
    assert updated.json()["model_name"] == "gpt-next"
    assert client.delete(f"/v1/model-connections/{first['id']}").status_code == 405


def test_validation_conflict_and_missing(client):
    create(client)
    assert client.post("/v1/model-connections", json=payload()).status_code == 409
    assert client.get(f"/v1/model-connections/{uuid4()}").status_code == 404
    for extra in ({"credential_ref": "env://MODEL_TEST_KEY"}, {"headers": {"X-Key": "PRIVATE"}}):
        assert client.post("/v1/model-connections", json=payload(**extra)).status_code == 422
    for api_key in ("", "   "):
        assert (
            client.post(
                "/v1/model-connections",
                json=payload(name=str(uuid4()), api_key=api_key),
            ).status_code
            == 422
        )
    for base_url in (
        "ftp://models.example",
        "https://user:PRIVATE@models.example/v1",
        "https://models.example/v1?key=PRIVATE",
        "https://models.example/v1#PRIVATE",
    ):
        assert (
            client.post(
                "/v1/model-connections",
                json=payload(name=str(uuid4()), base_url=base_url),
            ).status_code
            == 422
        )


def test_workspace_scope(application, client):
    connection = create(client)
    original = application.state.settings.workspace_id
    try:
        application.state.settings.workspace_id = str(uuid4())
        assert client.get(f"/v1/model-connections/{connection['id']}").status_code == 404
        assert client.get("/v1/model-connections").json()["total"] == 0
    finally:
        application.state.settings.workspace_id = original


def make_tester(handler, cipher):
    transport = httpx.MockTransport(handler)
    return OpenAICompatibleConnectionTester(
        lambda **kwargs: httpx.Client(transport=transport, **kwargs),
        cipher=cipher,
    )


def test_connection_success_and_disabled_allowed(client):
    connection = create(client, enabled=False)

    def handler(request):
        assert request.url == "https://models.example/v1/models"
        assert request.headers["Authorization"] == "Bearer PRIVATE"
        return httpx.Response(200, json={"data": [{"id": "gpt-test"}]})

    client.app.state.model_connection_tester = make_tester(
        handler, client.app.state.credential_cipher
    )
    result = client.post(f"/v1/model-connections/{connection['id']}/test")
    assert result.status_code == 200
    assert result.json()["success"] is True
    assert result.json()["code"] == "ok"
    assert "PRIVATE" not in result.text


def test_connection_expected_failures_are_safe(client):
    connection = create(client)
    path = f"/v1/model-connections/{connection['id']}/test"
    cases = [
        (lambda _: httpx.Response(401, text="PRIVATE"), "authentication_failed"),
        (lambda _: httpx.Response(302, headers={"Location": "http://PRIVATE"}), "invalid_response"),
        (lambda _: httpx.Response(200, content=b"not-json PRIVATE"), "invalid_response"),
        (lambda _: httpx.Response(200, json={"data": [{"id": "other"}]}), "model_not_found"),
    ]
    for handler, code in cases:
        client.app.state.model_connection_tester = make_tester(
            handler, client.app.state.credential_cipher
        )
        response = client.post(path)
        assert response.status_code == 200
        assert response.json()["code"] == code
        assert "PRIVATE" not in response.text

    def timeout(_):
        raise httpx.ConnectTimeout("PRIVATE")

    client.app.state.model_connection_tester = make_tester(
        timeout, client.app.state.credential_cipher
    )
    response = client.post(path)
    assert response.status_code == 200
    assert response.json()["code"] == "provider_unreachable"
    assert "PRIVATE" not in response.text


def test_test_result_is_not_persisted(client):
    connection = create(client)
    client.app.state.model_connection_tester = make_tester(
        lambda _: httpx.Response(200, json={"data": [{"id": "gpt-test"}]}),
        client.app.state.credential_cipher,
    )
    assert client.post(f"/v1/model-connections/{connection['id']}/test").json()["success"]
    stored = client.get(f"/v1/model-connections/{connection['id']}").json()
    assert "last_test" not in stored
    assert "test_result" not in stored
    assert "PRIVATE" not in json.dumps(stored)


def test_api_key_is_encrypted_and_patch_without_key_preserves_it(application, client):
    connection = create(client)
    with application.state.session_factory() as session:
        stored = session.scalar(
            select(ModelConnection).where(ModelConnection.id == connection["id"])
        )
        original_ciphertext = stored.credential_ciphertext
        assert original_ciphertext.startswith("v1:")
        assert "PRIVATE" not in original_ciphertext
        assert stored.credential_ref is None

    response = client.patch(
        f"/v1/model-connections/{connection['id']}", json={"model_name": "gpt-next"}
    )
    assert response.status_code == 200
    with application.state.session_factory() as session:
        stored = session.get(ModelConnection, connection["id"])
        assert stored.credential_ciphertext == original_ciphertext


def test_patch_api_key_rotates_ciphertext(application, client):
    connection = create(client)
    with application.state.session_factory() as session:
        before = session.get(ModelConnection, connection["id"]).credential_ciphertext
    response = client.patch(
        f"/v1/model-connections/{connection['id']}", json={"api_key": "REPLACEMENT"}
    )
    assert response.status_code == 200
    assert "REPLACEMENT" not in response.text
    with application.state.session_factory() as session:
        after = session.get(ModelConnection, connection["id"]).credential_ciphertext
        assert after != before
        assert "REPLACEMENT" not in after
