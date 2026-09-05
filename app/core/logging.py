"""Structured application/server logging without request payloads or exception values."""

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_context.get()),
        }
        # Only explicitly allowed metadata is emitted. No arbitrary extra fields or locals.
        for field in ("method", "route", "status_code", "duration_ms", "error_code"):
            if hasattr(record, field):
                entry[field] = getattr(record, field)
        if record.exc_info and record.exc_info[0]:
            entry["exception_type"] = record.exc_info[0].__name__
            entry["stack"] = [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    # Configure owned namespaces, leaving third-party/root handlers untouched.
    for name in ("app", "uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(level)
        logger.propagate = False
    # Our access event replaces Uvicorn's (which includes raw URL/query parameters).
    access = logging.getLogger("uvicorn.access")
    access.handlers = [logging.NullHandler()]
    access.propagate = False
