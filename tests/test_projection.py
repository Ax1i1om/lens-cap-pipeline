from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_stl_projection.py"


def _write_binary_stl(path: Path, offset_x: float = 0.0) -> None:
    triangles = [
        ((-10.0 + offset_x, -10.0), (10.0 + offset_x, -10.0), (10.0 + offset_x, 10.0)),
        ((-10.0 + offset_x, -10.0), (10.0 + offset_x, 10.0), (-10.0 + offset_x, 10.0)),
    ]
    payload = bytearray(b"projection-test".ljust(80, b" "))
    payload.extend(struct.pack("<I", len(triangles)))
    for vertices in triangles:
        payload.extend(struct.pack("<3f", 0.0, 0.0, 1.0))
        for x, y in vertices:
            payload.extend(struct.pack("<3f", x, y, 0.0))
        payload.extend(struct.pack("<H", 0))
    path.write_bytes(payload)


def _expected_mask(path: Path) -> None:
    image = Image.new("L", (64, 64), 0)
    draw = ImageDraw.Draw(image)
    draw.polygon([(16, 48), (48, 48), (48, 16)], fill=255)
    draw.polygon([(16, 48), (48, 16), (16, 16)], fill=255)
    image.save(path)


def test_projection_audit_passes_and_writes_portable_report(tmp_path: Path) -> None:
    mesh = tmp_path / "ivory.stl"
    mask = tmp_path / "ivory.png"
    _write_binary_stl(mesh)
    _expected_mask(mask)
    report = tmp_path / "audit" / "projection.json"
    diff_dir = tmp_path / "audit" / "diff"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mesh",
            f"ivory={mesh}",
            "--expected-mask",
            f"ivory={mask}",
            "--canvas-size-mm",
            "40",
            "--tolerance-pixels",
            "0",
            "--output-report",
            str(report),
            "--output-dir",
            str(diff_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["colors"]["ivory"]["raw_iou"] == 1.0
    assert payload["colors"]["ivory"]["diff"] == "diff/ivory-projection-diff.png"
    assert (diff_dir / "ivory-projection-diff.png").is_file()


def test_projection_audit_fails_on_shifted_mesh(tmp_path: Path) -> None:
    mesh = tmp_path / "ivory.stl"
    mask = tmp_path / "ivory.png"
    _write_binary_stl(mesh, offset_x=3.0)
    _expected_mask(mask)
    report = tmp_path / "projection.json"
    diff_dir = tmp_path / "diff"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mesh",
            f"ivory={mesh}",
            "--expected-mask",
            f"ivory={mask}",
            "--canvas-size-mm",
            "40",
            "--tolerance-pixels",
            "0",
            "--output-report",
            str(report),
            "--output-dir",
            str(diff_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["failures"]
