from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from coasty_mock import create_app
from coasty_mock.state import TestState
from helpers import TEST_KEY


@pytest.fixture()
def state() -> TestState:
    return TestState(seed=1234)


@pytest.fixture()
def client(state: TestState) -> Iterator[TestClient]:
    """A TestClient authenticated with the fake sandbox key by default."""
    with TestClient(create_app(state)) as test_client:
        test_client.headers.update({"X-API-Key": TEST_KEY})
        yield test_client
