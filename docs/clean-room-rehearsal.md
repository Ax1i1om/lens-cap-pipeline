# Clean-room rehearsal and release evidence

This document is the acceptance record for the public Alpha route. It answers
one question: can a new Codex task, with no previous conversation history,
take an approved named-lens artwork through the repository production route
and finish at a checked 3MF? The file runner can prove the latter; it cannot
emulate every host's Skill discovery cache. After installing or syncing Skills,
start a new Codex task/reload the host, and use an explicit `$lens-cap-*`
invocation if automatic matching has not refreshed.

## Latest audit snapshot

The 2026-09-03 Alpha candidate passes the repository test suite, Ruff,
`compileall`, both official Skill validators, and the canonical
`skills/`-to-`.agents/skills/` hash receipt. `make smoke-all` runs three
fixtures and three four-turn no-history interaction transcripts. On the
development host (OpenSCAD 2026.06.12 with Manifold), every matrix job emitted
a native 3MF through `scripts/build_3mf.py`; no smoke-only native/Bambu writer
exists. All three transcript-selected jobs passed that same bridge. The v3
95 mm case additionally passed a current Bambu Studio multipart `export` with
the exact A1 mini 0.2 mm machine, 0.10 mm process, and PLA profile files plus
their fully resolved inheritance chains. This is an audited unsliced printer
project, not a claim that it contains G-code.
Separately, the current 20 mm adapter cube passed `--require-slice`: its
manifest preserves the same profile provenance/effective-settings contract and
records 200/200 extrusion-bearing layers at 0.10 mm plus 13,438 positive XY
extrusion moves. That cube proves the semantic G-code gate; it is not a
lens-cap slice or physical-print claim. Older retained Helios/Mamiya slices are
historical compatibility samples and are excluded from this current evidence.

The default fixture is now an actual built-in ImageGen Helios-44-2 REHOUSE v3
candidate, frozen after visual approval. Its retained 95 mm native package
(`e1fda045…`) contains one object/one connected volume, 32,736 vertices and
65,468 triangles, with 100.2 × 100.2 × 16.601 mm world bounds. The
final-package gates verify ZIP/Core structure, bounds, non-empty
black/gray/ivory triangle assignments, no used colour outside the active job
palette, and all 12 expected friction ribs at their start, three interior
cross-sections, and end. Each rib must also expose a connected full-width tip
face and pass 15 full-height axial continuity samples. The rib gate proves
structural presence in the 3MF; the default bare-wall profile still reports
`guide_only_clearance` and requires a physical coupon before any retention
claim.

The v2 Helios fixture remains a 77/82/95 mm no-foam adapter matrix. The
independent Mamiya-Sekor C 80mm F1.9 fixture covers 77/85/95 mm with 1.5 mm
foam; its 85 mm primary case records and validates `80 + 2×2.5 = 85 mm` and
reports `foam_contact_unverified` until a coupon is measured. The resolver
selects `$lens-cap-imagegen` → `$lens-cap-production` for explicit Sigma
28–70mm F2.8 lens-cap/3MF wording and schedules no generic design Skill. A clean
task rejects generic “某镜头” and terse “test this lens” wording; the latter is
accepted only with the explicit `--lens-cap-context` continuation flag.

This is evidence for the declared repository/tool matrix, not a promise that
every Codex host exposes the same Skill discovery cache, provider attachment
save API, OpenSCAD version, Bambu profiles, or printer tolerances. The fixture
records a real ImageGen result and approval boundary; the deterministic
rehearsal does not call ImageGen again or claim pixel-identical regeneration.
After Skill sync, a host must start a new task/reload its catalog, or use the
documented explicit `$lens-cap-*` invocation.

### Blind Codex route audit

A separate agent was started without this conversation and was given only the
repository path plus the natural request “design a Sigma 28–70mm F2.8 DG DN
lens cap for an 82-to-95 cinema adapter, no foam, default ribs, 0.2 mm nozzle,
finish at printable 3MF.” The resolver selected
`lens-cap-imagegen -> lens-cap-production`, reported no generic parallel design
Skill, and preserved the already supplied no-foam/rib/nozzle choices. Its one
legitimate clarification was whether 95 mm was the caliper-measured cylindrical
gripping surface or only the adapter's nominal label; the latter must not be
invented as `measured_diameter_mm` or reverse-engineered into a 6.5 mm radial
wall without evidence.

That blind run also found two portability defects now covered by regression
tests: the project-local `.agents/skills` mirror had drifted from authoritative
`skills/`, and a quoted creation request inside a read-only prompt/route audit
was mistaken for a real deliverable. The mirror now has a checked hash receipt,
and explicit prompt simulations, interaction replays, and read-only/no-file
wrappers hard-veto quoted affirmative verbs. A genuine later cap deliverable in
an ordinary mixed turn remains clause-aware and can still route.

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
   and verifies the 3MF Core package. For an editable Bambu Studio deliverable,
   add `--bambu export` with three explicit local profiles and deliver the
   report's `primary_3mf`. Reserve `--bambu slice` for an explicit request for
   embedded G-code tied to that exact printer/material setup.

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
  examples/rehearsals/helios-44-2-imagegen-v3-95mm-clean-room.json \
  --bambu never --json
```

Each checked-in scenario is intentionally a four-turn “new user” exchange:
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

The repository also retains the legacy multi-diameter Helios transcript and
`examples/rehearsals/mamiya-sekor-c-80-f1-9-rehouse-clean-room.json`. The latter
repeats the exchange with a different lens identity and an 85 mm envelope
(`80 mm nominal ring + 2.5 mm radial wall`) plus 1.5 mm uncompressed foam.
Running all three is a cross-job leakage check: Helios text, M42, maker-specific
coating marks, and other job-local tokens must not carry into the Mamiya brief.

## Matrix exercised on the development host

The checked-in fixtures use generated, provider-neutral artwork and no copied
user 3MF. Each test includes an explicit adapter-envelope calculation so the
radial wall is not accidentally treated as a filter-thread diameter.

| fixture | approved visual identity | adapter envelope | mating diameter | liner | retained output |
| --- | --- | ---: | ---: | --- | --- |
| Helios-44-2 REHOUSE (actual ImageGen v3 default) | 58 / F2 | 95 + 2×0 mm | 95 mm | none | canonical native 3MF + release report |
| Helios-44-2 REHOUSE (legacy ImageGen v2) | 58 / F2 | 72 + 2×2.5 mm | 77 mm | none | canonical native 3MF + release report |
| Helios-44-2 REHOUSE (legacy ImageGen v2) | 58 / F2 | 77 + 2×2.5 mm | 82 mm | none | canonical native 3MF + release report |
| Helios-44-2 REHOUSE (legacy ImageGen v2) | 58 / F2 | 95 + 2×0 mm | 95 mm | none | canonical native 3MF + release report |
| Mamiya-Sekor C REHOUSE | 80 / F1.9 | 72 + 2×2.5 mm | 77 mm | 1.5 mm | canonical native 3MF + release report |
| Mamiya-Sekor C REHOUSE | 80 / F1.9 | 80 + 2×2.5 mm | 85 mm | 1.5 mm | canonical native 3MF + release report |
| Mamiya-Sekor C REHOUSE | 80 / F1.9 | 95 + 2×0 mm | 95 mm | 1.5 mm | canonical native 3MF + release report |

The v3 default plus the 77/82/95 and 77/85/95 job TOMLs, approved masters,
prompt records, briefs, and retained 3MF evidence live under:

- `examples/fixtures/helios-44-2-rehouse-imagegen-v3/`
- `examples/fixtures/helios-44-2-rehouse-imagegen-v2/`
- `examples/fixtures/mamiya-sekor-c-80-f1-9-rehouse/`

The native 3MF files are unsliced Core packages. The adapter removes OpenSCAD's
volatile creation timestamp, replaces generated UUID attributes with
content-derived UUIDv5 values, and rewrites ZIP entries in a canonical order
with fixed metadata. Repeated exports are therefore byte-identical under the
same OpenSCAD/Python/zlib toolchain. Across different tool versions, the
portable equivalence criteria remain source/config hashes, geometry
counts/bounds and surface evidence, projection, palette assignments, and rib
gates; the project does not promise identical mesh tessellation or compressed
bytes across arbitrary OpenSCAD/zlib versions. Printer-specific slicing remains
a separate local stage.

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

The machine-readable report separates these claims:

- `passed`: source/config hashes, palette roles, mask coverage, SVG canvas,
  model parameters, STL projection, 3MF package/XML/mesh indices, and (when
  requested) ordered layer metadata plus spatially plausible extrusion paths
  all satisfy their declared gates;
- `unverifiable`: an external executable, printer profile, or physical coupon
  is absent; no stronger claim is made;
- `failed`: a required input or invariant disagrees, so downstream release is
  blocked;
- `fit_status = unverifiable_until_coupon_measurement`: a physical lens,
  foam batch, ribs, printer, and material still need a short same-material
  fit-ring print and measurement.

The portable smoke runner may exit zero when OpenSCAD is absent so its JSON can
be collected, but top-level `status=unverifiable` and
`native_3mf_complete=false` are not success. `deterministic_preflight_status`
or `interaction_status` may independently be `passed`. Use
`--require-external` when a real native 3MF is the acceptance endpoint.

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
4. Add explicit Bambu machine/process/filament profiles for either an exported
   or sliced printer project. They are not needed for a native Core 3MF. The
   repository does not log into MakerWorld or ChromaCanvas.
5. Measure the real cylindrical surface the cap grips, including adapter wall;
   do not substitute a nominal filter size. Print a coupon before claiming fit.

The dependency-free `tools/3mf_adapter` can validate a Core package or convert
an already audited STL, but that diagnostic route is not a substitute for the
integrated OpenSCAD geometry or a physical print.
