# Contributing

The project favours small, deterministic changes that can be audited from a
job manifest.  A normal contribution is:

1. Create a project-local virtual environment and install the development
   extras (no global package installation):

   ```sh
   ./scripts/bootstrap.py --dev
   . .venv/bin/activate
   ```

   On Windows, activate with `.venv\Scripts\activate`; or use the manual
   `python3 -m venv .venv` plus `python3 -m pip install -e '.[test]'` on
   macOS/Linux (Windows: `py -3 -m venv .venv`, activate it, then use
   `python -m pip ...` so packages stay in the project environment).

2. Run `python -m pytest`, `python -m compileall -q lens_cap_pipeline`, and
   `python scripts/smoke.py` (the equivalent Make targets are `make test`,
   `make compile`, and `make smoke`).
3. Keep approved artwork immutable.  Add a new fixture instead of changing a
   source image in place.
4. Add or update a deterministic report when changing a processing rule.
   In particular, preserve the `int32`/`int64` colour-distance contract and
   test that palette polarity cannot silently invert.
5. Do not commit private source images, slicer credentials, or large binary
   outputs.  Attach a manifest and provenance note for any redistributable
   sample model.

OpenSCAD and Bambu Studio are optional developer tools. CI runs the portable
checks; use `lens-cap doctor` to inspect local availability and an opt-in
`lens-cap build ... --external` run on a machine where the external
applications are installed. Keep generated STL/3MF/G-code and non-redistributable
artwork out of commits unless their source licence explicitly permits it.

Code contributions are Apache-2.0. A contribution containing artwork, a logo,
film reference, or a downloaded model must include its own provenance and
licence statement; the code licence does not relicense those materials.
