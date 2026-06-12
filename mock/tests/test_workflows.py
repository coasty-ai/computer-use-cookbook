"""Workflows: CRUD + versioning, DSL validation matrix, execution paths, guards."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from helpers import LIVE_KEY, auth

JsonDict = dict[str, Any]


def _task(step_id: str, task: str = "do work", **extra: Any) -> JsonDict:
    return {"id": step_id, "type": "task", "task": task, **extra}


def _run(client: TestClient, definition: JsonDict, **body: Any) -> JsonDict:
    response = client.post("/v1/workflows/runs", json={"definition": definition, **body})
    assert response.status_code == 200, response.text
    return dict(response.json())


# ----------------------------------------------------------------------- CRUD
def test_crud_and_version_bump(client: TestClient) -> None:
    definition = {"steps": [_task("t1")]}
    created = client.post(
        "/v1/workflows",
        json={"name": "Wf", "slug": "wf-one", "definition": definition},
    ).json()
    assert created["object"] == "workflow"
    assert created["version"] == 1
    assert created["dsl_version"] == "2026-06-01"
    assert created["status"] == "active"
    wf_id = created["id"]

    got = client.get(f"/v1/workflows/{wf_id}").json()
    assert got["definition"] == definition

    updated = client.put(f"/v1/workflows/{wf_id}", json={"name": "Wf v2"}).json()
    assert updated["version"] == 2
    assert updated["name"] == "Wf v2"

    # Re-using the slug on POST bumps the version of the same workflow.
    re_created = client.post(
        "/v1/workflows",
        json={"name": "Wf v3", "slug": "wf-one", "definition": definition},
    ).json()
    assert re_created["id"] == wf_id
    assert re_created["version"] == 3

    listed = client.get("/v1/workflows").json()
    assert [wf["id"] for wf in listed["data"]] == [wf_id]

    archived = client.delete(f"/v1/workflows/{wf_id}").json()
    assert archived["status"] == "archived"

    missing = client.get("/v1/workflows/wf_nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "WORKFLOW_NOT_FOUND"


def test_workflow_mode_isolation(client: TestClient) -> None:
    created = client.post(
        "/v1/workflows",
        json={"name": "W", "slug": "iso", "definition": {"steps": [_task("t")]}},
    ).json()
    assert client.get(f"/v1/workflows/{created['id']}", headers=auth(LIVE_KEY)).status_code == 404


# ------------------------------------------------------- DSL validation matrix
INVALID_DEFINITIONS: list[tuple[str, JsonDict]] = [
    ("201 steps", {"steps": [_task(f"t{i}") for i in range(201)]}),
    (
        "nesting deeper than 8",
        {
            "steps": [
                # 8 nested loops -> bodies at depth 9 break the limit
                {
                    "id": "l1",
                    "type": "loop",
                    "count": 1,
                    "body": [
                        {
                            "id": "l2",
                            "type": "loop",
                            "count": 1,
                            "body": [
                                {
                                    "id": "l3",
                                    "type": "loop",
                                    "count": 1,
                                    "body": [
                                        {
                                            "id": "l4",
                                            "type": "loop",
                                            "count": 1,
                                            "body": [
                                                {
                                                    "id": "l5",
                                                    "type": "loop",
                                                    "count": 1,
                                                    "body": [
                                                        {
                                                            "id": "l6",
                                                            "type": "loop",
                                                            "count": 1,
                                                            "body": [
                                                                {
                                                                    "id": "l7",
                                                                    "type": "loop",
                                                                    "count": 1,
                                                                    "body": [
                                                                        {
                                                                            "id": "l8",
                                                                            "type": "loop",
                                                                            "count": 1,
                                                                            "body": [_task("t")],
                                                                        }
                                                                    ],
                                                                }
                                                            ],
                                                        }
                                                    ],
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    ),
    (
        "17 parallel branches",
        {
            "steps": [
                {"id": "p", "type": "parallel", "branches": [[_task(f"b{i}")] for i in range(17)]}
            ]
        },
    ),
    (
        "human_approval inside parallel",
        {
            "steps": [
                {
                    "id": "p",
                    "type": "parallel",
                    "branches": [[{"id": "ha", "type": "human_approval"}], [_task("t")]],
                }
            ]
        },
    ),
    (
        "succeed inside parallel",
        {
            "steps": [
                {
                    "id": "p",
                    "type": "parallel",
                    "branches": [[{"id": "s", "type": "succeed"}]],
                }
            ]
        },
    ),
    (
        "retry max_attempts 21",
        {"steps": [{"id": "r", "type": "retry", "max_attempts": 21, "body": [_task("t")]}]},
    ),
    (
        "retry max_attempts missing",
        {"steps": [{"id": "r", "type": "retry", "body": [_task("t")]}]},
    ),
    ("save_as reserved", {"steps": [_task("t", save_as="inputs")]}),
    ("bad step id", {"steps": [_task("bad id!")]}),
    ("unknown step type", {"steps": [{"id": "x", "type": "teleport"}]}),
    (
        "unknown condition op",
        {"steps": [{"id": "a", "type": "assert", "condition": {"op": "regex", "left": "x"}}]},
    ),
    (
        "and with empty conditions",
        {"steps": [{"id": "a", "type": "assert", "condition": {"op": "and", "conditions": []}}]},
    ),
    (
        "binary op missing right",
        {"steps": [{"id": "a", "type": "assert", "condition": {"op": "eq", "left": 1}}]},
    ),
    (
        "loop with both count and while",
        {
            "steps": [
                {
                    "id": "l",
                    "type": "loop",
                    "count": 1,
                    "while": {"op": "truthy", "value": True},
                    "body": [_task("t")],
                }
            ]
        },
    ),
    (
        "loop with neither count nor while",
        {"steps": [{"id": "l", "type": "loop", "body": [_task("t")]}]},
    ),
    ("task without task text", {"steps": [{"id": "t", "type": "task"}]}),
    ("empty steps", {"steps": []}),
]


@pytest.mark.parametrize(
    ("label", "definition"), INVALID_DEFINITIONS, ids=[c[0] for c in INVALID_DEFINITIONS]
)
def test_invalid_definitions_422_on_create(
    client: TestClient, label: str, definition: JsonDict
) -> None:
    response = client.post(
        "/v1/workflows", json={"name": "Bad", "slug": "bad-wf", "definition": definition}
    )
    assert response.status_code == 422, label
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"], label


@pytest.mark.parametrize(
    ("label", "definition"), INVALID_DEFINITIONS[:3], ids=[c[0] for c in INVALID_DEFINITIONS[:3]]
)
def test_invalid_definitions_422_on_adhoc(
    client: TestClient, label: str, definition: JsonDict
) -> None:
    response = client.post("/v1/workflows/runs", json={"definition": definition})
    assert response.status_code == 422, label


def test_bad_slug_and_missing_definition(client: TestClient) -> None:
    bad_slug = client.post(
        "/v1/workflows",
        json={"name": "X", "slug": "Bad_Slug!", "definition": {"steps": [_task("t")]}},
    )
    assert bad_slug.status_code == 422
    no_def = client.post("/v1/workflows", json={"name": "X", "slug": "ok-slug"})
    assert no_def.status_code == 422
    adhoc_no_def = client.post("/v1/workflows/runs", json={})
    assert adhoc_no_def.status_code == 422


# ------------------------------------------------------------------ execution
def test_saved_run_with_templating_and_branching(client: TestClient) -> None:
    definition = {
        "steps": [
            _task("fetch", "Open order {{inputs.order_id}}; result contains PAID", save_as="inv"),
            {
                "id": "check",
                "type": "assert",
                "condition": {"op": "truthy", "value": "{{inv.passed}}"},
            },
            {
                "id": "branch",
                "type": "if",
                "condition": {"op": "contains", "left": "{{inv.result}}", "right": "PAID"},
                "then": [
                    {
                        "id": "ok",
                        "type": "succeed",
                        "output": {"state": "paid", "order": "{{inputs.order_id}}"},
                    }
                ],
                "else": [{"id": "no", "type": "fail", "message": "not paid"}],
            },
        ]
    }
    wf_id = client.post(
        "/v1/workflows", json={"name": "Reconcile", "slug": "reconcile", "definition": definition}
    ).json()["id"]
    run = client.post(f"/v1/workflows/{wf_id}/runs", json={"inputs": {"order_id": "ord_42"}}).json()
    assert run["object"] == "workflow.run"
    assert run["workflow_id"] == wf_id
    assert run["workflow_version"] == 1
    assert run["status"] == "succeeded"
    assert run["output"] == {"state": "paid", "order": "ord_42"}
    assert run["spent_cents"] == 5  # one task step; control flow is free


def test_else_branch_and_fail_step(client: TestClient) -> None:
    definition = {
        "steps": [
            {
                "id": "branch",
                "type": "if",
                "condition": {"op": "eq", "left": "{{inputs.x}}", "right": "nope"},
                "then": [{"id": "s", "type": "succeed"}],
                "else": [{"id": "f", "type": "fail", "message": "took the else branch"}],
            }
        ]
    }
    run = _run(client, definition, inputs={"x": "yes"})
    assert run["status"] == "failed"
    assert run["error"] == {"code": "WORKFLOW_FAILED", "message": "took the else branch"}


def test_assert_failure_fails_run(client: TestClient) -> None:
    definition = {
        "steps": [
            _task("t", "this will [fail]", save_as="t_out"),
            {
                "id": "a",
                "type": "assert",
                "condition": {"op": "truthy", "value": "{{t_out.passed}}"},
                "message": "task did not pass",
            },
        ]
    }
    run = _run(client, definition)
    assert run["status"] == "failed"
    assert run["error"] == {"code": "STEP_FAILED", "message": "task did not pass"}


def test_all_thirteen_condition_ops(client: TestClient) -> None:
    conditions: list[JsonDict] = [
        {"op": "eq", "left": "{{inputs.n}}", "right": 3},
        {"op": "ne", "left": "{{inputs.n}}", "right": 4},
        {"op": "lt", "left": "{{inputs.n}}", "right": 5},
        {"op": "gt", "left": "{{inputs.n}}", "right": 1},
        {"op": "lte", "left": "{{inputs.n}}", "right": 3},
        {"op": "gte", "left": "{{inputs.n}}", "right": 3},
        {"op": "contains", "left": "{{inputs.word}}", "right": "AID"},
        {"op": "truthy", "value": "{{inputs.word}}"},
        {"op": "falsy", "value": "{{inputs.missing}}"},
        {"op": "exists", "value": "{{inputs.word}}"},
        {
            "op": "and",
            "conditions": [{"op": "truthy", "value": 1}, {"op": "truthy", "value": "x"}],
        },
        {
            "op": "or",
            "conditions": [{"op": "falsy", "value": 1}, {"op": "truthy", "value": 1}],
        },
        {"op": "not", "condition": {"op": "exists", "value": "{{inputs.missing}}"}},
    ]
    steps: list[JsonDict] = [
        {"id": f"a{i}", "type": "assert", "condition": cond} for i, cond in enumerate(conditions)
    ]
    run = _run(client, {"steps": steps}, inputs={"n": 3, "word": "PAID"})
    assert run["status"] == "succeeded", run["error"]


def test_loop_count_and_iterations(client: TestClient) -> None:
    definition = {
        "steps": [{"id": "l", "type": "loop", "count": 3, "body": [_task("inner")]}],
        "output": {"iters": "{{vars.iteration}}"},
    }
    run = _run(client, definition)
    assert run["status"] == "succeeded"
    assert run["iterations_used"] == 3
    assert run["spent_cents"] == 15
    assert run["output"] == {"iters": 3}


def test_loop_while_condition(client: TestClient) -> None:
    definition = {
        "steps": [
            {
                "id": "l",
                "type": "loop",
                "while": {"op": "lt", "left": "{{vars.iteration}}", "right": 2},
                "body": [_task("inner")],
            }
        ]
    }
    run = _run(client, definition)
    assert run["status"] == "succeeded"
    assert run["iterations_used"] == 2


def test_parallel_branches_all_execute(client: TestClient) -> None:
    definition = {
        "steps": [
            {
                "id": "p",
                "type": "parallel",
                "branches": [[_task("a", save_as="ra")], [_task("b", save_as="rb")]],
            },
            {
                "id": "check",
                "type": "assert",
                "condition": {
                    "op": "and",
                    "conditions": [
                        {"op": "truthy", "value": "{{ra.passed}}"},
                        {"op": "truthy", "value": "{{rb.passed}}"},
                    ],
                },
            },
        ]
    }
    run = _run(client, definition)
    assert run["status"] == "succeeded"
    assert run["spent_cents"] == 10


def test_retry_succeeds_on_flaky_task(client: TestClient) -> None:
    definition = {
        "steps": [
            {
                "id": "r",
                "type": "retry",
                "max_attempts": 3,
                "body": [
                    _task("flaky", "unstable thing [flaky:2]", save_as="out"),
                    {
                        "id": "a",
                        "type": "assert",
                        "condition": {"op": "truthy", "value": "{{out.passed}}"},
                    },
                ],
            }
        ],
        "output": {"attempts": "{{vars.attempt}}"},
    }
    run = _run(client, definition)
    assert run["status"] == "succeeded"
    assert run["output"] == {"attempts": 2}
    assert run["spent_cents"] == 10  # the task ran twice


def test_retry_exhausted_fails(client: TestClient) -> None:
    definition = {
        "steps": [
            {
                "id": "r",
                "type": "retry",
                "max_attempts": 2,
                "body": [
                    _task("bad", "always [fail]", save_as="out"),
                    {
                        "id": "a",
                        "type": "assert",
                        "condition": {"op": "truthy", "value": "{{out.passed}}"},
                    },
                ],
            }
        ]
    }
    run = _run(client, definition)
    assert run["status"] == "failed"
    assert run["error"]["code"] == "STEP_FAILED"
    assert run["spent_cents"] == 10


# --------------------------------------------------------------------- guards
def test_budget_guard(client: TestClient) -> None:
    definition = {"steps": [_task("t1"), _task("t2")]}
    run = _run(client, definition, budget_cents=9)
    assert run["status"] == "failed"
    assert run["error"]["code"] == "GUARD_EXCEEDED"
    assert "budget_cents" in run["error"]["message"]
    assert run["spent_cents"] == 5  # only the first step accrued


def test_max_iterations_guard(client: TestClient) -> None:
    definition = {"steps": [{"id": "l", "type": "loop", "count": 5, "body": [_task("t")]}]}
    run = _run(client, definition, max_iterations=2)
    assert run["status"] == "failed"
    assert run["error"]["code"] == "GUARD_EXCEEDED"
    assert run["iterations_used"] == 3  # the third iteration tripped the guard


def test_deadline_guard(client: TestClient) -> None:
    # Each task step advances the frozen clock by 30s; the second breaches 30s.
    definition = {"steps": [_task("t1"), _task("t2")]}
    run = _run(client, definition, deadline_seconds=30)
    assert run["status"] == "failed"
    assert run["error"]["code"] == "GUARD_EXCEEDED"
    assert "deadline" in run["error"]["message"]


# ----------------------------------------------------------- human approvals
def test_human_approval_approve_path(client: TestClient) -> None:
    definition = {
        "steps": [
            {"id": "gate", "type": "human_approval", "message": "ship {{inputs.thing}}?"},
            _task("after"),
        ]
    }
    run = _run(client, definition, inputs={"thing": "v2"})
    assert run["status"] == "awaiting_human"
    assert run["awaiting_step_id"] == "gate"
    assert run["awaiting_human_reason"] == "ship v2?"

    resumed = client.post(
        f"/v1/workflows/runs/{run['id']}/resume", json={"approved": True, "note": "lgtm"}
    ).json()
    assert resumed["status"] == "succeeded"
    assert resumed["spent_cents"] == 5


def test_human_approval_reject_fails_step(client: TestClient) -> None:
    definition = {"steps": [{"id": "gate", "type": "human_approval"}, _task("after")]}
    run = _run(client, definition)
    rejected = client.post(
        f"/v1/workflows/runs/{run['id']}/resume", json={"approved": False, "note": "nope"}
    ).json()
    assert rejected["status"] == "failed"
    assert rejected["error"]["code"] == "STEP_FAILED"
    assert "nope" in rejected["error"]["message"]
    assert rejected["spent_cents"] == 0  # the task after the gate never ran


def test_rejected_approval_inside_retry_pauses_again(client: TestClient) -> None:
    definition = {
        "steps": [
            {
                "id": "r",
                "type": "retry",
                "max_attempts": 2,
                "body": [{"id": "gate", "type": "human_approval"}],
            }
        ]
    }
    run = _run(client, definition)
    run_id = run["id"]
    first = client.post(f"/v1/workflows/runs/{run_id}/resume", json={"approved": False}).json()
    assert first["status"] == "awaiting_human"  # retry re-asks
    second = client.post(f"/v1/workflows/runs/{run_id}/resume", json={"approved": True}).json()
    assert second["status"] == "succeeded"


def test_resume_conflicts_and_cancel(client: TestClient) -> None:
    finished = _run(client, {"steps": [_task("t")]})
    response = client.post(f"/v1/workflows/runs/{finished['id']}/resume", json={"approved": True})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RESUME_CONFLICT"

    paused = _run(client, {"steps": [{"id": "g", "type": "human_approval"}]})
    cancelled = client.post(f"/v1/workflows/runs/{paused['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    again = client.post(f"/v1/workflows/runs/{paused['id']}/cancel")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "INVALID_STATE"
    resume_cancelled = client.post(
        f"/v1/workflows/runs/{paused['id']}/resume", json={"approved": True}
    )
    assert resume_cancelled.status_code == 409


def test_resume_requires_approved_boolean(client: TestClient) -> None:
    paused = _run(client, {"steps": [{"id": "g", "type": "human_approval"}]})
    response = client.post(f"/v1/workflows/runs/{paused['id']}/resume", json={})
    assert response.status_code == 422


# ------------------------------------------------------- billing / idempotency
def test_live_key_workflow_billing(client: TestClient) -> None:
    definition = {"steps": [_task("t1"), _task("t2", cua_version="v1")]}
    response = client.post(
        "/v1/workflows/runs", json={"definition": definition}, headers=auth(LIVE_KEY)
    )
    run = response.json()
    assert run["spent_cents"] == 13  # 5 (v3) + 8 (v1)
    config = client.get("/__mock__/config").json()
    assert config["wallet_balance_cents"] == 10_000 - 13


def test_workflow_run_idempotency(client: TestClient) -> None:
    body = {"definition": {"steps": [_task("t")]}}
    headers = {"Idempotency-Key": "wf-key-1"}
    first = client.post("/v1/workflows/runs", json=body, headers=headers)
    second = client.post("/v1/workflows/runs", json=body, headers=headers)
    assert second.headers["X-Coasty-Idempotent-Replay"] == "true"
    assert second.json()["id"] == first.json()["id"]
    conflict = client.post(
        "/v1/workflows/runs",
        json={"definition": {"steps": [_task("other")]}},
        headers=headers,
    )
    assert conflict.status_code == 422
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_workflow_run_list_and_get(client: TestClient) -> None:
    wf_id = client.post(
        "/v1/workflows",
        json={"name": "L", "slug": "list-wf", "definition": {"steps": [_task("t")]}},
    ).json()["id"]
    run = client.post(f"/v1/workflows/{wf_id}/runs", json={}).json()
    listed = client.get(f"/v1/workflows/runs?workflow_id={wf_id}").json()
    assert [r["id"] for r in listed["data"]] == [run["id"]]
    got = client.get(f"/v1/workflows/runs/{run['id']}").json()
    assert got["status"] == "succeeded"
    assert got["webhook_secret"] is None
    missing = client.get("/v1/workflows/runs/wfr_nope")
    assert missing.status_code == 404


def test_workflow_webhooks_fire_on_pause_and_terminal(client: TestClient) -> None:
    definition = {"steps": [{"id": "g", "type": "human_approval"}]}
    run = _run(client, definition, webhook_url="https://example.com/wf-hook")
    assert str(run["webhook_secret"]).startswith("whsec_")
    client.post(f"/v1/workflows/runs/{run['id']}/resume", json={"approved": True})
    deliveries = client.get("/__mock__/webhooks").json()["deliveries"]
    assert [d["event"] for d in deliveries] == [
        "workflow_run.awaiting_human",
        "workflow_run.succeeded",
    ]
