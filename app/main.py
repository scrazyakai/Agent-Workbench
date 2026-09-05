import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import router
from app.core.config import Settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestLoggingMiddleware
from app.db.session import build_engine, build_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    engine = build_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        logger = logging.getLogger(__name__)
        logger.info("application_started")
        try:
            yield
        finally:
            engine.dispose()
            logger.info("application_stopped")

    application = FastAPI(title="AI Workbench", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.session_factory = build_session_factory(engine)

    register_exception_handlers(application)
    application.add_middleware(RequestLoggingMiddleware)
    application.include_router(router)
    return application


app = create_app()
