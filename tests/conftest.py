import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.db.models import Agent, AgentVersion
from app.db.session import build_engine
from app.main import create_app


@pytest.fixture(scope="session")
def database_url():
    load_dotenv()
    url = os.getenv("WORKBENCH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set WORKBENCH_TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    parsed = make_url(url)
    if not parsed.database or not parsed.database.endswith("_test"):
        pytest.fail("Test database name must end with _test")
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return url


@pytest.fixture
def application(database_url):
    workspace = f"test-{uuid4()}"
    app = create_app(Settings(database_url=database_url, workspace_id=workspace))
    yield app
    engine = build_engine(database_url)
    with engine.begin() as connection:
        connection.execute(delete(AgentVersion).where(AgentVersion.workspace_id == workspace))
        connection.execute(delete(Agent).where(Agent.workspace_id == workspace))
    engine.dispose()


@pytest.fixture
def client(application):
    with TestClient(application) as client:
        yield client
