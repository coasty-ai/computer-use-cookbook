"""Known-answer tests for the pricing table (docs/API_NOTES.md SSPricing)."""

from __future__ import annotations

import pytest

from coasty import cost

# ── HD boundary (strict: w>1280 OR h>720) ──────────────────────────────────


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1280, 720, False),  # exactly 1280x720 is NOT HD
        (1281, 720, True),
        (1280, 721, True),
        (1920, 1080, True),
        (640, 480, False),
        (1280, 1080, True),
        (1920, 720, True),
    ],
)
def test_is_hd_strict_boundary(width: int, height: int, expected: bool) -> None:
    assert cost.is_hd(width, height) is expected


# ── predict ─────────────────────────────────────────────────────────────────


def test_predict_default_fullhd_costs_6() -> None:
    # base 5 + 1 HD (1920x1080 default)
    assert cost.estimate_predict().credits == 6


def test_predict_at_1280x720_is_not_hd() -> None:
    assert cost.estimate_predict(screen_width=1280, screen_height=720).credits == 5


def test_predict_with_trajectory_surcharges() -> None:
    # base 5 + 2 trajectory shots (+2 each) + HD current (+1) + 1 HD trajectory (+1)
    estimate = cost.estimate_predict(
        screen_width=1920,
        screen_height=1080,
        trajectory_sizes=[(1920, 1080), (1280, 720)],
    )
    assert estimate.credits == 5 + 4 + 1 + 1


def test_predict_v1_engine_surcharge() -> None:
    estimate = cost.estimate_predict(cua_version="v1", screen_width=1280, screen_height=720)
    assert estimate.credits == 5 + 3


def test_predict_system_prompt_boundary_exactly_500_is_free() -> None:
    sd = {"screen_width": 1280, "screen_height": 720}
    assert cost.estimate_predict(system_prompt="x" * 500, **sd).credits == 5
    assert cost.estimate_predict(system_prompt="x" * 501, **sd).credits == 6
    assert cost.estimate_predict(system_prompt=None, **sd).credits == 5


def test_predict_all_surcharges_stack() -> None:
    estimate = cost.estimate_predict(
        cua_version="v1",
        screen_width=2560,
        screen_height=1440,
        trajectory_sizes=[(2560, 1440)],
        system_prompt="p" * 600,
    )
    # 5 base + 2 trajectory + 2 HD (current + trajectory) + 3 v1 + 1 prompt
    assert estimate.credits == 13


# ── sessions / ground / parse ───────────────────────────────────────────────


def test_session_create_is_flat_10() -> None:
    assert cost.estimate_session_create().credits == 10


def test_session_predict_base_4_with_surcharges() -> None:
    assert cost.estimate_session_predict(screen_width=1280, screen_height=720).credits == 4
    estimate = cost.estimate_session_predict(
        screen_width=1920,
        screen_height=1080,
        trajectory_sizes=[(1920, 1080), (1920, 1080), (1920, 1080)],
    )
    # 4 base + 3x2 trajectory + 4 HD (current + 3 trajectory)
    assert estimate.credits == 4 + 6 + 4


def test_ground_hd_surcharge() -> None:
    assert cost.estimate_ground(screen_width=1280, screen_height=720).credits == 3
    assert cost.estimate_ground(screen_width=1920, screen_height=1080).credits == 4
    assert cost.estimate_ground().credits == 4  # defaults are 1920x1080


def test_parse_is_free() -> None:
    assert cost.estimate_parse().credits == 0
    assert cost.estimate_parse().usd == 0.0


# ── runs / workflows ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("version", "expected"), [("v3", 5), ("v4", 5), ("v1", 8)])
def test_run_step_credits(version: str, expected: int) -> None:
    assert cost.run_step_credits(version) == expected  # type: ignore[arg-type]


def test_estimate_run() -> None:
    assert cost.estimate_run(steps=10).credits == 50
    assert cost.estimate_run(steps=10, cua_version="v1").credits == 80
    assert cost.estimate_run(steps=0).credits == 0
    with pytest.raises(ValueError, match=">= 0"):
        cost.estimate_run(steps=-1)


def test_estimate_workflow_run_control_flow_free() -> None:
    estimate = cost.estimate_workflow_run(task_steps=3)
    assert estimate.credits == 15
    labels = [item.label for item in estimate.items]
    assert any("control-flow" in label for label in labels)
    with pytest.raises(ValueError, match=">= 0"):
        cost.estimate_workflow_run(task_steps=-2)


# ── machines ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("os_type", "state", "expected"),
    [
        ("linux", "running", 5),
        ("linux", "starting", 5),
        ("linux", "stopping", 5),
        ("linux", "restarting", 5),
        ("windows", "running", 9),
        ("windows", "stopped", 1),
        ("linux", "stopped", 1),
        ("linux", "suspended", 1),
        ("windows", "suspended_for_billing", 1),
        ("linux", "creating", 0),
        ("windows", "error", 0),
        ("linux", "terminated", 0),
    ],
)
def test_machine_hourly_credits(os_type: str, state: str, expected: int) -> None:
    assert cost.machine_hourly_credits(os_type, state) == expected  # type: ignore[arg-type]


def test_machine_hourly_unknown_state_raises() -> None:
    with pytest.raises(ValueError, match="unknown machine state"):
        cost.machine_hourly_credits("linux", "hibernating")


def test_machine_runtime_metered_per_minute_rounded_down() -> None:
    # 90 min of Linux running at 5 cr/hr = 7.5 -> 7 (rounded down)
    assert cost.estimate_machine_runtime(os_type="linux", state="running", minutes=90).credits == 7
    # 5 min = 0.41 -> 0
    assert cost.estimate_machine_runtime(os_type="linux", state="running", minutes=5).credits == 0
    # 60 min Windows running = 9
    assert (
        cost.estimate_machine_runtime(os_type="windows", state="running", minutes=60).credits == 9
    )
    with pytest.raises(ValueError, match=">= 0"):
        cost.estimate_machine_runtime(os_type="linux", state="running", minutes=-1)


def test_snapshot_costs_1() -> None:
    assert cost.estimate_snapshot().credits == 1


# ── combine / format ────────────────────────────────────────────────────────


def test_combine_merges_items() -> None:
    merged = cost.combine(cost.estimate_session_create(), cost.estimate_snapshot())
    assert merged.credits == 11
    assert len(merged.items) == 2


def test_usd_conversion_is_one_cent_per_credit() -> None:
    assert cost.estimate_session_create().usd == pytest.approx(0.10)
    assert cost.CREDIT_USD == 0.01


def test_format_estimate_itemizes_and_totals() -> None:
    text = cost.format_estimate(cost.estimate_predict(), title="predict")
    assert text.startswith("predict:")
    assert "predict base: 5 cr" in text
    assert "total: 6 cr = $0.06" in text


def test_format_estimate_sandbox_is_zero_dollars() -> None:
    text = cost.format_estimate(cost.estimate_predict(), sandbox=True)
    assert "$0.00" in text
    assert "never billed" in text
    assert "$0.06" not in text
