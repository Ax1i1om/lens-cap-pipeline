.PHONY: bootstrap test smoke smoke-rehouse build-3mf compile lint doctor skills-check skills-sync

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

# Clean-room rehearsal of the checked-in rehoused-lens fixture.  The portable
# default does not require desktop tools; pass `--bambu auto --require-external`
# manually on a host with OpenSCAD/Bambu when the native/sliced 3MF gates are
# also to be exercised.
smoke-rehouse:
	$(PYTHON) scripts/smoke_rehouse.py --bambu never --json

# One-command native 3MF bridge. A job path must be supplied explicitly:
#   make build-3mf JOB=jobs/name/job.toml
build-3mf:
	@test -n "$(JOB)" || (echo "usage: make build-3mf JOB=jobs/name/job.toml" >&2; exit 2)
	$(PYTHON) scripts/build_3mf.py "$(JOB)" --force --json

compile:
	$(PYTHON) -m compileall -q lens_cap_pipeline

lint:
	$(PYTHON) -m ruff check lens_cap_pipeline tests scripts tools/3mf_adapter

doctor:
	$(LENS_CAP) doctor --json

# Skill files are companion assets rather than wheel runtime dependencies.
# Keep the default target read-only; pass an explicit destination and
# ``--apply`` to ``scripts/install_skills.py`` when installation is intended.
skills-check:
	$(PYTHON) scripts/install_skills.py check --json

skills-sync:
	$(PYTHON) scripts/install_skills.py sync --dest .agents/skills
