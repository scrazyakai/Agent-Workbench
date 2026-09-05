import logging
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.requests import Request

from app.core.exception_handlers import unexpected_error
from app.core.logging import request_id_context

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    """Keep request context alive through response streaming and background tasks."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_context.set(request_id)
        started = perf_counter()
        status = 500
        response_started = False

        async def send_with_id(message):
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_with_id)
            except Exception as exc:
                response = await unexpected_error(Request(scope), exc)
                if response_started:
                    # A sent/streaming response cannot be replaced by a JSON error.
                    raise
                await response(scope, receive, send_with_id)
        finally:
            route = scope.get("route")
            logger.info(
                "request_completed",
                extra={
                    "method": scope["method"],
                    "route": getattr(route, "path", "<unmatched>"),
                    "status_code": status,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                },
            )
            request_id_context.reset(token)
