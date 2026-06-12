"""The full pricing table (1 credit = 1 cent = $0.01) incl. surcharges.

Source: .llms.txt §6 Reference / Pricing and docs/API_NOTES.md §Pricing.

Surcharges on inference requests:
- +2 cr per trajectory screenshot
- +1 cr per HD image (width > 1280 OR height > 720, strict; applies to the
  current screenshot AND each trajectory screenshot; exactly 1280x720 is NOT HD)
- +3 cr per request on the v1 engine
- +1 cr when system_prompt is longer than 500 chars (exactly 500 is free)

Run / workflow task steps: 5 cr on v3/v4, 8 cr on v1, no other surcharges.
"""

from __future__ import annotations

PREDICT_BASE = 5
SESSION_CREATE = 10
SESSION_PREDICT_BASE = 4
GROUND_BASE = 3
TRAJECTORY_SURCHARGE = 2
HD_SURCHARGE = 1
V1_SURCHARGE = 3
SYSTEM_PROMPT_SURCHARGE = 1
SYSTEM_PROMPT_FREE_CHARS = 500
RUN_STEP_V3 = 5
RUN_STEP_V1 = 8
SNAPSHOT = 1
MACHINE_PROVISION_GATE = 20

MACHINE_HOURLY = {
    "running_linux": 5,
    "running_windows": 9,
    "stopped": 1,
    "creating": 0,
    "error": 0,
    "terminated": 0,
}


def is_hd(width: int, height: int) -> bool:
    """Strictly greater than 1280x720; exactly 1280x720 is NOT HD."""
    return width > 1280 or height > 720


def _surcharges(
    *,
    width: int,
    height: int,
    trajectory_screenshots: int,
    cua_version: str,
    system_prompt: str | None,
) -> int:
    total = TRAJECTORY_SURCHARGE * trajectory_screenshots
    if is_hd(width, height):
        total += HD_SURCHARGE * (1 + trajectory_screenshots)
    if cua_version == "v1":
        total += V1_SURCHARGE
    if system_prompt is not None and len(system_prompt) > SYSTEM_PROMPT_FREE_CHARS:
        total += SYSTEM_PROMPT_SURCHARGE
    return total


def predict_price(
    *,
    width: int,
    height: int,
    trajectory_screenshots: int = 0,
    cua_version: str = "v3",
    system_prompt: str | None = None,
) -> int:
    return PREDICT_BASE + _surcharges(
        width=width,
        height=height,
        trajectory_screenshots=trajectory_screenshots,
        cua_version=cua_version,
        system_prompt=system_prompt,
    )


def session_predict_price(
    *,
    width: int,
    height: int,
    trajectory_screenshots: int = 0,
    cua_version: str = "v3",
    system_prompt: str | None = None,
) -> int:
    return SESSION_PREDICT_BASE + _surcharges(
        width=width,
        height=height,
        trajectory_screenshots=trajectory_screenshots,
        cua_version=cua_version,
        system_prompt=system_prompt,
    )


def ground_price(*, width: int, height: int) -> int:
    return GROUND_BASE + (HD_SURCHARGE if is_hd(width, height) else 0)


def run_step_price(cua_version: str) -> int:
    return RUN_STEP_V1 if cua_version == "v1" else RUN_STEP_V3
