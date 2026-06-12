# Go track — test / lint / typecheck.
#
# Windows note: this repo uses a portable Go toolchain at
#   %LOCALAPPDATA%\coasty-tools\go\bin\go.exe
# Run make from Git Bash / WSL, or use the direct PowerShell commands from
# go/README.md. Override the toolchain with `make GO=go GOFMT=gofmt ...`
# when Go is on your PATH.

ifeq ($(strip $(LOCALAPPDATA)),)
GO    ?= go
GOFMT ?= gofmt
else
GO    ?= $(subst \,/,$(LOCALAPPDATA))/coasty-tools/go/bin/go.exe
GOFMT ?= $(subst \,/,$(LOCALAPPDATA))/coasty-tools/go/bin/gofmt.exe
endif

.PHONY: all test lint typecheck fmt

all: lint typecheck test

test:
	$(GO) test ./...

# lint = gofmt -l (must print nothing) + go vet.
lint:
	@unformatted="$$($(GOFMT) -l .)"; \
	if [ -n "$$unformatted" ]; then \
		echo "gofmt: the following files need formatting:"; \
		echo "$$unformatted"; \
		exit 1; \
	fi
	$(GO) vet ./...

# Go has no separate typechecker; go vet compiles and typechecks everything.
typecheck:
	$(GO) vet ./...

fmt:
	$(GOFMT) -w .
