"""Exception handlers mapping errors to consistent JSON responses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.core.exceptions import AppError
from app.core.logging import get_logger

log = get_logger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("app_error", code=exc.code, message=exc.message)
        headers: dict[str, str] = {}
        retry_after = exc.details.get("retry_after")
        if retry_after is not None:
            headers["Retry-After"] = str(int(retry_after))
        return JSONResponse(exc.to_dict(), status_code=exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # ``exc.errors()`` may embed the original exception object in ``ctx``;
        # jsonable_encoder makes it safe to serialise.
        return JSONResponse(
            {
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "details": {"errors": jsonable_encoder(exc.errors())},
                }
            },
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "http_error", "message": str(exc.detail)}},
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        # Never leak internal details to the client.
        log.error("unhandled_exception", error=str(exc), exc_type=type(exc).__name__)
        return JSONResponse(
            {"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
            status_code=500,
        )
