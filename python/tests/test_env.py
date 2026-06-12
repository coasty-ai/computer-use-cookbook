"""env.py -- key / base-url / spend-confirmation helpers (offline)."""

from __future__ import annotations

import pytest

from coasty import env


def test_get_api_key_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COASTY_API_KEY", "  sk-coasty-test-abc  ")
    assert env.get_api_key() == "sk-coasty-test-abc"


def test_get_api_key_empty_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COASTY_API_KEY", "   ")
    assert env.get_api_key() is None


def test_require_api_key_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COASTY_API_KEY")
    with pytest.raises(env.MissingAPIKeyError):
        env.require_api_key()


def test_base_url_defaults_to_production() -> None:
    assert env.get_base_url() == "https://coasty.ai/v1"
    assert env.DEFAULT_BASE_URL == "https://coasty.ai/v1"


def test_base_url_override_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COASTY_BASE_URL", "http://127.0.0.1:8787/v1/")
    assert env.get_base_url() == "http://127.0.0.1:8787/v1"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("", False),
        ("no", False),
        ("maybe", False),
    ],
)
def test_spend_confirmed(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
    monkeypatch.setenv("COASTY_CONFIRM_SPEND", value)
    assert env.spend_confirmed() is expected


def test_is_sandbox_key() -> None:
    assert env.is_sandbox_key("sk-coasty-test-" + "0" * 48) is True
    assert env.is_sandbox_key("sk-coasty-live-" + "0" * 48) is False
    assert env.is_sandbox_key("cua_sk_" + "0" * 48) is False
    assert env.is_sandbox_key("") is False
