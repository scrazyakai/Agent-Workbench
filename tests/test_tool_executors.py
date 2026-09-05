from types import SimpleNamespace

import httpx
import httpx2
import pytest
from mcp.server.mcpserver import MCPServer

from app.schemas.tools import HttpToolConfig, McpToolConfig
from app.services.tools import HttpToolExecutor, McpToolExecutor, ToolExecutionError


class AllowTarget:
    async def validate(self, url, allowed_hosts):
        return None


@pytest.mark.anyio
async def test_http_executor_maps_arguments_auth_and_json_response():
    async def handler(request):
        assert request.url == "https://api.example.com/users/a%20b?q=hello"
        assert request.headers["Authorization"] == "Bearer PRIVATE"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    executor = HttpToolExecutor(
        lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        target_policy=AllowTarget(),
    )
    config = HttpToolConfig(
        method="GET",
        endpoint="https://api.example.com/users/{user}",
        allowed_hosts=["api.example.com"],
        path_params={"user": "user"},
        query_params={"q": "query"},
        body_mode="none",
        auth={"type": "bearer"},
    )
    assert await executor.execute(config, {"user": "a b", "query": "hello"}, "PRIVATE") == {
        "ok": True
    }


@pytest.mark.anyio
async def test_http_executor_normalizes_upstream_failures():
    transport = httpx.MockTransport(lambda _: httpx.Response(500, text="PRIVATE"))
    executor = HttpToolExecutor(
        lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        target_policy=AllowTarget(),
    )
    config = HttpToolConfig(
        method="GET",
        endpoint="https://api.example.com/fail",
        allowed_hosts=["api.example.com"],
        body_mode="none",
    )
    with pytest.raises(ToolExecutionError, match="HTTP 500") as error:
        await executor.execute(config, {}, None)
    assert error.value.code == "tool_upstream_rejected"
    assert "PRIVATE" not in error.value.safe_message


@pytest.mark.anyio
async def test_http_executor_retries_safe_get_with_stable_request():
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts == 1 else 200, json={"ok": True})

    executor = HttpToolExecutor(
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs),
        target_policy=AllowTarget(),
    )
    config = HttpToolConfig(
        method="GET",
        endpoint="https://api.example.com/retry",
        allowed_hosts=["api.example.com"],
        body_mode="none",
        retry={"max_attempts": 2, "backoff_seconds": 0, "retry_statuses": [503]},
    )
    assert await executor.execute(config, {}, None) == {"ok": True}
    assert attempts == 2


class FakeMcpClient:
    def __init__(self, transport, **kwargs):
        self.transport = transport

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def list_tools(self, *, cursor=None):
        assert cursor is None
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="remote_search",
                    description="Search",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                )
            ],
            next_cursor=None,
        )

    async def call_tool(self, name, arguments, **kwargs):
        assert name == "remote_search"
        return SimpleNamespace(
            is_error=False,
            structured_content={"result": arguments["q"]},
            content=[],
        )


class PaginatedMcpClient(FakeMcpClient):
    async def list_tools(self, *, cursor=None):
        if cursor is None:
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="first_tool",
                        description="First page",
                        input_schema={"type": "object"},
                        output_schema=None,
                    )
                ],
                next_cursor="page-2",
            )
        assert cursor == "page-2"
        return await super().list_tools(cursor=None)


@pytest.mark.anyio
async def test_mcp_executor_discovers_and_calls_remote_tool():
    executor = McpToolExecutor(target_policy=AllowTarget(), client_factory=FakeMcpClient)
    config = McpToolConfig(
        server_url="https://mcp.example.com/mcp",
        remote_tool_name="remote_search",
        allowed_hosts=["mcp.example.com"],
        auth={"type": "bearer"},
    )
    discovered = await executor.discover(config, "PRIVATE")
    assert discovered[0]["name"] == "remote_search"
    assert await executor.execute(config, {"q": "hello"}, "PRIVATE") == {"result": "hello"}


@pytest.mark.anyio
async def test_mcp_executor_follows_tool_list_pagination():
    executor = McpToolExecutor(
        target_policy=AllowTarget(),
        client_factory=PaginatedMcpClient,
    )
    config = McpToolConfig(
        server_url="https://mcp.example.com/mcp/",
        remote_tool_name="remote_search",
        allowed_hosts=["mcp.example.com"],
    )

    discovered = await executor.discover(config, None)

    assert [tool["name"] for tool in discovered] == ["first_tool", "remote_search"]
    assert await executor.execute(config, {"q": "page two"}, None) == {"result": "page two"}


@pytest.mark.anyio
async def test_mcp_executor_negotiates_real_streamable_http_protocol():
    server = MCPServer("test-server")

    @server.tool()
    def add(a: int, b: int) -> dict:
        return {"total": a + b}

    app = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        host="mcp.example.com",
    )

    def client_factory(**kwargs):
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)

    executor = McpToolExecutor(
        target_policy=AllowTarget(),
        http_client_factory=client_factory,
    )
    config = McpToolConfig(
        server_url="https://mcp.example.com/mcp",
        remote_tool_name="add",
        allowed_hosts=["mcp.example.com"],
    )
    async with app.router.lifespan_context(app):
        discovered = await executor.discover(config, None)
        assert discovered[0]["name"] == "add"
        assert await executor.execute(config, {"a": 2, "b": 3}, None) == {"total": 5}
