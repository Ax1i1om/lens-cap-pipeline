.PHONY: bootstrap test smoke smoke-rehouse rehearse-user-agent route-smoke smoke-all acceptance build-3mf compile lint doctor skills-check skills-sync

# ``bootstrap`` creates a venv inside this checkout; it never installs
# packages into the system interpreter.  Windows users can run the same
# Python script directly when GNU Make is unavailable.
PYTHON ?= python3
LENS_CAP ?= .venv/bin/lens-cap
SMOKE_FIXTURE ?= examples/fixtures/helios-44-2-rehouse
REHEARSAL ?= examples/rehearsals/helios-44-2-rehouse-clean-room.json

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
	$(PYTHON) scripts/smoke_rehouse.py --fixture "$(SMOKE_FIXTURE)" --bambu never --json

# Conversation-boundary rehearsal: validates a no-history user/agent handoff,
# then runs the same isolated production route as smoke-rehouse.
rehearse-user-agent:
	$(PYTHON) scripts/rehearse_user_agent.py "$(REHEARSAL)" --bambu never --json

# Manifest-driven semantic routing check for older/host-specific Skill loaders.
route-smoke:
	$(PYTHON) scripts/resolve_skill_route.py \
		"为适马 28–70mm F2.8 设计圆形镜头盖正面并输出 3MF"

# Portable release smoke gate: route resolution plus both independent
# clean-room fixtures and their transcripts.  It deliberately skips desktop
# slicers; use the documented ``--require-external`` commands for OpenSCAD /
# Bambu verification on a host that has them installed.
smoke-all: route-smoke
	$(PYTHON) scripts/smoke_rehouse.py --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v2 --bambu never --json
	$(PYTHON) scripts/smoke_rehouse.py --fixture examples/fixtures/mamiya-sekor-c-80-f1-9-rehouse --bambu never --json
	$(PYTHON) scripts/rehearse_user_agent.py examples/rehearsals/helios-44-2-rehouse-clean-room.json --bambu never --json
	$(PYTHON) scripts/rehearse_user_agent.py examples/rehearsals/mamiya-sekor-c-80-f1-9-rehouse-clean-room.json --bambu never --json

acceptance: smoke-all

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
