import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.core.errors import DomainError

logger = logging.getLogger(__name__)


def error_response(request, status, code, message, details=None, headers=None):
    request_id = request.state.request_id
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
                "request_id": request_id,
            }
        },
        headers={**(headers or {}), "X-Request-ID": request_id},
    )


async def domain_error(request: Request, exc: DomainError):
    logger.log(
        logging.ERROR if exc.status >= 500 else logging.WARNING,
        "request_rejected",
        extra={"error_code": exc.code, "status_code": exc.status},
    )
    return error_response(request, exc.status, exc.code, exc.message, exc.details)


async def validation_error(request: Request, exc: RequestValidationError):
    # Pydantic messages may contain user-supplied values; return only location and error type.
    details = [
        {"loc": list(item["loc"]), "message": "Invalid value", "type": item["type"]}
        for item in exc.errors()
    ]
    logger.warning(
        "request_validation_failed", extra={"error_code": "validation_error", "status_code": 422}
    )
    return error_response(request, 422, "validation_error", "Invalid request", details)


async def http_error(request: Request, exc: HTTPException):
    message = str(exc.detail)
    if exc.status_code >= 500:
        message = "Internal server error"
    logger.log(
        logging.ERROR if exc.status_code >= 500 else logging.WARNING,
        "http_error",
        extra={"error_code": "http_error", "status_code": exc.status_code},
    )
    return error_response(request, exc.status_code, "http_error", message, headers=exc.headers)


async def database_error(request: Request, exc: SQLAlchemyError):
    logger.error(
        "database_operation_failed",
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={"error_code": "database_unavailable", "status_code": 503},
    )
    return error_response(request, 503, "database_unavailable", "Database operation failed")


async def unexpected_error(request: Request, exc: Exception):
    logger.error(
        "unhandled_exception",
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={"error_code": "internal_error", "status_code": 500},
    )
    return error_response(request, 500, "internal_error", HTTPStatus(500).phrase)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(SQLAlchemyError, database_error)
