# Python examples

Placeholder: examples ex01..ex10 land here (see ../../PLAN.md). Each example
is a pure, testable core function plus a thin CLI `main`, imports the shared
`coasty` client from `../src/coasty`, prints an itemized cost estimate first,
and refuses to spend unless `--confirm` or `COASTY_CONFIRM_SPEND=1` is set.

Shared pytest fixtures for the examples live in `../tests/conftest.py`
(`coasty_env`, `client`, `respx_router`, payload factories, `sse_body`).
