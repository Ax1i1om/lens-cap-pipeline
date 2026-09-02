.PHONY: bootstrap test smoke compile lint doctor

# ``bootstrap`` creates a venv inside this checkout; it never installs
# packages into the system interpreter.  Windows users can run the same
# Python script directly when GNU Make is unavailable.
PYTHON ?= python3
LENS_CAP ?= .venv/bin/lens-cap

bootstrap:
	$(PYTHON) scripts/bootstrap.py --dev

test:
	$(PYTHON) -m pytest

smoke:
	$(PYTHON) scripts/smoke.py

compile:
	$(PYTHON) -m compileall -q lens_cap_pipeline

lint:
	$(PYTHON) -m ruff check lens_cap_pipeline tests scripts

doctor:
	$(LENS_CAP) doctor --json
