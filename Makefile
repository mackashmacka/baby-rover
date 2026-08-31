# Baby Rover — host-side test harness.
#
# Every target works from a bare checkout. The first `make test` builds a
# repo-local .venv (gitignored) and installs tests/requirements.txt into it;
# nothing is installed system-wide.
#
#   make test      fast suite, no hardware, WITH the 80% line-coverage gate
#   make test-hw   opt-in: needs a real Pico and the FX2 analyser plugged in
#   make lint      ruff over the host Python
#   make coverage  HTML coverage report in htmlcov/
#   make doctor    what is installed, what is plugged in, what is missing
#
# Override the interpreter or venv location if needed:
#   make test PY=python3.12 VENV=/tmp/rover-venv

PY      ?= python3
VENV    ?= .venv
BIN     := $(VENV)/bin
PYTEST  := $(BIN)/pytest
COVERAGE:= $(BIN)/coverage
RUFF    := $(BIN)/ruff
SRC     := tools/rover_bench
REQ     := tests/requirements.txt
STAMP   := $(VENV)/.requirements-stamp

.DEFAULT_GOAL := help
.PHONY: help venv test test-hw lint coverage doctor clean

help:
	@echo "Baby Rover test harness"
	@echo ""
	@echo "  make test      fast suite, no hardware, with the 80% line-coverage gate"
	@echo "  make test-hw   hardware suite (needs a Pico + FX2 analyser attached)"
	@echo "  make lint      ruff over tools/ pi/ tests/"
	@echo "  make coverage  HTML coverage report -> htmlcov/index.html"
	@echo "  make doctor    environment and bench diagnostics"
	@echo "  make venv      create $(VENV) and install $(REQ)"
	@echo "  make clean     remove caches, .coverage and htmlcov/"

# --- environment ------------------------------------------------------------
# The stamp file means we only pip-install when requirements.txt actually
# changes, so `make test` in a loop stays fast and works offline afterwards.
venv: $(STAMP)

$(STAMP): $(REQ)
	@test -x $(BIN)/python || $(PY) -m venv $(VENV)
	@$(BIN)/python -m pip install --quiet --disable-pip-version-check --upgrade pip
	@$(BIN)/python -m pip install --quiet --disable-pip-version-check -r $(REQ)
	@touch $(STAMP)
	@echo "venv ready: $(VENV)"

# --- the default gate -------------------------------------------------------
# Coverage is measured over $(SRC). Until the other streams land that package
# there is nothing to measure, so the gate is announced-but-skipped rather than
# failing a bare checkout on an empty source tree. It switches itself on the
# moment tools/rover_bench exists — no edit to this file required.
test: venv
	@if [ -d "$(SRC)" ]; then \
	    echo "--- pytest (no hardware) + coverage gate: 80% of $(SRC) ---"; \
	    $(COVERAGE) run --rcfile=.coveragerc -m pytest $(PYTEST_ARGS) && \
	    $(COVERAGE) report --rcfile=.coveragerc; \
	else \
	    echo "!!! $(SRC) does not exist yet -- running tests WITHOUT the coverage gate."; \
	    echo "!!! The 80% gate turns itself on as soon as that package lands."; \
	    $(PYTEST) $(PYTEST_ARGS); \
	fi

# --- the bench suite --------------------------------------------------------
# -m hardware overrides the `-m "not hardware"` in pytest.ini (last -m wins).
# No coverage gate here: these tests exist to exercise real silicon, not lines.
test-hw: venv
	@echo "--- HARDWARE SUITE ---"
	@echo "Requires: Pico 2 W flashed and on USB, TB6612 wired per docs/WIRING.md,"
	@echo "          FX2 analyser (0925:3881) plugged in with the udev rule applied."
	@echo "Safety:   never probe AO1/AO2/BO1/BO2. Encoder supply is 3.3 V, never 5 V."
	@echo ""
	$(PYTEST) -m hardware $(PYTEST_ARGS)

# --- lint -------------------------------------------------------------------
# The rule set is PINNED, not left to ruff's default, for two reasons:
#   * ruff's default selection changes between releases, so an unpinned lint
#     target reports different things on two machines and stops being trusted;
#   * E4 (imports), E9 (syntax/runtime) and F (pyflakes) are BUGS. E1/E2/E3/E7
#     are whitespace and naming opinions, and this project is explicitly
#     "shitty code, lots of tests" - a style avalanche would bury the one
#     finding that matters.
# Override for a stricter pass:  make lint RUFF_SELECT=E,F,B,I
RUFF_SELECT ?= E4,E9,F

lint: venv
	@dirs=""; for d in tools pi tests; do \
	    [ -n "$$(find $$d -name '*.py' -print -quit 2>/dev/null)" ] && dirs="$$dirs $$d"; \
	done; \
	if [ -z "$$dirs" ]; then echo "no Python to lint yet"; else \
	    echo "--- ruff check --select $(RUFF_SELECT)$$dirs ---"; \
	    $(RUFF) check --select $(RUFF_SELECT) $$dirs; \
	fi

# --- html coverage ----------------------------------------------------------
# Deliberately does NOT apply the fail_under gate: this target is for reading,
# `make test` is for gating.
coverage: venv
	@if [ ! -d "$(SRC)" ]; then echo "$(SRC) does not exist yet - nothing to report on."; exit 0; fi
	$(COVERAGE) run --rcfile=.coveragerc -m pytest $(PYTEST_ARGS) || true
	$(COVERAGE) html --rcfile=.coveragerc --fail-under=0
	@echo "open htmlcov/index.html"

# --- diagnostics ------------------------------------------------------------
doctor:
	@PY="$(PY)" VENV="$(VENV)" SRC="$(SRC)" sh tests/doctor.sh

clean:
	rm -rf .pytest_cache htmlcov .coverage .ruff_cache
	find tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "clean (the venv is left alone; rm -rf $(VENV) to drop it too)"
