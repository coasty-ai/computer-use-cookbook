"""Fixtures for the example tests: put ``examples/`` on sys.path.

The example scripts are plain sibling modules (not a package); when run as
``python examples/ex01_....py`` the script directory is sys.path[0], and
this conftest reproduces that for the test process. All HTTP fixtures
(``client``, ``respx_router``, payload factories, ``sse_body``) come from
the parent ``tests/conftest.py`` -- everything stays offline.
"""

from __future__ import annotations

import sys
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))
