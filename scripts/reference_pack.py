#!/usr/bin/env python3
"""Validate and list the bundled lens-cap quality reference pack.

The pack is deliberately data-only. This command gives a clean-context host a
deterministic way to discover the same reference paths and hashes without
looking through old job folders or relying on conversation history.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PACK_DIR = ROOT / "skills" / "lens-cap-imagegen" / "references" / "quality-library"
MANIFEST_PATH = PACK_DIR / "reference-manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n" or len(header) != 24:
        raise ValueError(f"not a supported PNG: {path}")
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def load_manifest() -> dict[str, Any]:
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {MANIFEST_PATH}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("reference-manifest.json must have schema_version=1")
    assets = payload.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("reference manifest has no assets")
    return payload


def check_manifest() -> dict[str, Any]:
    manifest = load_manifest()
    assets = manifest["assets"]
    seen: set[str] = set()
    checked: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            errors.append(f"assets[{index}] is not an object")
            continue
        filename = asset.get("file")
        expected = asset.get("sha256")
        if not isinstance(filename, str) or not filename or filename in seen:
            errors.append(f"assets[{index}] has a missing or duplicate file name")
            continue
        seen.add(filename)
        path = (PACK_DIR / filename).resolve()
        try:
            path.relative_to(PACK_DIR.resolve())
        except ValueError:
            errors.append(f"asset escapes quality-library: {filename}")
            continue
        if not path.is_file():
            errors.append(f"missing asset: {filename}")
            continue
        actual = _sha256(path)
        try:
            dimensions = _png_size(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if actual != expected:
            errors.append(f"hash mismatch: {filename} ({actual} != {expected})")
        if dimensions[0] != dimensions[1]:
            errors.append(f"reference is not square: {filename} ({dimensions!r})")
        checked.append({"file": filename, "sha256": actual, "dimensions": list(dimensions)})

    lead = manifest.get("default_lead")
    if lead not in seen:
        errors.append(f"default_lead is not listed in assets: {lead!r}")
    return {
        "status": "passed" if not errors else "failed",
        "manifest": str(MANIFEST_PATH.relative_to(ROOT)),
        "pack_id": manifest.get("pack_id"),
        "asset_count": len(checked),
        "assets": checked,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "list"), nargs="?", default="check")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    report = check_manifest()
    if args.command == "list" and report["status"] == "passed" and not args.json:
        for item in report["assets"]:
            print(item["file"])
    elif args.json or report["status"] != "passed":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
