"""ex10: cost helper -- known-answer pricing tests incl. the documented edges."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ex10_cost_helper as ex10

# ── predict: base + HD + trajectory + v1 + system_prompt edges ─────────────


def test_predict_default_fullhd_is_six_credits() -> None:
    assert ex10.estimate_predict_cmd().credits == 6  # 5 base + 1 HD (1920x1080)


def test_predict_1280x720_is_exactly_not_hd() -> None:
    assert ex10.estimate_predict_cmd(width=1280, height=720).credits == 5


@pytest.mark.parametrize(("width", "height"), [(1281, 720), (1280, 721)])
def test_predict_one_pixel_over_is_hd(width: int, height: int) -> None:
    assert ex10.estimate_predict_cmd(width=width, height=height).credits == 6


def test_predict_system_prompt_500_chars_is_free_501_is_not() -> None:
    base = {"width": 1280, "height": 720}
    assert ex10.estimate_predict_cmd(system_prompt_chars=500, **base).credits == 5
    assert ex10.estimate_predict_cmd(system_prompt_chars=501, **base).credits == 6


def test_predict_trajectory_surcharges_count_hd_per_image() -> None:
    # 5 base + 3x2 trajectory + 4x1 HD (current + 3 trajectory shots) = 15
    assert ex10.estimate_predict_cmd(trajectory=3).credits == 15
    # SD everywhere: 5 base + 2x2 trajectory = 9
    assert ex10.estimate_predict_cmd(width=1280, height=720, trajectory=2).credits == 9


def test_predict_v1_engine_surcharge() -> None:
    assert ex10.estimate_predict_cmd(cua_version="v1", width=1280, height=720).credits == 8


def test_predict_count_scales_linearly() -> None:
    assert ex10.estimate_predict_cmd(width=1280, height=720, count=10).credits == 50


# ── session / ground / run / workflow / machine ────────────────────────────


def test_session_create_plus_steps() -> None:
    estimate = ex10.estimate_session_cmd(steps=5, width=1280, height=720)
    assert estimate.credits == 10 + 5 * 4  # create 10 flat + 4 cr per step
    assert ex10.estimate_session_cmd(steps=0).credits == 10


def test_ground_hd_surcharge() -> None:
    assert ex10.estimate_ground_cmd(width=1920, height=1080).credits == 4
    assert ex10.estimate_ground_cmd(width=1280, height=720).credits == 3


def test_run_steps_v3_vs_v1() -> None:
    assert ex10.estimate_run_cmd(steps=12, cua_version="v3").credits == 60
    assert ex10.estimate_run_cmd(steps=12, cua_version="v4").credits == 60
    assert ex10.estimate_run_cmd(steps=12, cua_version="v1").credits == 96


def test_workflow_task_steps_bill_control_flow_free() -> None:
    estimate = ex10.estimate_workflow_cmd(task_steps=6)
    assert estimate.credits == 30
    assert any(item.credits == 0 and "free" in item.label for item in estimate.items)


def test_machine_runtime_metered_per_minute_rounded_down() -> None:
    assert ex10.estimate_machine_cmd(os_type="windows", hours=2.0).credits == 18
    assert ex10.estimate_machine_cmd(os_type="linux", hours=1.5).credits == 7  # 7.5 floors
    assert ex10.estimate_machine_cmd(os_type="linux", hours=3.0, state="stopped").credits == 3
    assert ex10.estimate_machine_cmd(os_type="linux", hours=1.5, snapshots=2).credits == 9


def test_machine_negative_hours_rejected() -> None:
    with pytest.raises(ValueError, match="hours"):
        ex10.estimate_machine_cmd(hours=-1)


# ── plan mode ──────────────────────────────────────────────────────────────


def _plan() -> dict[str, object]:
    return {
        "items": [
            {"kind": "predict", "count": 10, "screen_width": 1280, "screen_height": 720},  # 50
            {"kind": "predict", "trajectory": 3},  # 15
            {"kind": "ground", "count": 2},  # 8
            {"kind": "session_create"},  # 10
            {"kind": "session_predict", "count": 5, "screen_width": 1280, "screen_height": 720},
            {"kind": "run", "steps": 10, "cua_version": "v1"},  # 80   (session steps: 20)
            {"kind": "workflow", "task_steps": 4},  # 20
            {"kind": "machine", "os": "windows", "hours": 2},  # 18
            {"kind": "machine", "os": "linux", "state": "stopped", "minutes": 180},  # 3
            {"kind": "snapshot", "count": 2},  # 2
            {"kind": "parse", "count": 100},  # 0
        ]
    }


def test_plan_totals_a_whole_batch() -> None:
    estimate = ex10.estimate_plan(_plan())
    assert estimate.credits == 50 + 15 + 8 + 10 + 20 + 80 + 20 + 18 + 3 + 2 + 0  # 226
    assert estimate.usd == pytest.approx(2.26)


def test_plan_rejects_unknown_kind_and_bad_shapes() -> None:
    with pytest.raises(ex10.PlanError, match="unknown plan item kind"):
        ex10.estimate_plan({"items": [{"kind": "teleport"}]})
    with pytest.raises(ex10.PlanError, match="items"):
        ex10.estimate_plan({"items": []})
    with pytest.raises(ex10.PlanError, match="integer"):
        ex10.estimate_plan({"items": [{"kind": "predict", "count": True}]})
    with pytest.raises(ex10.PlanError, match="object"):
        ex10.estimate_plan({"items": ["predict"]})


def test_load_plan_errors_are_clear(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(ex10.PlanError, match="cannot read plan file"):
        ex10.load_plan(missing)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ex10.PlanError, match="not valid JSON"):
        ex10.load_plan(bad)


# ── CLI ────────────────────────────────────────────────────────────────────


def test_cli_predict_prints_itemized_credits_and_usd(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = ex10.main(["predict", "--width", "1280", "--height", "720"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "predict base: 5 cr" in out
    # the autouse fixture pins a sandbox key -> labeled $0.00
    assert "total: 5 cr = $0.00 (sandbox key - never billed)" in out
    assert "auto-refunded on failure" in out  # the documented refund rule


def test_cli_live_key_shows_real_dollars_and_sandbox_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COASTY_API_KEY", "sk-coasty-live-" + "0" * 48)  # obviously fake
    exit_code = ex10.main(["machine", "--os", "windows", "--hours", "2"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "total: 18 cr = $0.18" in out
    assert "sandbox keys (sk-coasty-test-*) never bill" in out


def test_cli_plan_subcommand_reads_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan_path = tmp_path / "batch.json"
    plan_path.write_text(json.dumps(_plan()), encoding="utf-8")
    exit_code = ex10.main(["plan", str(plan_path)])
    assert exit_code == 0
    assert "total: 226 cr" in capsys.readouterr().out


def test_cli_plan_missing_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = ex10.main(["plan", str(tmp_path / "missing.json")])
    assert exit_code == 2
    assert "cannot read plan file" in capsys.readouterr().err


def test_parser_maps_args_to_estimators() -> None:
    parser = ex10.build_parser()
    args = parser.parse_args(["machine", "--os", "windows", "--hours", "2"])
    assert ex10.estimate_from_args(args).credits == 18
    args = parser.parse_args(["workflow", "--task-steps", "4", "--cua-version", "v1"])
    assert ex10.estimate_from_args(args).credits == 32  # 4 x 8 cr on v1
