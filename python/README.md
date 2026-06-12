# Coasty Python client (cookbook shared library)

Thin, fully typed synchronous wrapper around the Coasty Computer Use API
(`https://coasty.ai/v1`). Every Python example in this cookbook imports it.

## Install

```bash
cd python
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"        # tests/lint/typecheck
.venv/Scripts/python.exe -m pip install -e ".[dev,local]"  # + pyautogui/mss/pillow for local automation
```

Configuration comes from the repo-root `.env` (loaded automatically, never
logged) or the environment: `COASTY_API_KEY`, `COASTY_BASE_URL` (default
`https://coasty.ai/v1`), `COASTY_CONFIRM_SPEND=1`. Sandbox keys
(`sk-coasty-test-...`) never bill.

## Use

```python
from coasty import CoastyClient

with CoastyClient() as client:           # key from COASTY_API_KEY / .env
    result = client.predict(screenshot_b64, "Click the login button")
    print(result.request_id, result.credits_charged)
    for action in result.data["actions"]:
        print(action["action_type"], action["params"])
```

Modules: `client` (all endpoints + retries + SSE), `errors` (typed exception
tree incl. `request_id`), `types` (TypedDicts/Literals), `sse` (parser +
`Last-Event-ID` reconnect), `webhooks` (`verify_signature`), `cost` (full
pricing table estimator), `dsl` (workflow builders + `validate`), `executor`
(defensive local action executor; `raw` is never executed), `env`.

## Commands

```bash
.venv/Scripts/python.exe -m pytest -q                       # tests (offline, respx-mocked)
.venv/Scripts/python.exe -m mypy src                        # mypy --strict per pyproject
.venv/Scripts/python.exe -m ruff check src tests examples   # lint
.venv/Scripts/python.exe -m black --check src tests examples
```

or `make test lint typecheck fmt` from `python/` (Git Bash/WSL).
