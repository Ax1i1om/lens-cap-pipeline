# Clean-room rehearsal and release evidence

This document is the acceptance record for the public Alpha route. It answers
one question: can a new Codex task, with no previous conversation history,
take an approved named-lens artwork through the repository production route
and finish at a checked 3MF? The file runner can prove the latter; it cannot
emulate every host's Skill discovery cache. After installing or syncing Skills,
start a new Codex task/reload the host, and use an explicit `$lens-cap-*`
invocation if automatic matching has not refreshed.

## Latest audit snapshot

The committed Alpha snapshot was replayed from a fresh Git archive and a new
Python virtual environment on the development host. The repository suite
collected **150 tests**, all passed; Ruff, both official Skill validators, and
the source/mirror hash check passed. The fresh ImageGen Helios-44-2 REHOUSE
fixture then passed at 77, 82, and 95 mm with the native one-piece OpenSCAD
route, same-canvas projection audits, and default integrated friction ribs. The
independent Mamiya-Sekor C 80mm F1.9 fixture passed at 77, 85, and 95 mm,
including a 1.5 mm foam-lined 85 mm adapter case. Both structured clean-room
user/agent transcripts passed, and the exact `bin/lens-cap-3mf` endpoint passed
from an arbitrary working directory and from an extracted source distribution.
With the local Bambu A1 mini 0.2 mm profiles, both 95 mm optional slices
produced non-empty-G-code 3MFs and passed Core-package verification.
The smoke gate also rejects an unapproved raster handoff, a missing
source-backed culture/rehouse anchor, or a prompt/artwork path outside the
fixture.

This is a reproducibility result for the declared host/tool matrix, not a
promise that every Codex host has the same Skill catalog, ImageGen save API,
OpenSCAD installation, slicer profiles, or physical fit. A fresh ephemeral
Codex CLI blind prompt for “Sigma 28–70mm F2.8 + circular image + printable
model” selected `$lens-cap-imagegen` → `$lens-cap-production` and rejected
parallel generic design Skills; hosts with a stale catalog still need the
explicit invocation documented below.

## Simulated new-user interaction

The rehearsal deliberately begins with an already approved raster and brief.
That is the reproducibility boundary: the provider-dependent generation and
human approval happen before the file runner, while every downstream byte and
hash gate is exercised in a clean directory. It therefore must not be read as
a claim that every Codex host can automatically save an ImageGen attachment.

Use a fresh task and provide only a lens identity and the requested endpoint:

> Design a circular lens-cap front for the Mamiya-Sekor C 80mm F1.9 REHOUSE;
> make 80 the first read and F1.9 the second, then deliver a 3MF. The measured
> mating diameter is 85 mm: an 80 mm ring plus a 2.5 mm radial adapter wall.
> Add 1.5 mm uncompressed foam, keep the default inner-wall friction ribs,
> and use a 0.2 mm nozzle.

The expected agent behaviour is deterministic and short:

1. Route the request exclusively to lens-cap-imagegen for the concept stage;
   suppress generic graphic/CAD/design Skills. Verify the exact lens variant,
   research one brand/culture or qualified REHOUSE anchor, and record facts,
   sourced associations, folklore, and visual metaphor separately.
2. Make 80 the dominant text and F1.9 the second read. Generate a complete
   circle in black/charcoal/gray/ivory flat linocut or letterpress forms. The
   approved raster is frozen with a prompt hash and artwork hash; production
   never redraws or OCR-retypes it.
3. Hand the approved master sequentially to lens-cap-production. For a fitted
   cap, collect the three physical inputs as one grouped intake: actual
   mating diameter, foam plan plus uncompressed thickness, and rib preference.
   Do not ask for a second relief diameter or a structure choice. Ribs are on
   unless the user explicitly requests a smooth wall.
4. Run the bridge as the explicit 3MF endpoint:

   ```sh
   ./bin/lens-cap-3mf jobs/name/job.toml --force --json
   ```

   The bridge reruns the public build, exports current relief STLs, runs the
   same-canvas projection audit, exports one integrated OpenSCAD Manifold 3MF,
   and verifies the 3MF Core package. A printer-specific file is a separate,
   explicit `--bambu slice` request with three local profiles.

If any required external program is unavailable, the response must say
`UNVERIFIABLE` and stop at the last verified stage. A SCAD file or handoff JSON
must never be described as a 3MF.

The routing assertions are intentionally split: `tests/test_skill_routing.py`
checks the repository contract and manifest, while a real Codex host must
still confirm that its catalog has loaded the synced files. This is a host
integration prerequisite, not evidence that a generic design Skill may run in
parallel.

The manifest-driven `scripts/resolve_skill_route.py` is the optional bridge for
older hosts that cannot evaluate semantic descriptions. The rehearsal invokes
it against the first user turn, so a transcript cannot claim the dedicated
sequence while the checked-in resolver would miss the cap/front surface.

### Replay the interaction gate

For a reusable, host-neutral rehearsal of the *conversation* boundary, use the
structured transcript runner. It checks the first natural-language request,
the exclusive Skill sequence, the one grouped physical intake, and the values
written to the selected job TOML before invoking `smoke_rehouse.py` in its own
temporary fixture:

```sh
python3 scripts/rehearse_user_agent.py \
  examples/rehearsals/helios-44-2-rehouse-clean-room.json \
  --bambu never --json
```

The checked-in scenario is intentionally a four-turn “new user” exchange:
the first user turn names the lens and asks for a circular cap/3MF, the agent
declares the two dedicated Skills, the next user turn answers diameter/foam/
ribs together, and the final agent turn records the bridge endpoint. The
runner requires `conversation_history = none`, an isolated temporary file
system, no generic design Skill in parallel, and exactly these canonical
questions (in this order):

```text
mating_outside_diameter_mm
foam_liner_plan_and_uncompressed_thickness_mm
friction_rib_preference
```

Use another JSON transcript with the same schema for a different lens or
adapter envelope. The `fixture.primary_job` values must match the answered
diameter, foam decision/thickness, and rib profile; this catches a conversation
that asked the right question but silently dropped it before production.
`--artifact-dir PATH --force-artifacts` deliberately retains the new native
(and, when requested, Bambu) 3MF snapshots. The transcript runner does not
call an image provider or pretend that a generated image can be reproduced
byte-for-byte: the fixture’s approved artwork/hash remains the human approval
boundary.

The repository also includes
`examples/rehearsals/mamiya-sekor-c-80-f1-9-rehouse-clean-room.json`. It repeats
the same clean-room exchange with a different lens identity and an 85 mm
envelope (`80 mm nominal ring + 2.5 mm radial wall`) plus 1.5 mm uncompressed
foam. Running both scenarios is a useful cross-job leakage check: Helios text,
M42, T*, and other maker-specific marks must not carry into the Mamiya brief.

## Matrix exercised on the development host

The checked-in fixtures use generated, provider-neutral artwork and no copied
user 3MF. Each test includes an explicit adapter-envelope calculation so the
radial wall is not accidentally treated as a filter-thread diameter.

| fixture | approved visual identity | adapter envelope | mating diameter | liner | retained output |
| --- | --- | ---: | ---: | --- | --- |
| Helios-44-2 REHOUSE (baseline) | 58 / F2 | 72 + 2×2.5 mm | 77 mm | none | native 3MF |
| Helios-44-2 REHOUSE (baseline) | 58 / F2 | 77 + 2×2.5 mm | 82 mm | none | native 3MF |
| Helios-44-2 REHOUSE (baseline) | 58 / F2 | nominal 95 mm PL front | 95 mm | none | native + Bambu slice |
| Helios-44-2 REHOUSE (fresh ImageGen v2) | 58 / F2 | 72 + 2×2.5 mm | 77 mm | none | native 3MF |
| Helios-44-2 REHOUSE (fresh ImageGen v2) | 58 / F2 | 77 + 2×2.5 mm | 82 mm | none | native 3MF |
| Helios-44-2 REHOUSE (fresh ImageGen v2) | 58 / F2 | nominal 95 mm PL front | 95 mm | none | native + Bambu slice |
| Mamiya-Sekor C REHOUSE | 80 / F1.9 | 72 + 2×2.5 mm | 77 mm | 1.5 mm | native 3MF |
| Mamiya-Sekor C REHOUSE | 80 / F1.9 | 80 + 2×2.5 mm | 85 mm | 1.5 mm | native 3MF |
| Mamiya-Sekor C REHOUSE | 80 / F1.9 | nominal 95 mm PL front | 95 mm | 1.5 mm | native + Bambu slice |

The 77/82/95 and 77/85/95 job TOMLs, approved masters, prompt records,
briefs, and verified 3MF sidecars live under:

- `examples/fixtures/helios-44-2-rehouse/`
- `examples/fixtures/helios-44-2-rehouse-imagegen-v2/`
- `examples/fixtures/mamiya-sekor-c-80-f1-9-rehouse/`

The native 3MF files are unsliced Core packages. The retained Bambu files
contain non-empty G-code and are snapshots of the recorded A1 mini/0.2 mm
profile; Bambu UUIDs, timestamps, and G-code metadata may vary by release.

## Rehearse from a clean checkout

The runner copies only `design-brief.json`, `prompt.txt`, `art/master.png`, and
`jobs/*/job.toml` into a new temporary directory. It does not copy `out/`, old
STL/3MF files, or any conversation state:

```sh
python3 scripts/smoke_rehouse.py --bambu never --json
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/mamiya-sekor-c-80-f1-9-rehouse \
  --bambu never --json
```

On a host with OpenSCAD (Manifold backend) and Bambu Studio profiles, run the
full external gates and retain a run for inspection:

```sh
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/mamiya-sekor-c-80-f1-9-rehouse \
  --bambu auto --require-external --keep-workdir --json
```

To exercise the exact one-command endpoint in that isolated copy:

```sh
./bin/lens-cap-3mf /path/to/clean/fixture/jobs/85mm/job.toml \
  --force --json
```

Use `--artifact-dir examples/fixtures/<fixture>/artifacts
--force-artifacts` only when deliberately refreshing a checked-in snapshot.

## What “pass” means

The machine-readable report separates four claims:

- `passed`: source/config hashes, palette roles, mask coverage, SVG canvas,
  model parameters, STL projection, 3MF package/XML/mesh indices, and (when
  requested) non-empty G-code all satisfy their declared gates;
- `unverifiable`: an external executable, printer profile, or physical coupon
  is absent; no stronger claim is made;
- `failed`: a required input or invariant disagrees, so downstream release is
  blocked;
- `fit_status = unverifiable_until_coupon_measurement`: a physical lens,
  foam batch, ribs, printer, and material still need a short same-material
  fit-ring print and measurement.

The artwork contract is style/specification equivalence, not byte-identical
image generation. Once a user approves a raster, its hash makes the boundary
deterministic; all later stages preserve its coordinates and text hierarchy.

## Cross-host checklist

1. Install Python 3.11+ with `scripts/bootstrap.py --dev` (or the locked uv
   mode for dependency-pinned comparisons).
2. Check and, when needed, synchronise the two companion Skills with
   `scripts/install_skills.py`; inspect the `.lens-cap-skills.json` receipt.
3. Install OpenSCAD with Manifold for a native one-piece 3MF. The bridge also
   accepts an explicit `--openscad` path or the job `[print]` executable field;
   explicit values are authoritative and do not silently fall back.
4. Add Bambu machine/process/filament profiles only for a sliced printer
   project. The repository does not log into MakerWorld or ChromaCanvas.
5. Measure the real cylindrical surface the cap grips, including adapter wall;
   do not substitute a nominal filter size. Print a coupon before claiming fit.

The dependency-free `tools/3mf_adapter` can validate a Core package or convert
an already audited STL, but that diagnostic route is not a substitute for the
integrated OpenSCAD geometry or a physical print.
