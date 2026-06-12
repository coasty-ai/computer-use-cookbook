# Python track -- run from python/ (Git Bash / WSL / make on Windows).
# Uses the venv interpreter directly so no activation is needed.

PY := .venv/Scripts/python.exe

.PHONY: test lint typecheck fmt install

install:
	$(PY) -m pip install -e ".[dev,local]"

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests examples
	$(PY) -m black --check src tests examples

typecheck:
	$(PY) -m mypy src

fmt:
	$(PY) -m black src tests examples
	$(PY) -m ruff check --fix src tests examples
