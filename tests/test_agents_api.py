from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient


def create(client, name="demo", tags=None):
    connection = client.post(
        "/v1/model-connections",
        json={
            "name": f"connection-{uuid4()}",
            "model_name": "gpt-test",
            "base_url": "https://models.example/v1",
            "api_key": "MODEL_TEST_PRIVATE",
        },
    )
    assert connection.status_code == 201, connection.text
    response = client.post(
        "/v1/agents",
        json={
            "name": name,
            "tags": tags or [],
            "system_prompt": "Analyze data",
            "model_config": {"connection_id": connection.json()["id"]},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_versions_and_snapshot(client):
    path = f"/v1/agents/{create(client)}"
    first = client.post(f"{path}/versions")
    assert first.status_code == 201
    assert first.json()["version"] == 1
    assert client.patch(path, json={"system_prompt": "changed"}).status_code == 200
    assert client.post(f"{path}/versions").json()["version"] == 2
    assert client.get(f"{path}/versions/1").json() == first.json()
    assert client.get(path).json()["latest_version"] == 2
    assert client.get(f"{path}/versions").json()["total"] == 2
    assert client.patch(f"{path}/versions/1", json={}).status_code == 405
    assert client.delete(f"{path}/versions/1").status_code == 405
    snapshot = first.json()["snapshot"]
    assert set(snapshot["model_config"]) == {
        "connection_id",
        "temperature",
        "max_output_tokens",
        "timeout_seconds",
    }
    assert "credential" not in str(snapshot).lower()


def test_filters_and_pagination(client):
    create(client, "alpha", ["ops", "运营"])
    create(client, "beta", ["ops-team"])
    create(client, "gamma", ["ops"])
    create(client, "delta")
    result = client.get("/v1/agents", params={"tag": "ops", "offset": 1, "limit": 1}).json()
    assert result["total"] == 2
    assert [row["name"] for row in result["items"]] == ["gamma"]
    assert client.get("/v1/agents", params={"tag": "运营"}).json()["total"] == 1
    assert client.get("/v1/agents", params={"tag": "ops", "name": "alpha"}).json()["total"] == 1
    assert client.get("/v1/agents", params={"tag": "OPS"}).json()["total"] == 0
    assert client.get("/v1/agents", params={"tag": "ops", "offset": 99}).json()["items"] == []
    assert client.get("/v1/agents", params={"tag": " "}).status_code == 422
    assert client.get("/v1/agents?limit=0").status_code == 422


def test_literal_tag_matching(client):
    tag = 'tag%_"\\'
    create(client, "special", [tag])
    create(client, "normal", ["tag-other"])
    assert client.get("/v1/agents", params={"tag": tag}).json()["total"] == 1


def test_validation_and_rollback(client):
    path = f"/v1/agents/{create(client)}"
    before = client.get(path).json()
    assert client.patch(path, json={"input_schema": {"type": "invalid"}}).status_code == 422
    assert client.get(path).json() == before
    assert client.post("/v1/agents", json={"name": "demo"}).status_code == 409
    assert client.patch(path, json={"name": None}).status_code == 422
    assert client.post("/v1/agents", json={}).status_code == 422


def test_publish_requirements(client):
    response = client.post("/v1/agents", json={"name": "unfinished"})
    path = f"/v1/agents/{response.json()['id']}"
    result = client.post(f"{path}/versions")
    assert result.status_code == 422
    assert len(result.json()["error"]["details"]) == 2
    assert client.get(path).json()["latest_version"] is None


def test_publish_requires_existing_enabled_connection(client):
    legacy = client.post(
        "/v1/agents",
        json={
            "name": "legacy-reference",
            "system_prompt": "test",
            "model_config": {"connection_id": "model-test"},
        },
    ).json()
    result = client.post(f"/v1/agents/{legacy['id']}/versions")
    assert result.status_code == 422
    assert result.json()["error"]["details"] == [
        {
            "field": "model_config.connection_id",
            "message": "Model connection does not exist",
        }
    ]

    connection = client.post(
        "/v1/model-connections",
        json={
            "name": "disabled",
            "model_name": "gpt-test",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "LOCAL_MODEL_PRIVATE",
            "enabled": False,
        },
    ).json()
    disabled = client.post(
        "/v1/agents",
        json={
            "name": "disabled-reference",
            "system_prompt": "test",
            "model_config": {"connection_id": connection["id"]},
        },
    ).json()
    result = client.post(f"/v1/agents/{disabled['id']}/versions")
    assert result.status_code == 422
    assert result.json()["error"]["details"][0]["message"] == "Model connection is disabled"


def test_missing(client):
    path = f"/v1/agents/{uuid4()}"
    assert client.get(path).status_code == 404
    assert client.patch(path, json={"name": "missing"}).status_code == 404
    assert client.post(f"{path}/versions").status_code == 404
    assert client.get(f"/v1/agents/{create(client)}/versions/99").status_code == 404


def test_concurrent_publish(application, client):
    path = f"/v1/agents/{create(client)}/versions"

    def publish(_):
        with TestClient(application) as peer:
            response = peer.post(path)
            assert response.status_code == 201
            return response.json()["version"]

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sorted(executor.map(publish, range(4))) == [1, 2, 3, 4]


def test_workspace_scope(application, client):
    path = f"/v1/agents/{create(client, tags=['ops'])}"
    original = application.state.settings.workspace_id
    try:
        application.state.settings.workspace_id = str(uuid4())
        assert client.get(path).status_code == 404
        assert client.get("/v1/agents?tag=ops").json()["total"] == 0
    finally:
        application.state.settings.workspace_id = original


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}
