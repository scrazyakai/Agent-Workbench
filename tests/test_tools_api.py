from uuid import uuid4

from app.db.models import Tool


class FakeToolRuntime:
    def __init__(self):
        self.calls = []

    async def execute(self, definition, arguments, credential):
        self.calls.append((definition, arguments, credential))
        return {"echo": arguments, "source": definition.tool_type, "upstream_echo": credential}

    async def discover_mcp(self, definition, credential):
        self.calls.append((definition, {}, credential))
        return [
            {
                "name": "remote_search",
                "description": "Search remotely",
                "input_schema": {"type": "object"},
                "output_schema": None,
            }
        ]


def http_payload(name="search", **overrides):
    value = {
        "name": name,
        "description": "Search an external service",
        "owner": "platform",
        "tags": ["search"],
        "tool_type": "http",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "output_schema": {"type": "object"},
        "config": {
            "method": "GET",
            "endpoint": "https://api.example.com/search",
            "allowed_hosts": ["api.example.com"],
            "query_params": {"q": "query"},
            "body_mode": "none",
            "auth": {"type": "bearer"},
        },
        "risk_level": "read",
        "requires_approval": False,
        "enabled": True,
        "credential": "TOOL_PRIVATE",
    }
    value.update(overrides)
    return value


def mcp_payload(name="mcp-search", **overrides):
    value = {
        "name": name,
        "description": "Remote MCP search",
        "owner": "platform",
        "tool_type": "mcp",
        "input_schema": {"type": "object"},
        "config": {
            "transport": "streamable_http",
            "server_url": "https://mcp.example.com/mcp",
            "remote_tool_name": "remote_search",
            "allowed_hosts": ["mcp.example.com"],
            "auth": {"type": "header", "header_name": "X-API-Key"},
        },
        "credential": "MCP_PRIVATE",
    }
    value.update(overrides)
    return value


def create(client, payload):
    response = client.post("/v1/tools", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_http_tool_crud_publish_and_secret_safety(application, client):
    runtime = FakeToolRuntime()
    application.state.tool_runtime = runtime
    tool = create(client, http_payload())
    assert tool["credential_configured"] is True
    assert tool["latest_version"] is None
    assert "credential" not in tool

    with application.state.session_factory() as session:
        stored = session.get(Tool, tool["id"])
        assert stored.credential_ciphertext.startswith("v1:")
        assert "TOOL_PRIVATE" not in stored.credential_ciphertext

    tested = client.post(f"/v1/tools/{tool['id']}/test", json={"arguments": {"query": "x"}})
    assert tested.status_code == 200
    assert tested.json()["success"] is True
    assert tested.json()["output"]["source"] == "http"
    assert runtime.calls[-1][2] == "TOOL_PRIVATE"
    assert "TOOL_PRIVATE" not in tested.text
    assert tested.json()["output"]["upstream_echo"] == "[REDACTED]"

    version = client.post(f"/v1/tools/{tool['id']}/versions")
    assert version.status_code == 201
    assert version.json()["version"] == 1
    assert "credential" not in version.text
    updated = client.patch(f"/v1/tools/{tool['id']}", json={"description": "changed"})
    assert updated.status_code == 200
    assert client.get(f"/v1/tools/{tool['id']}/versions/1").json() == version.json()


def test_tool_filters_validation_and_missing(client):
    create(client, http_payload("alpha"))
    create(client, mcp_payload("beta"))
    assert client.get("/v1/tools", params={"tool_type": "http"}).json()["total"] == 1
    assert client.get("/v1/tools", params={"name": "a"}).json()["total"] == 2
    assert client.get("/v1/tools", params={"enabled": False}).json()["total"] == 0
    assert client.get(f"/v1/tools/{uuid4()}").status_code == 404
    assert client.post("/v1/tools", json=http_payload("alpha")).status_code == 409

    invalid = http_payload(
        "private",
        config={
            "method": "GET",
            "endpoint": "https://other.example/search",
            "allowed_hosts": ["api.example.com"],
            "body_mode": "none",
        },
    )
    assert client.post("/v1/tools", json=invalid).status_code == 422
    high_risk = http_payload("danger", risk_level="high", requires_approval=False)
    assert client.post("/v1/tools", json=high_risk).status_code == 422


def test_input_and_output_schema_are_enforced(application, client):
    application.state.tool_runtime = FakeToolRuntime()
    tool = create(client, http_payload())
    invalid = client.post(f"/v1/tools/{tool['id']}/test", json={"arguments": {}})
    assert invalid.json()["code"] == "tool_input_invalid"
    assert invalid.json()["success"] is False


def test_mcp_discovery_test_and_publish(application, client):
    runtime = FakeToolRuntime()
    application.state.tool_runtime = runtime
    tool = create(client, mcp_payload())
    discovery = client.post(f"/v1/tools/{tool['id']}/discover")
    assert discovery.status_code == 200
    assert discovery.json()["tools"][0]["name"] == "remote_search"
    tested = client.post(f"/v1/tools/{tool['id']}/test", json={"arguments": {"q": "x"}})
    assert tested.json()["success"] is True
    assert tested.json()["output"]["source"] == "mcp"
    assert client.post(f"/v1/tools/{tool['id']}/versions").status_code == 201


def test_authenticated_tool_requires_credential_to_publish(client):
    data = http_payload("no-secret")
    data.pop("credential")
    tool = create(client, data)
    response = client.post(f"/v1/tools/{tool['id']}/versions")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "publish_validation_failed"


def test_agent_publish_validates_bound_tool_version(client):
    connection = client.post(
        "/v1/model-connections",
        json={
            "name": "tool-agent-model",
            "model_name": "gpt-test",
            "base_url": "https://models.example/v1",
            "api_key": "PRIVATE",
        },
    ).json()
    tool = create(client, http_payload("agent-search"))
    agent = client.post(
        "/v1/agents",
        json={
            "name": "tool-agent",
            "system_prompt": "Use tools",
            "model_config": {"connection_id": connection["id"]},
            "tool_bindings": [{"tool_id": tool["id"], "version": 1}],
        },
    ).json()
    missing = client.post(f"/v1/agents/{agent['id']}/versions")
    assert missing.status_code == 422
    assert missing.json()["error"]["details"][-1]["message"] == "Tool version does not exist"

    client.post(f"/v1/tools/{tool['id']}/versions")
    assert client.post(f"/v1/agents/{agent['id']}/versions").status_code == 201
    client.patch(f"/v1/tools/{tool['id']}", json={"enabled": False})
    assert client.post(f"/v1/agents/{agent['id']}/versions").status_code == 422
