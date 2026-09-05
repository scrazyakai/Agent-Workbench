import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.logging import JsonFormatter, configure_logging, request_id_context
from app.main import create_app


@pytest.fixture
def app_and_logs():
    app = create_app(Settings(_env_file=None))
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app")
    original = logger.handlers
    logger.handlers = [handler]

    @app.get("/test/ok/{item}")
    def ok(item: str):
        logging.getLogger("app.test").info("business_event")
        return {"request_id": request_id_context.get()}

    @app.get("/test/domain")
    def domain():
        raise DomainError(409, "name_conflict", "Name already exists", [{"field": "name"}])

    @app.get("/test/database")
    def database():
        raise OperationalError("SELECT secret", {"password": "PRIVATE"}, Exception("PRIVATE"))

    @app.get("/test/unexpected")
    def unexpected():
        raise RuntimeError("PRIVATE")

    @app.get("/test/auth")
    def auth():
        raise HTTPException(401, "Login required", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/test/server-http")
    def server_http():
        raise HTTPException(500, "PRIVATE")

    yield app, stream
    logger.handlers = original


def records(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/test/domain", 409, "name_conflict"),
        ("/test/database", 503, "database_unavailable"),
        ("/test/unexpected", 500, "internal_error"),
        ("/test/auth", 401, "http_error"),
        ("/test/server-http", 500, "http_error"),
        ("/not-found", 404, "http_error"),
        ("/v1/agents/not-a-uuid", 422, "validation_error"),
    ],
)
def test_error_contract_and_logs(app_and_logs, path, status, code):
    app, stream = app_and_logs
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert "PRIVATE" not in response.text + stream.getvalue()
    request_logs = [r for r in records(stream) if r["request_id"] == error["request_id"]]
    assert len([r for r in request_logs if r["message"] == "request_completed"]) == 1
    assert any(r.get("error_code") == code for r in request_logs)
    if path == "/test/auth":
        assert response.headers["WWW-Authenticate"] == "Bearer"
    if path in ("/test/unexpected", "/test/database"):
        assert any(r.get("stack") and r.get("exception_type") for r in request_logs)


def test_request_context_and_no_raw_url(app_and_logs):
    app, stream = app_and_logs
    with TestClient(app) as client:
        result = client.get(
            "/test/ok/PRIVATE?password=PRIVATE",
            headers={"Authorization": "Bearer PRIVATE", "X-Request-ID": "PRIVATE"},
        )
    assert result.json()["request_id"] == result.headers["X-Request-ID"]
    assert "PRIVATE" not in stream.getvalue()
    event = next(r for r in records(stream) if r["message"] == "request_completed")
    assert event["route"] == "/test/ok/{item}"
    assert event["status_code"] == 200
    assert event["duration_ms"] >= 0
    assert request_id_context.get() is None


def test_concurrent_context_isolation(app_and_logs):
    app, stream = app_and_logs
    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(lambda i: client.get(f"/test/ok/{i}"), range(8)))
    ids = {r.headers["X-Request-ID"] for r in responses}
    assert len(ids) == 8
    assert all(r.json()["request_id"] == r.headers["X-Request-ID"] for r in responses)
    business = [r for r in records(stream) if r["message"] == "business_event"]
    assert {r["request_id"] for r in business} == ids


def test_validation_does_not_echo_payload(app_and_logs):
    app, stream = app_and_logs
    with TestClient(app) as client:
        result = client.post(
            "/v1/agents", json={"name": "PRIVATE", "input_schema": {"type": "PRIVATE"}}
        )
    assert result.status_code == 422
    assert "PRIVATE" not in result.text + stream.getvalue()


def test_health_uses_common_database_handler(app_and_logs):
    app, _ = app_and_logs

    def failed_session():
        raise OperationalError("", {}, Exception("PRIVATE"))

    app.state.session_factory = failed_session
    with TestClient(app) as client:
        result = client.get("/health")
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "database_unavailable"


def test_logging_setup_is_idempotent():
    configure_logging("INFO")
    configure_logging("INFO")
    assert len(logging.getLogger("app").handlers) == 1
    assert not logging.getLogger("app").propagate
    assert isinstance(logging.getLogger("uvicorn.access").handlers[0], logging.NullHandler)
