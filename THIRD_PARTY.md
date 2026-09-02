# Third-party material and generated outputs

The repository separates pipeline code from job assets on purpose.

* The Python code and first-party templates are Apache-2.0 (`LICENSE`).
* An approved lens image, logo, brand mark, film still, or historical
  reference remains subject to its own copyright, trademark, and licence.
  Do not commit an image unless you have permission to redistribute it.
* A downloaded cap-body or MakerWorld model keeps the licence and required
  attribution of its source.  Put the URL, author, licence, and modification
  note in the job's `manifest.json`; do not assume the pipeline licence
  relicenses the source model.
* Generated STL/3MF files inherit the licences of the inputs that materially
  contribute to them.  A job may therefore be Apache-2.0, CC BY, CC BY-NC, or
  otherwise restricted even though the generator itself is open source.

The example configuration intentionally points to a local `art/master.png`
that is not included in the repository.  Contributors should use synthetic
test art or an explicitly redistributable sample when adding fixtures.
