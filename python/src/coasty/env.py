"""Environment / configuration helpers for the Coasty cookbook.

Loads the repo-root ``.env`` (if present) exactly once via python-dotenv.
Values are only ever read from ``os.environ`` -- they are never logged.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://coasty.ai/v1"
SANDBOX_KEY_PREFIX = "sk-coasty-test-"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# Flipped to True after the first load attempt (tests pin it to True so the
# real repo .env is never consulted).
_dotenv_loaded = False


class MissingAPIKeyError(RuntimeError):
    """Raised when an API key is required but COASTY_API_KEY is not set."""


def _repo_root_env_file() -> Path:
    """The cookbook repo root ``.env`` (python/src/coasty -> repo root)."""
    return Path(__file__).resolve().parents[3] / ".env"


def load_repo_dotenv(*, force: bool = False) -> None:
    """Load the repo-root ``.env`` into ``os.environ`` (once, never overriding).

    Existing environment variables always win. Values are never printed.
    """
    global _dotenv_loaded
    if _dotenv_loaded and not force:
        return
    _dotenv_loaded = True
    env_file = _repo_root_env_file()
    if env_file.is_file():
        load_dotenv(env_file, override=False)


def get_api_key() -> str | None:
    """Return COASTY_API_KEY (stripped), or None when unset/empty."""
    load_repo_dotenv()
    key = os.environ.get("COASTY_API_KEY", "").strip()
    return key or None


def require_api_key() -> str:
    """Return COASTY_API_KEY or raise :class:`MissingAPIKeyError`."""
    key = get_api_key()
    if key is None:
        raise MissingAPIKeyError(
            "COASTY_API_KEY is not set. Export it or add it to the repo-root .env "
            "(use an sk-coasty-test-... sandbox key for free, unbilled testing)."
        )
    return key


def get_base_url() -> str:
    """Base URL for the API: COASTY_BASE_URL override or the default."""
    load_repo_dotenv()
    override = os.environ.get("COASTY_BASE_URL", "").strip().rstrip("/")
    return override or DEFAULT_BASE_URL


def spend_confirmed() -> bool:
    """True when COASTY_CONFIRM_SPEND opts in to billable calls (e.g. ``1``)."""
    load_repo_dotenv()
    return os.environ.get("COASTY_CONFIRM_SPEND", "").strip().lower() in _TRUTHY


def is_sandbox_key(key: str) -> bool:
    """True for sandbox keys (``sk-coasty-test-...``) which never bill."""
    return key.startswith(SANDBOX_KEY_PREFIX)
