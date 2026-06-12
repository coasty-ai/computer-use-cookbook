"""Coasty Computer Use API -- shared typed client for the cookbook examples."""

from __future__ import annotations

from . import cost, dsl, env, types
from .client import ApiResult, CoastyClient
from .cost import CostEstimate, CostItem, format_estimate
from .env import (
    DEFAULT_BASE_URL,
    MissingAPIKeyError,
    get_api_key,
    get_base_url,
    is_sandbox_key,
    require_api_key,
    spend_confirmed,
)
from .errors import (
    AuthenticationError,
    CoastyConnectionError,
    CoastyError,
    ConflictError,
    InsufficientCreditsError,
    InsufficientScopeError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
    error_from_parts,
    error_from_response,
)
from .executor import (
    ActionBackend,
    ActionExecutor,
    NullBackend,
    PyAutoGuiBackend,
    UnsupportedActionError,
)
from .sse import SSEEvent, StreamInterruptedError, iter_events_reconnecting, parse_sse_lines
from .types import TERMINAL_RUN_STATUSES
from .webhooks import build_signature_header, compute_signature, verify_signature

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_BASE_URL",
    "TERMINAL_RUN_STATUSES",
    "ActionBackend",
    "ActionExecutor",
    "ApiResult",
    "AuthenticationError",
    "CoastyClient",
    "CoastyConnectionError",
    "CoastyError",
    "ConflictError",
    "CostEstimate",
    "CostItem",
    "InsufficientCreditsError",
    "InsufficientScopeError",
    "MissingAPIKeyError",
    "NotFoundError",
    "NullBackend",
    "PyAutoGuiBackend",
    "RateLimitError",
    "SSEEvent",
    "ServerError",
    "StreamInterruptedError",
    "UnsupportedActionError",
    "ValidationError",
    "__version__",
    "build_signature_header",
    "compute_signature",
    "cost",
    "dsl",
    "env",
    "error_from_parts",
    "error_from_response",
    "format_estimate",
    "get_api_key",
    "get_base_url",
    "is_sandbox_key",
    "iter_events_reconnecting",
    "parse_sse_lines",
    "require_api_key",
    "spend_confirmed",
    "types",
    "verify_signature",
]
