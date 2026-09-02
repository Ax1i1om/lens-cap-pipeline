# Helios-44-2 REHOUSE clean-room fixture

This fixture is the repository's end-to-end rehearsal for a named vintage
REHOUSE lens. It is intentionally a style/specification test, not a claim of
pixel-perfect generative reproduction. The approved circular master keeps
`58` as the first read and `F2` as the second; all other text is secondary and
the large fields are black, charcoal, gray, and ivory linocut shapes.

The cultural cue is deliberately strong but scoped. The Soviet Helios-44-2 is
a 58 mm f/2 lens supplied with Zenit cameras. The ASC describes IronGlass
rehoused Helios-44-2 lenses in testing and production for *Dune: Part Two*,
following work on *The Batman*. That is a family-level cinema/rehouse
association, not evidence that every original still-lens specimen was used on
every shot. The artwork translates it into searchlight, desert-contour, swirl,
and registration motifs; it contains no film logos, stills, or characters.

The specification and historical starting points are the [Zenit-E
manual](https://www.zenitcamera.com/mans/zenit-e/zenit-e-eng.html), the [ASC
production account](https://theasc.com/article/expanding-view-dune-part-two/),
and the [industry rehouse description](https://www.vintagelensesforvideo.com/helios44cine/).
The generated-art terms and third-party marks remain separate from this
repository's Apache-2.0 code licence.

## Three clean-room fit scenarios

The jobs use measured gripping diameters rather than nominal filter-thread
labels. They are synthetic adapter envelopes for repeatable testing:

| job | test interpretation | measured mating diameter |
| --- | --- | ---: |
| `95mm` | nominal rehoused PL front | 95.0 mm |
| `82mm` | 77 mm ring plus 2.5 mm radial adapter wall | 82.0 mm |
| `77mm` | 72 mm ring plus 2.5 mm radial adapter wall | 77.0 mm |

All three jobs use no foam and leave the neutral `light_tapered` inner-wall
friction ribs enabled by default (12 shallow ribs). A real user must measure
the outside cylindrical surface the cap grips, including any adapter wall,
and replace the value before printing. The face/relief diameter follows that
measurement; the fixture deliberately does not ask for a second relief
diameter or a structure choice.

## Retained 3MF outputs

`artifacts/` contains outputs generated from the fixture's current SCAD/STL
chain, never from a user-supplied 3MF:

| file | route | verification |
| --- | --- | --- |
| `helios-44-2-rehouse-95mm-native.3mf` | OpenSCAD `Manifold` native Core 3MF | ZIP/XML/mesh indices pass; unsliced |
| `helios-44-2-rehouse-82mm-native.3mf` | OpenSCAD `Manifold` native Core 3MF | ZIP/XML/mesh indices pass; unsliced |
| `helios-44-2-rehouse-77mm-native.3mf` | OpenSCAD `Manifold` native Core 3MF | ZIP/XML/mesh indices pass; unsliced |
| `helios-44-2-rehouse-95mm-bambu-slice.3mf` | Bambu Studio A1 mini, 0.2 mm, 0.10 mm process | embedded non-empty G-code; preview still requires human review |
| `helios-44-2-rehouse-95mm-assembly-standard.3mf` | dependency-free STL → Core adapter | ZIP/XML/mesh indices pass; explicit non-manifold audit |
| `helios-44-2-rehouse-82mm-assembly-standard.3mf` | dependency-free STL → Core adapter | ZIP/XML/mesh indices pass; explicit non-manifold audit |
| `helios-44-2-rehouse-77mm-assembly-standard.3mf` | dependency-free STL → Core adapter | ZIP/XML/mesh indices pass; explicit non-manifold audit |

Each file has a `.manifest.json` sidecar with input/output hashes, tool banner,
portable command, package counts, and (for sliced output) G-code byte count.
Native OpenSCAD output avoids the coincident/internal-face ambiguity that can
appear when a color-relief assembly is flattened to a single STL. The
dependency-free `standard` adapter remains available as an explicit diagnostic
route; for this fixture it records the assembly's non-manifold internal edges
instead of pretending they prove printability.

Bambu Studio's CLI can report a successful slice while emitting non-fatal wall
orientation diagnostics. The retained sidecar records stdout/stderr and the
empty plate-warning field; inspect the 3MF preview and topology before a real
print. A valid package is not evidence of physical fit.

The STL-derived standard packages have Bambu `--info` bounds of 100.2 × 100.2
× 16.601 mm (95 mm), 87.2 × 87.2 × 16.601 mm (82 mm), and 82.2 × 82.2 ×
16.601 mm (77 mm). Their STL audit reports 318 non-manifold internal edges
plus 4/8/3 boundary edges after dropping 2/3/1 zero-area facets; they are
diagnostic Core packages, not a printability claim. The native OpenSCAD
packages are one-part `manifold = yes` on the recorded Bambu `--info` check.

## Reproduce from a fresh checkout

The recommended clean-room command copies only the source art, prompt, brief,
and TOML jobs into a temporary directory, so old outputs or conversation
history cannot influence the result:

```sh
python3 scripts/smoke_rehouse.py --bambu never --json
# On a host with OpenSCAD and compatible Bambu profiles:
python3 scripts/smoke_rehouse.py --bambu auto --require-external --keep-workdir --json
```

To refresh the retained artifacts explicitly, pass
`--artifact-dir examples/fixtures/helios-44-2-rehouse/artifacts
--force-artifacts`. The runner never writes back to `art/master.png` or copies
old `out/` directories. Its final fit status remains
`UNVERIFIABLE_UNTIL_COUPON_MEASUREMENT` until a same-material fit coupon is
printed and measured.

The three STL-derived standard packages can be recreated after the builds with
the standalone adapter (the flags make the topology caveat explicit):

```sh
for size in 95 82 77; do
  python3 tools/3mf_adapter/three_mf_adapter.py standard \
    "examples/fixtures/helios-44-2-rehouse/jobs/${size}mm/out/model/mesh/helios-44-2-rehouse-${size}mm-assembly.stl" \
    "examples/fixtures/helios-44-2-rehouse/artifacts/helios-44-2-rehouse-${size}mm-assembly-standard.3mf" \
    --title "Helios-44-2 rehouse ${size}mm assembly" \
    --drop-degenerate --allow-nonmanifold
done
```
