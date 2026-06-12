"""coasty_mock — a fully offline mock of the Coasty Computer Use API.

Usage:

    from coasty_mock import create_app
    app = create_app()                # FrozenClock, seed 0

or standalone:

    python -m coasty_mock --port 8787   # -> http://127.0.0.1:8787/v1
"""

from __future__ import annotations

from .app import create_app
from .state import MockConfig, TestState

__all__ = ["MockConfig", "TestState", "create_app"]

__version__ = "0.1.0"
