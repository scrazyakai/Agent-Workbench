from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import build_engine, build_session_factory
from app.services.worker import RunWorker


def main():
    settings = Settings()
    configure_logging(settings.log_level)
    engine = build_engine(settings.database_url)
    try:
        RunWorker(build_session_factory(engine), settings).run_forever()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
