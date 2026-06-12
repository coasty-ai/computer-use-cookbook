# coasty-mock — run from mock/ with GNU make (Git Bash / WSL on Windows).
# Without make, run the PY commands directly.

PY := .venv/Scripts/python.exe
ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python.exe
else
PY := .venv/bin/python
endif

.PHONY: install test lint fmt typecheck check serve

install:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests
	$(PY) -m black --check src tests

fmt:
	$(PY) -m black src tests
	$(PY) -m ruff check --fix src tests

typecheck:
	$(PY) -m mypy

check: lint typecheck test

serve:
	$(PY) -m coasty_mock --port 8787
