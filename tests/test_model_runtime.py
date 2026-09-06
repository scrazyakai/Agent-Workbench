import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier, Event
from uuid import UUID

import httpx
import pytest
from langchain_core.messages import AIMessageChunk
from sqlalchemy import select

from app.db.models import Checkpoint, ModelConnection, Run, utcnow
from app.services.agent_executor import AgentExecutor
from app.services.model_client import LangChainModelClient, ModelReply, StreamRedactor
from app.services.runs import append_event
from app.services.worker import DeterministicRunError, LostLeaseError, RunWorker
from tests.test_runs_api import create_run, published_agent, worker
from tests.test_tools_api import FakeToolRuntime, http_payload


def configured(client, *, limits=None, schema=None, tool=False):
    agent, _ = published_agent(client)
    patch = {}
    bound = None
    if tool:
        bound = client.post("/v1/tools", json=http_payload()).json()
        assert client.post(f"/v1/tools/{bound['id']}/versions").status_code == 201
        patch["tool_bindings"] = [{"tool_id": bound["id"], "version": 1}]
    if limits:
        patch["execution_limits"] = limits
    if schema:
        patch["output_schema"] = schema
    assert client.patch(f"/v1/agents/{agent['id']}", json=patch).status_code == 200
    version = client.post(f"/v1/agents/{agent['id']}/versions").json()
    response = client.post(
        "/v1/runs",
        json={
            "target": {"type": "agent", "id": agent["id"], "version": version["version"]},
            "input": {"question": "hello"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json(), bound


class FakeModel:
    def __init__(self, *messages):
        self.messages = list(messages) or [{"role": "assistant", "content": "完成"}]
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append({**kwargs, "messages": deepcopy(kwargs["messages"])})
        message = self.messages.pop(0)
        if message.get("content"):
            kwargs["emit"](message["content"])
        return ModelReply(
            message, {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
        )


def calling(bound, *, name=None, arguments='{"query":"hello"}'):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": name or AgentExecutor.tool_name({"tool_id": bound["id"], "version": 1}),
                    "arguments": arguments,
                },
            }
        ],
    }


def runtime(application, model, tools=None, name="model-worker"):
    return RunWorker(
        application.state.session_factory,
        application.state.settings,
        name,
        model_client=model,
        tool_runtime=tools,
    )


def read(client, run):
    return client.get(f"/v1/runs/{run['id']}").json()


def expire(application, run):
    with application.state.session_factory() as session, session.begin():
        row = session.get(Run, run["id"])
        row.lease_expires_at = utcnow() - timedelta(seconds=1)


def test_model_run_snapshots_usage_trace_and_stream(application, client):
    run, _ = configured(client)
    model = FakeModel({"role": "assistant", "content": "hello MODEL_TEST_PRIVATE"})
    with application.state.session_factory() as session, session.begin():
        row = session.get(Run, run["id"])
        connection = session.get(ModelConnection, row.config_snapshot["model"]["connection_id"])
        connection.model_name = "changed-after-creation"
    runtime(application, model).run_once()
    result = read(client, run)
    assert result["status"] == "succeeded", result
    assert result["execution_mode"] == "model"
    assert result["usage"]["total_tokens"] == result["usage"]["charged_tokens"] == 30
    assert result["result"]["text"] == "hello [REDACTED]"
    assert model.calls[0]["model"]["model_name"] == "gpt-test"
    steps = client.get(f"/v1/runs/{run['id']}/steps").json()
    assert steps[0]["output_summary"]["usage"]["total_tokens"] == 30
    stream = client.get(f"/v1/runs/{run['id']}/stream")
    assert "event: done" in stream.text
    assert "model_output_delta" in stream.text
    assert "MODEL_TEST_PRIVATE" not in stream.text
    assert "MODEL_TEST_PRIVATE" not in json.dumps(result)
    with application.state.session_factory() as session:
        states = session.scalars(
            select(Checkpoint.state).where(Checkpoint.run_id == UUID(run["id"]))
        ).all()
        assert "MODEL_TEST_PRIVATE" not in json.dumps(states)


def test_tool_loop_and_checkpoint_recovery(application, client):
    run, bound = configured(client, tool=True)
    model = FakeModel(calling(bound), {"role": "assistant", "content": "工具已完成"})
    tools = FakeToolRuntime()
    first = runtime(application, model, tools)
    first.run_once(max_steps=2)  # Model and tool checkpoint committed, final model not started.
    assert len(tools.calls) == 1
    expire(application, run)
    second = runtime(application, model, tools, "replacement")
    second.run_once()
    result = read(client, run)
    assert result["status"] == "succeeded", result
    assert len(model.calls) == 2 and len(tools.calls) == 1
    assert model.calls[-1]["messages"][-1]["role"] == "tool"
    assert "TOOL_PRIVATE" not in model.calls[-1]["messages"][-1]["content"]
    assert result["usage"]["model_calls"] == 2
    assert result["usage"]["tool_calls"] == 1
    assert result["usage"]["charged_tokens"] == 60
    assert result["recovery_count"] == 1


@pytest.mark.parametrize(
    "policy,code",
    [
        ({"enabled": False}, "tool_unavailable"),
        ({"risk_level": "write"}, "tool_requires_approval"),
        ({"requires_approval": True}, "tool_requires_approval"),
    ],
)
def test_policy_rechecked_after_model_checkpoint(application, client, policy, code):
    run, bound = configured(client, tool=True)
    model, tools = FakeModel(calling(bound)), FakeToolRuntime()
    runner = runtime(application, model, tools)
    runner.run_once(max_steps=1)
    assert client.patch(f"/v1/tools/{bound['id']}", json=policy).status_code == 200
    runner.execute_claimed(UUID(run["id"]))
    result = read(client, run)
    assert result["status"] == "failed" and result["error"]["code"] == code, result
    assert not tools.calls


@pytest.mark.parametrize(
    "name,args,code",
    [
        ("unbound", "{}", "unbound_tool"),
        (None, "not JSON", "invalid_tool_arguments"),
        (None, "{}", "invalid_tool_arguments"),
    ],
)
def test_invalid_tool_requests_never_execute(application, client, name, args, code):
    run, bound = configured(client, tool=True)
    tools = FakeToolRuntime()
    runtime(application, FakeModel(calling(bound, name=name, arguments=args)), tools).run_once()
    result = read(client, run)
    assert result["error"]["code"] == code, result
    assert not tools.calls


@pytest.mark.parametrize(
    "limits,code,expected_model_calls,expected_tool_calls",
    [
        ({"token_budget": 1}, "token_budget_exceeded", 0, 0),
        ({"max_steps": 1}, "step_budget_exceeded", 1, 0),
        ({"max_tool_calls": 0}, "tool_budget_exceeded", 1, 0),
    ],
)
def test_budgets(application, client, limits, code, expected_model_calls, expected_tool_calls):
    run, bound = configured(client, limits=limits, tool=True)
    model, tools = FakeModel(calling(bound)), FakeToolRuntime()
    runtime(application, model, tools).run_once()
    result = read(client, run)
    assert result["error"]["code"] == code, result
    assert len(model.calls) == expected_model_calls
    assert len(tools.calls) == expected_tool_calls


@pytest.mark.parametrize(
    "text,status", [('"yes"', "succeeded"), ("{}", "failed"), ("broken", "failed")]
)
def test_output_schema(application, client, text, status):
    run, _ = configured(client, schema={"type": "string"})
    runtime(application, FakeModel({"role": "assistant", "content": text})).run_once()
    assert read(client, run)["status"] == status


class WaitingModel:
    def __init__(self, delay=30):
        self.started = Event()
        self.stopped = Event()
        self.delay = delay

    async def complete(self, **kwargs):
        self.started.set()
        try:
            await asyncio.sleep(self.delay)
            return ModelReply({"role": "assistant", "content": "done"}, {})
        finally:
            self.stopped.set()


@pytest.mark.parametrize("cancel", [True, False])
def test_cancel_and_deadline_interrupt_inflight_model(application, client, cancel):
    run, _ = configured(client, limits={"timeout_seconds": 1 if not cancel else 30})
    model = WaitingModel()
    runner = runtime(application, model)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run_once)
        assert model.started.wait(5)
        if cancel:
            assert client.post(f"/v1/runs/{run['id']}/cancel").status_code == 200
        future.result(timeout=5)
    assert model.stopped.is_set()
    result = read(client, run)
    assert result["status"] == ("cancelled" if cancel else "timed_out"), result
    steps = client.get(f"/v1/runs/{run['id']}/steps").json()
    assert all(step["status"] != "running" for step in steps)


def test_long_model_call_renews_lease(application, client):
    application.state.settings.worker_lease_seconds = 2
    application.state.settings.worker_heartbeat_seconds = 1
    run, _ = configured(client)
    runtime(application, WaitingModel(delay=3)).run_once()
    assert read(client, run)["status"] == "succeeded"


def test_expired_and_reused_worker_id_are_fenced(application, client):
    agent, _ = published_agent(client)
    run = create_run(client, agent)
    first = worker(application, "same-name")
    assert first.claim() == UUID(run["id"])
    expire(application, run)
    with pytest.raises(LostLeaseError):
        first.check_live(UUID(run["id"]))
    replacement = worker(application, "same-name")
    replacement.recover_expired()
    assert replacement.claim() == UUID(run["id"])
    first.execute_claimed(UUID(run["id"]))
    assert read(client, run)["status"] == "running"
    replacement.execute_claimed(UUID(run["id"]))
    assert read(client, run)["status"] == "succeeded"


def test_same_thread_concurrent_claim(application, client):
    agent, _ = published_agent(client)
    create_run(client, agent, thread_id="one-thread")
    create_run(client, agent, thread_id="one-thread")
    barrier = Barrier(2)

    def claim(index):
        runner = worker(application, f"worker-{index}")
        barrier.wait()
        return runner.claim()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, [1, 2]))
    assert sum(value is not None for value in claims) == 1


def test_sse_pagination_reconnect_and_scope(application, client):
    agent, _ = published_agent(client)
    run = create_run(client, agent)
    with application.state.session_factory() as session, session.begin():
        row = session.get(Run, run["id"], with_for_update=True)
        for i in range(210):
            append_event(session, row, "sample", {"i": i})
    client.post(f"/v1/runs/{run['id']}/cancel")
    full = client.get(f"/v1/runs/{run['id']}/stream").text
    assert full.count("event: run_event") == 212
    rest = client.get(f"/v1/runs/{run['id']}/stream?after=1", headers={"Last-Event-ID": "205"}).text
    assert rest.count("event: run_event") == 7
    assert "id: 206\n" in rest and "id: 205\n" not in rest
    assert (
        client.get(f"/v1/runs/{run['id']}/stream", headers={"Last-Event-ID": "bad"}).status_code
        == 422
    )
    with application.state.session_factory() as session, session.begin():
        row = session.get(Run, run["id"])
        row.workspace_id = "another-workspace"
    try:
        assert client.get(f"/v1/runs/{run['id']}/stream").status_code == 404
        assert client.get(f"/v1/runs/{run['id']}/steps").status_code == 404
    finally:
        with application.state.session_factory() as session, session.begin():
            session.get(Run, run["id"]).workspace_id = application.state.settings.workspace_id


def stream_response(*deltas, finish="stop", usage=True, done=True):
    chunks = [{"choices": [{"index": 0, "delta": delta}]} for delta in deltas]
    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    if usage:
        chunks.append(
            {
                "choices": [],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            }
        )
    text = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return httpx.Response(
        200,
        text=text + ("data: [DONE]\n\n" if done else ""),
        headers={"Content-Type": "text/event-stream"},
    )


def adapter(handler):
    return LangChainModelClient(
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
    )


def test_real_adapter_parses_fragmented_tool_calls(application, client):
    run, bound = configured(client, tool=True)
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["authorization"] == "Bearer MODEL_TEST_PRIVATE"
        assert str(request.url).endswith("/chat/completions")
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        assert body["max_completion_tokens"] > 0
        assert body["parallel_tool_calls"] is False
        if len(calls) == 1:
            name = body["tools"][0]["function"]["name"]
            return stream_response(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {"name": name, "arguments": '{"query":'},
                        }
                    ]
                },
                {"tool_calls": [{"index": 0, "function": {"arguments": '"hello"}'}}]},
                finish="tool_calls",
            )
        assert body["messages"][-1]["role"] == "tool"
        assert body["messages"][-2]["tool_calls"][0]["id"] == "call_1"
        return stream_response({"content": "完"}, {"content": "成"})

    tools = FakeToolRuntime()
    runtime(application, adapter(handle), tools).run_once()
    result = read(client, run)
    assert result["status"] == "succeeded", result
    assert result["result"]["text"] == "完成"
    assert result["usage"]["total_tokens"] == 20
    assert len(tools.calls) == 1


@pytest.mark.parametrize(
    "status,code,retry",
    [
        (401, "model_authentication_failed", False),
        (400, "model_request_rejected", False),
        (429, "provider_unavailable", True),
        (503, "provider_unavailable", True),
    ],
)
def test_model_http_errors_and_retry_budget(application, client, status, code, retry):
    run, _ = configured(client)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, text="MODEL_TEST_PRIVATE")

    runner = runtime(application, adapter(handle))
    runner.run_once()
    assert len(requests) == 1  # SDK retries must not bypass platform budgeting.
    result = read(client, run)
    assert result["status"] == ("queued" if retry else "failed"), result
    charge = result["usage"]["charged_tokens"]
    assert charge > 0
    if retry:
        runner.run_once()
        runner.run_once()
        result = read(client, run)
        assert result["status"] == "failed" and result["usage"]["charged_tokens"] == 3 * charge
        assert len(requests) == 3
    assert result["error"]["code"] == code
    assert "MODEL_TEST_PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("finish,usage", [(None, True), ("stop", False)])
def test_incomplete_stream_and_missing_usage(application, client, finish, usage):
    run, _ = configured(client)
    runtime(
        application,
        adapter(lambda request: stream_response({"content": "ok"}, finish=finish, usage=usage)),
    ).run_once()
    result = read(client, run)
    assert result["status"] == ("succeeded" if finish else "failed"), result
    assert result["usage"]["charged_tokens"] > 10
    if not usage:
        assert result["usage"]["unmeasured_calls"] == 1


def test_stream_redactor_cross_chunk_and_overlapping_secrets():
    output = []
    redactor = StreamRedactor(["MODEL_TEST_PRIVATE", "abcde", "def"], output.append)
    for char in "hello MODEL_TEST_PRIVATE abcdef done":
        redactor.feed(char)
    redactor.feed("", final=True)
    assert "MODEL_TEST_PRIVATE" not in "".join(output)
    assert "abcde" not in "".join(output)
    assert "def" not in "".join(output)


def test_langchain_decoded_output_limit():
    class LargeModel:
        def __init__(self, **kwargs):
            pass

        async def astream(self, messages):
            yield AIMessageChunk(content="x" * 2_097_152)

    with pytest.raises(DeterministicRunError, match="2 MiB"):
        asyncio.run(
            LangChainModelClient(model_factory=LargeModel).complete(
                model={
                    "model_name": "test",
                    "base_url": "https://models.example/v1",
                    "timeout_seconds": 10,
                },
                secret="TEST",
                messages=[{"role": "user", "content": "hello"}],
                functions=[],
                settings={"temperature": 0.7, "timeout_seconds": 10},
                max_tokens=100,
                emit=lambda text: pytest.fail("Oversize chunk must not be emitted"),
            )
        )


def test_compiled_langgraph_nodes(application):
    executor = AgentExecutor(runtime(application, FakeModel()))
    graph = executor.build_graph(UUID("11111111-1111-4111-8111-111111111111"), {})
    assert set(graph.get_graph().nodes) == {"__start__", "model", "tool", "__end__"}
    assert graph.checkpointer is None  # Fenced SQL node checkpoints are authoritative.


@pytest.mark.parametrize("boundary", [1, 2])
def test_langchain_langgraph_recovery_at_each_node(application, client, boundary):
    run, bound = configured(client, tool=True)
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            call = calling(bound)["tool_calls"][0]
            return stream_response({"tool_calls": [{**call, "index": 0}]}, finish="tool_calls")
        assert payload["messages"][-1]["role"] == "tool"
        return stream_response({"content": "Recovered through LangGraph"})

    tools = FakeToolRuntime()
    model = adapter(handle)
    runtime(application, model, tools).run_once(max_steps=boundary)
    expire(application, run)
    runtime(application, model, tools, name="replacement").run_once()
    result = read(client, run)
    assert result["status"] == "succeeded", result
    assert len(requests) == 2 and len(tools.calls) == 1
    assert result["usage"]["total_tokens"] == 20


@pytest.mark.parametrize("arguments", ['{"query":"hello"', '["hello"]'])
def test_langchain_partial_json_cannot_execute_tool(application, client, arguments):
    run, bound = configured(client, tool=True)
    call = calling(bound, arguments=arguments)["tool_calls"][0]
    tools = FakeToolRuntime()
    model = adapter(
        lambda request: stream_response(
            {"tool_calls": [{**call, "index": 0}]},
            finish="tool_calls",
        )
    )
    runtime(application, model, tools).run_once()
    assert read(client, run)["status"] == "failed"
    assert not tools.calls


def test_langchain_cancel_closes_sdk_stream(application, client):
    run, _ = configured(client)
    started, closed = Event(), Event()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            yield b'data: {"choices":[{"index":0,"delta":{"content":"hello"}}]}\n\n'
            await asyncio.sleep(30)

        async def aclose(self):
            closed.set()

    model = adapter(
        lambda request: httpx.Response(
            200,
            stream=SlowStream(),
            headers={"Content-Type": "text/event-stream"},
        )
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime(application, model).run_once)
        assert started.wait(10)
        client.post(f"/v1/runs/{run['id']}/cancel")
        future.result(timeout=10)
    assert read(client, run)["status"] == "cancelled"
    assert closed.is_set()


def test_graph_does_not_enable_external_tracing(application, client, monkeypatch):
    from langsmith import utils as langsmith_utils

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    run, _ = configured(client)

    class PrivateModel(FakeModel):
        async def complete(self, **kwargs):
            assert not langsmith_utils.tracing_is_enabled()
            return await super().complete(**kwargs)

    runtime(application, PrivateModel()).run_once()
    assert read(client, run)["status"] == "succeeded"
