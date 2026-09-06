from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Checkpoint, Run, StepExecution, utcnow
from app.services.worker import RunWorker


def published_agent(client, name="runtime-agent", input_schema=None):
    connection = client.post(
        "/v1/model-connections",
        json={
            "name": f"runtime-connection-{uuid4()}",
            "model_name": "gpt-test",
            "base_url": "https://models.example/v1",
            "api_key": "MODEL_TEST_PRIVATE",
        },
    ).json()
    agent = client.post(
        "/v1/agents",
        json={
            "name": f"{name}-{uuid4()}",
            "system_prompt": "Return a deterministic response",
            "input_schema": input_schema or {"type": "object"},
            "model_config": {"connection_id": connection["id"]},
        },
    ).json()
    version = client.post(f"/v1/agents/{agent['id']}/versions").json()
    return agent, version


def create_run(client, agent, *, input_value=None, thread_id=None, key=None):
    payload = {
        "target": {"type": "agent", "id": agent["id"], "version": 1},
        "input": input_value or {"question": "hello"},
    }
    if thread_id:
        payload["thread_id"] = thread_id
    if key:
        payload["idempotency_key"] = key
    response = client.post("/v1/runs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def worker(application, name):
    return RunWorker(application.state.session_factory, application.state.settings, name)


def test_create_list_events_and_execute(application, client):
    agent, _ = published_agent(client)
    run = create_run(client, agent, thread_id="thread-a")
    assert run["status"] == "queued"
    assert client.get("/v1/runs", params={"thread_id": "thread-a"}).json()["total"] == 1

    assert worker(application, "worker-a").run_once() == UUID(run["id"])
    completed = client.get(f"/v1/runs/{run['id']}").json()
    assert completed["status"] == "succeeded"
    assert completed["result"] == {
        "agent": agent["name"],
        "version": 1,
        "input": {"question": "hello"},
    }
    events = client.get(f"/v1/runs/{run['id']}/events", params={"limit": 3}).json()
    assert [event["event_type"] for event in events["items"]] == [
        "run_queued",
        "run_started",
        "step_started",
    ]
    assert events["has_more"] is True
    rest = client.get(
        f"/v1/runs/{run['id']}/events", params={"after": events["next_cursor"]}
    ).json()
    assert rest["items"][-1]["event_type"] == "run_succeeded"


def test_idempotency_and_conflict(application, client):
    agent, _ = published_agent(client)
    key = f"key-{uuid4()}"
    payload = {
        "target": {"type": "agent", "id": agent["id"], "version": 1},
        "input": {"value": 1},
        "idempotency_key": key,
    }

    def submit(_):
        with TestClient(application) as peer:
            response = peer.post("/v1/runs", json=payload)
            assert response.status_code == 201, response.text
            return response.json()["id"]

    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(submit, range(4)))
    assert len(set(ids)) == 1
    conflict = client.post("/v1/runs", json={**payload, "input": {"value": 2}})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_cancel_queued_and_running(application, client):
    agent, _ = published_agent(client)
    queued = create_run(client, agent)
    cancelled = client.post(f"/v1/runs/{queued['id']}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert client.post(f"/v1/runs/{queued['id']}/cancel").json()["status"] == "cancelled"

    running = create_run(client, agent)
    executor = worker(application, "worker-cancel")
    assert executor.claim() == UUID(running["id"])
    assert client.post(f"/v1/runs/{running['id']}/cancel").json()["status"] == "cancelling"
    executor.execute_claimed(running["id"])
    assert client.get(f"/v1/runs/{running['id']}").json()["status"] == "cancelled"


def test_checkpoint_recovery_does_not_repeat_step(application, client):
    agent, _ = published_agent(client)
    run = create_run(client, agent)
    first = worker(application, "worker-first")
    assert first.run_once(max_steps=1) == UUID(run["id"])

    with application.state.session_factory.begin() as session:
        stored = session.get(Run, run["id"])
        assert stored.status == "running"
        stored.lease_expires_at = utcnow() - timedelta(seconds=1)

    second = worker(application, "worker-second")
    assert second.run_once() == UUID(run["id"])
    result = client.get(f"/v1/runs/{run['id']}").json()
    assert result["status"] == "succeeded"
    assert result["recovery_count"] == 1
    with application.state.session_factory() as session:
        validate = session.scalar(
            select(StepExecution).where(
                StepExecution.run_id == run["id"],
                StepExecution.step_key == "validate_input",
            )
        )
        checkpoints = list(
            session.scalars(
                select(Checkpoint)
                .where(Checkpoint.run_id == run["id"])
                .order_by(Checkpoint.sequence)
            )
        )
        assert validate.attempt_count == 1
        assert checkpoints[-1].state["completed_steps"] == [
            "validate_input",
            "produce_result",
        ]


def test_thread_serialization_and_multi_worker(application, client):
    agent, _ = published_agent(client)
    first = create_run(client, agent, thread_id="serial")
    second = create_run(client, agent, thread_id="serial")
    third = create_run(client, agent, thread_id="parallel")
    one = worker(application, "worker-one")
    two = worker(application, "worker-two")
    assert one.claim() == UUID(first["id"])
    assert two.claim() == UUID(third["id"])
    one.execute_claimed(first["id"])
    two.execute_claimed(third["id"])
    assert two.claim() == UUID(second["id"])


def test_concurrent_workers_only_claim_once(application, client):
    agent, _ = published_agent(client)
    run = create_run(client, agent)

    def claim(name):
        return worker(application, name).claim()

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ["worker-left", "worker-right"]))
    assert claims.count(UUID(run["id"])) == 1
    assert claims.count(None) == 1


def test_invalid_input_fails_without_retry(application, client):
    agent, _ = published_agent(
        client,
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
    )
    run = create_run(client, agent, input_value={"value": "wrong"})
    worker(application, "worker-invalid").run_once()
    failed = client.get(f"/v1/runs/{run['id']}").json()
    assert failed["status"] == "failed"
    assert failed["execution_attempts"] == 1
    assert failed["error"]["code"] == "input_schema_invalid"


def test_transient_failure_retries_three_times(application, client):
    class FailingWorker(RunWorker):
        def _produce_result(self, run_id):
            raise RuntimeError("PRIVATE upstream detail")

    agent, _ = published_agent(client)
    run = create_run(client, agent)
    executor = FailingWorker(
        application.state.session_factory,
        application.state.settings,
        "worker-failing",
    )
    for _ in range(3):
        executor.run_once()
    failed = client.get(f"/v1/runs/{run['id']}").json()
    assert failed["status"] == "failed"
    assert failed["execution_attempts"] == 3
    assert failed["error"] == {"code": "execution_error", "message": "Run execution failed"}


def test_missing_version_and_workspace_scope(application, client):
    missing = client.post(
        "/v1/runs",
        json={
            "target": {"type": "agent", "id": str(uuid4()), "version": 1},
            "input": {},
        },
    )
    assert missing.status_code == 404
    agent, _ = published_agent(client)
    run = create_run(client, agent)
    original = application.state.settings.workspace_id
    try:
        application.state.settings.workspace_id = str(uuid4())
        assert client.get(f"/v1/runs/{run['id']}").status_code == 404
        assert client.get("/v1/runs").json()["total"] == 0
    finally:
        application.state.settings.workspace_id = original
