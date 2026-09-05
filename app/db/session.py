from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def build_engine(url: str):
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


def build_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_session(request: Request):
    with request.app.state.session_factory() as session:
        yield session
