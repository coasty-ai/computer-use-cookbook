# Python track -- run from python/ (Git Bash / WSL / make on Windows).
# Uses the venv interpreter directly so no activation is needed.

ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python.exe
else
PY := .venv/bin/python
endif

.PHONY: test lint typecheck fmt install

install:
	$(PY) -m pip install -e ".[dev,local]"

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests examples
	$(PY) -m black --check src tests examples

typecheck:
	$(PY) -m mypy

fmt:
	$(PY) -m black src tests examples
	$(PY) -m ruff check --fix src tests examples
