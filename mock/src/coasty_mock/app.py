"""FastAPI application factory wiring every router behind the auth middleware."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import ApiError, error_body
from .middleware import CoastyMiddleware
from .routes_admin import router as admin_router
from .routes_core import router as core_router
from .routes_machines import router as machines_router
from .routes_runs import router as runs_router
from .routes_sessions import router as sessions_router
from .routes_workflows import router as workflows_router
from .state import TestState


def _request_id_of(request: Request) -> str:
    scope_state = request.scope.get("state") or {}
    return str(scope_state.get("request_id", "req_unknown"))


async def _api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(
        status_code=exc.status,
        content=error_body(
            exc.code, exc.message, exc.error_type, _request_id_of(request), exc.extras
        ),
        headers=exc.headers,
    )


async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code == 404:
        code, error_type = "NOT_FOUND", "not_found_error"
        message = f"No route {request.url.path!r}."
    else:
        code, error_type = "VALIDATION_ERROR", "validation_error"
        message = str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(code, message, error_type, _request_id_of(request)),
    )


def create_app(state: TestState | None = None) -> FastAPI:
    """Build the mock server; pass a TestState to control seed/clock/config."""
    mock_state = state if state is not None else TestState()
    app = FastAPI(title="coasty-mock", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.mock = mock_state

    app.include_router(core_router)
    app.include_router(sessions_router)
    app.include_router(runs_router)
    app.include_router(workflows_router)
    app.include_router(machines_router)
    app.include_router(admin_router)

    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_middleware(CoastyMiddleware, state=mock_state)
    return app
