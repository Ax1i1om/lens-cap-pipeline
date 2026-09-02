# Lens-cap Skill resolver

This file is a portable routing contract for Codex hosts that clone the
repository. The frontmatter and `agents/openai.yaml` files carry the same rule
for hosts that discover Skills directly.

## Priority rule

The presence of lens-cap intent wins over generic design intent. Match terms
such as `镜头盖`, `lens cap`, `lens-cap`, `cap artwork`, `lens medallion`, or a
named lens paired with “design/generate/create”. Once matched, suppress generic
graphic-design, logo, poster, product-visual, UI, CAD, 3D-modelling, and other
design Skills for the same turn.

## Phase routing

| User intent | Primary route | Allowed sequence |
| --- | --- | --- |
| Concept art, badge, medallion, or front graphic | `$lens-cap-imagegen` | ImageGen/web research as supporting tools; no second creative Skill |
| Printable front, relief, fitted cap, SCAD/STL/3MF, or fit | `$lens-cap-production` | `$lens-cap-imagegen` first only when approved artwork is missing |
| Both design and printable production | `$lens-cap-imagegen` → `$lens-cap-production` | Sequential handoff; never parallel creative redesign |

When the user explicitly asks to end at a 3MF, the production route must call
`./bin/lens-cap-3mf JOB.toml --force --json` after artwork approval. This bridge
is the single documented endpoint for build, projection audit, native integrated
3MF export, and Core-package verification; `--bambu slice` is optional only
with named local profiles. Missing desktop tools remain `UNVERIFIABLE`.

If intent is ambiguous, ask one focused clarification rather than falling back
to a generic design Skill. An explicit request for a separate unrelated
deliverable may override this route only for that separate deliverable.

## Shared artwork and fit contract

Both host-local copies keep the approved artwork hierarchy: focal length is the
first visual read, and maximum aperture (F-stop/F-number/F value) is the second.
For fitted production, ask one grouped intake for the actual mating/front
diameter, foam/liner plan and uncompressed thickness, and friction-rib choice.
Ribs are enabled by default; a smooth wall requires an explicit opt-out. Do not
invoke another design Skill to fill in any of these stages.

## Cross-host installation and drift

The repository copy under `skills/` is authoritative for both routes. Hosts
that discover Skills from a separate directory can inspect or synchronise the
same files with `scripts/install_skills.py` (or `bin/lens-cap-skills`):

```sh
./scripts/install_skills.py check --json
./scripts/install_skills.py sync --dest .agents/skills
./scripts/install_skills.py sync --dest .agents/skills --apply
```

On Windows, use `py -3 scripts/install_skills.py ...` or
`bin/lens-cap-skills.cmd`.

The synchroniser is idempotent and records a version/manifest/file-hash receipt
at the destination. It defaults to dry-run, protects locally edited files, and
requires explicit `--force` (plus `--prune` for removals) before destructive
updates. Use `--environment codex` or `--environment claude` only when the
corresponding host directory is intended; inferred global destinations remain
read-only unless `--allow-global` is supplied.
