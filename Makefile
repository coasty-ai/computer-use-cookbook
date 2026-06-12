# Coasty Computer Use API Cookbook — root Makefile.
# Requires GNU make (Git Bash/WSL on Windows). Every target is a thin wrapper
# over the per-track commands listed in README.md; run those directly if you
# don't have make.
#
#   make test lint typecheck     # everything, no network, no spend
#   make mock                    # offline mock server on 127.0.0.1:8787
#   make run-ex05 ARGS="--machine-id mch_test_demo --task 'invoice' --events"

ifeq ($(OS),Windows_NT)
PY      := .venv/Scripts/python.exe
else
PY      := .venv/bin/python
endif

.PHONY: test lint typecheck fmt install mock \
        test-python test-typescript test-go test-mock test-curl \
        lint-python lint-typescript lint-go lint-mock \
        typecheck-python typecheck-typescript typecheck-go typecheck-mock

test: test-python test-typescript test-go test-mock test-curl
lint: lint-python lint-typescript lint-go lint-mock
typecheck: typecheck-python typecheck-typescript typecheck-go typecheck-mock

# ── per-track install ────────────────────────────────────────────────────────
install:
	cd python && python -m venv .venv && $(PY) -m pip install -e ".[dev,local]"
	cd mock && python -m venv .venv && $(PY) -m pip install -e ".[dev]"
	cd typescript && npm ci

# ── tests ────────────────────────────────────────────────────────────────────
test-python:
	$(MAKE) -C python test
test-typescript:
	cd typescript && npm test
test-go:
	$(MAKE) -C go test
test-mock:
	$(MAKE) -C mock test
test-curl:
	cd curl && bash tests/smoke.sh

# ── lint / format check ──────────────────────────────────────────────────────
lint-python:
	$(MAKE) -C python lint
lint-typescript:
	cd typescript && npm run lint
lint-go:
	$(MAKE) -C go lint
lint-mock:
	$(MAKE) -C mock lint

# ── typecheck ────────────────────────────────────────────────────────────────
typecheck-python:
	$(MAKE) -C python typecheck
typecheck-typescript:
	cd typescript && npm run typecheck
typecheck-go:
	$(MAKE) -C go typecheck
typecheck-mock:
	$(MAKE) -C mock typecheck

# ── format (write) ───────────────────────────────────────────────────────────
fmt:
	$(MAKE) -C python fmt
	cd typescript && npm run fmt
	$(MAKE) -C go fmt
	$(MAKE) -C mock fmt

# ── the offline mock server ──────────────────────────────────────────────────
mock:
	$(MAKE) -C mock serve

# ── run examples: make run-ex01 .. run-ex10 (Python track; ARGS passthrough) ─
# TypeScript: make run-ts-ex01 .. run-ts-ex10
RUN_PY = cd python && $(PY)
run-ex01: ; $(RUN_PY) examples/ex01_local_predict_loop.py $(ARGS)
run-ex02: ; $(RUN_PY) examples/ex02_grounding.py $(ARGS)
run-ex03: ; $(RUN_PY) examples/ex03_sessions.py $(ARGS)
run-ex04: ; $(RUN_PY) examples/ex04_parse.py $(ARGS)
run-ex05: ; $(RUN_PY) examples/ex05_runs.py $(ARGS)
run-ex06: ; $(RUN_PY) examples/ex06_webhook_server.py $(ARGS)
run-ex07: ; $(RUN_PY) examples/ex07_workflows.py $(ARGS)
run-ex08: ; $(RUN_PY) examples/ex08_machines.py $(ARGS)
run-ex09: ; $(RUN_PY) examples/ex09_error_handling.py $(ARGS)
run-ex10: ; $(RUN_PY) examples/ex10_cost_helper.py $(ARGS)

run-ts-%: ; cd typescript && npx tsx src/examples/$$(ls src/examples | grep '^$*-') $(ARGS)
