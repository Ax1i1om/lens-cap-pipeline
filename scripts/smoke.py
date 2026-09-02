#!/usr/bin/env python3
"""Portable CI smoke test; creates only a temporary synthetic artwork."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Keep this check runnable directly from a fresh checkout, before an editable
# install exists.  The repository-local launcher follows the same convention.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from lens_cap_pipeline.config import load_config  # noqa: E402
from lens_cap_pipeline.model import generate_model  # noqa: E402
from lens_cap_pipeline.process import process  # noqa: E402
from lens_cap_pipeline.validate import validate_job  # noqa: E402
from scripts.install_skills import inspect_skills  # noqa: E402


def main() -> int:
    # Skill files are companion assets, so smoke their source manifest before
    # exercising the image/model pipeline.  This remains read-only and works
    # from a fresh checkout without a host-specific Skill directory.
    skill_report = inspect_skills(root=ROOT)
    assert skill_report["status"] == "passed", skill_report
    with tempfile.TemporaryDirectory(prefix="lens-cap-smoke-") as raw:
        root = Path(raw)
        image = Image.new("RGB", (96, 96), (17, 18, 17))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, 91, 91), fill=(242, 231, 211))
        draw.rectangle((38, 38, 57, 57), fill=(17, 18, 17))
        image.save(root / "master.png")
        (root / "job.toml").write_text(
            '''job_slug = "ci-smoke"
source_art = "master.png"
output_dir = "build"
face_diameter_mm = 52.0
grid_size = 128
nozzle_mm = 0.2
safe_border_mm = 0.3

[circle]
center_px = [48, 48]
radius_px = 44

[palette.black]
index = 0
rgb = [17, 18, 17]
role = "base"
height_mm = 0.0
required = true

[palette.ivory]
index = 1
rgb = [242, 231, 211]
role = "relief"
height_mm = 0.4
required = true
''',
            encoding="utf-8",
        )
        config = load_config(root / "job.toml")
        report = process(config)
        assert report["status"] == "passed", report
        assert report["checks"]["overflow_guard"] is True
        assert (root / "build" / "vector" / "ivory.svg").is_file()
        # Add fitted dimensions only in the in-memory smoke config?  The
        # portable fixture intentionally tests the art-only path; modelizing a
        # cap must require a separately declared mating diameter.
        model_job = root / "model-job.toml"
        model_job.write_text(
            (root / "job.toml").read_text(encoding="utf-8")
            .replace('output_dir = "build"', 'output_dir = "model-build"')
            .replace("face_diameter_mm = 52.0", "face_diameter_mm = 52.0\nmeasured_diameter_mm = 52.0")
            + '\n[fit]\nfoam_liner_status = "foam"\nliner_thickness_mm = 1.5\n',
            encoding="utf-8",
        )
        model_config = load_config(model_job)
        model_report = process(model_config)
        model = generate_model(model_config, model_report)
        assert model.scad_path.is_file()
        validation = validate_job(model_config)
        assert validation["status"] == "passed", validation
        print(
            json.dumps(
                {
                    "status": "passed",
                    "job": config.job_slug,
                    "skills": {
                        "project_version": skill_report["project_version"],
                        "manifest_sha256": skill_report["manifest_sha256"],
                    },
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
