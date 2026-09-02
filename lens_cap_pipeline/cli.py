"""Command-line entry point for the reproducible lens-cap pipeline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from .config import (
    FRICTION_RIB_PROFILE_NAMES,
    ConfigError,
    friction_rib_profile_defaults,
    load_config,
    normalize_friction_rib_profile,
    template_config,
)
from .external import ExternalToolError, doctor, export_openscad, write_bambu_handoff
from .model import ModelError, ModelResult, generate_model
from .process import ProcessError, process, sha256_file
from .validate import validate_job


def _write_config(path: Path, data: dict) -> None:
    """Write the dependency-free starter TOML/JSON config."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    if path.suffix.lower() not in {".toml", ".tml"}:
        raise ConfigError("init target must end in .json or .toml")
    lines = [
        "schema_version = 1",
        f'job_slug = {json.dumps(data["job_slug"])}',
        f'source_art = {json.dumps(data["source_art"])}',
        f'output_dir = {json.dumps(data["output_dir"])}',
        f'grid_size = {data["grid_size"]}',
        f'nozzle_mm = {data["nozzle_mm"]}',
        f'safe_border_mm = {data["safe_border_mm"]}',
        "assembly_mode = \"auto\"",
        "",
        "[circle]",
        "# center_px = [627, 624] # required for opaque square art",
        "# radius_px = 619",
        "allow_outside = false",
        "",
        "[prefilter]",
        f'name = {json.dumps(data["prefilter"]["name"])}',
        f'size = {data["prefilter"]["size"]}',
        f'radius = {data["prefilter"]["radius"]}',
        "",
        "[cleanup]",
        f'enabled = {str(data["cleanup"]["enabled"]).lower()}',
        f'max_area_px = {data["cleanup"]["max_area_px"]}',
        f'max_dimension_px = {data["cleanup"]["max_dimension_px"]}',
        f'ring_px = {data["cleanup"]["ring_px"]}',
        f'dominance = {data["cleanup"]["dominance"]}',
        'apply_to = ["relief"]',
    ]
    # A fitted-cap user should only have to provide the measured mating
    # diameter.  When that value is the source of the face size, omitting the
    # optional face field keeps the derivation visible in the config instead
    # of asking the same question twice.
    if data.get("face_diameter_mm") is not None:
        lines.insert(4, f'face_diameter_mm = {data["face_diameter_mm"]}')
    for name, spec in data["palette"].items():
        lines += [
            "",
            f"[palette.{name}]",
            f"index = {spec['index']}",
            f"rgb = {spec['rgb']}",
            f"role = {json.dumps(spec['role'])}",
            f"height_mm = {spec['height_mm']}",
            f"required = {str(spec['required']).lower()}",
        ]
        if "detect" in spec:
            detect = spec["detect"]
            deltas = "{ " + ", ".join(
                f"{str(key)} = {int(value)}" for key, value in detect["deltas"].items()
            ) + " }"
            lines += [
                "",
                f"[palette.{name}.detect]",
                f"channel = {json.dumps(detect['channel'])}",
                f"minimum = {detect['minimum']}",
                f"deltas = {deltas}",
            ]
    if data.get("measured_diameter_mm") is not None:
        # Keep the real gripping measurement explicit; a nominal filter size
        # must never become a hidden fitted-cap default.
        lines.insert(5, f'measured_diameter_mm = {data["measured_diameter_mm"]}')
    fit = data.get("fit")
    if isinstance(fit, dict):
        marker = next((i for i, line in enumerate(lines) if line == "[circle]"), len(lines))
        fit_lines = [
            "[fit]",
            f'foam_liner_status = {json.dumps(fit.get("foam_liner_status", "none"))}',
        ]
        for key in (
            "liner_material", "liner_thickness_mm", "compression_fraction",
            "compression_is_assumption", "wall_thickness_mm", "bottom_thickness_mm",
            "side_height_mm", "bare_clearance_mm", "friction_ribs_enabled",
            "friction_ribs_explicit", "friction_rib_profile", "friction_rib_count", "friction_rib_protrusion_mm",
            "friction_rib_width_mm", "friction_rib_height_mm", "friction_rib_start_mm",
            "retention_strategy",
        ):
            if fit.get(key) is None:
                continue
            value = fit[key]
            if isinstance(value, str):
                rendered = json.dumps(value)
            elif isinstance(value, bool):
                rendered = str(value).lower()
            else:
                rendered = str(value)
            fit_lines.append(f"{key} = {rendered}")
        lines[marker:marker] = fit_lines + [""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lens-cap",
        description="Reproducible artwork, relief, model and printer handoff pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    process_parser = sub.add_parser("process", help="build process master, masks, SVGs and report")
    process_parser.add_argument("config", type=Path)
    process_parser.add_argument("--force", action="store_true", help="replace generated derivatives")
    process_parser.add_argument("--json", action="store_true", help="print the complete report")

    model_parser = sub.add_parser("model", aliases=["generate-scad"], help="generate parameterised OpenSCAD geometry from a passed process")
    model_parser.add_argument("config", type=Path)
    model_parser.add_argument("--force", action="store_true")
    model_parser.add_argument("--json", action="store_true")

    run_parser = sub.add_parser("run", aliases=["build"], help="process artwork, generate model, validate and optionally export")
    run_parser.add_argument("config", type=Path)
    run_parser.add_argument("--force", action="store_true")
    run_parser.add_argument("--export-openscad", action="store_true", help="export STL selectors when OpenSCAD is available")
    run_parser.add_argument("--mesh", dest="export_openscad", action="store_true", help=argparse.SUPPRESS)
    run_parser.add_argument("--bambu-handoff", action="store_true", help="write the version-neutral Bambu handoff manifest")
    run_parser.add_argument("--external", action="store_true", help="compile/check with installed OpenSCAD")
    run_parser.add_argument("--strict-external", action="store_true", help="fail when an external check is unavailable")
    run_parser.add_argument("--openscad", help="explicit OpenSCAD executable for external checks/exports")
    run_parser.add_argument("--json", action="store_true")

    export_parser = sub.add_parser("export-openscad", aliases=["mesh"], help="export selected model parts to STL")
    export_parser.add_argument("config", type=Path)
    export_parser.add_argument("--part", action="append", dest="parts", help="selector (repeatable)")
    export_parser.add_argument("--force", action="store_true")
    export_parser.add_argument("--strict-external", action="store_true", help="treat a missing renderer as a failure")
    export_parser.add_argument("--openscad", help="explicit OpenSCAD executable")
    export_parser.add_argument("--json", action="store_true")

    handoff_parser = sub.add_parser("bambu-handoff", aliases=["handoff"], help="write an explicit Bambu Studio handoff manifest")
    handoff_parser.add_argument("config", type=Path)
    handoff_parser.add_argument("--force", action="store_true")
    handoff_parser.add_argument("--json", action="store_true")

    validate_parser = sub.add_parser("validate", help="validate process/model artifacts and config")
    validate_parser.add_argument("config", type=Path)
    validate_parser.add_argument("--external", action="store_true")
    validate_parser.add_argument("--strict-external", action="store_true")
    validate_parser.add_argument("--openscad", help="explicit OpenSCAD executable for the compile probe")
    validate_parser.add_argument("--json", action="store_true")

    doctor_parser = sub.add_parser("doctor", help="show optional external-tool availability")
    doctor_parser.add_argument("--config", type=Path, default=None)
    doctor_parser.add_argument("--json", action="store_true")

    init_parser = sub.add_parser("init", help="write a portable starter JSON/TOML config")
    init_parser.add_argument("config", type=Path)
    init_parser.add_argument("--source", default="art/master.png", help="approved source artwork path")
    init_parser.add_argument(
        "--face-diameter",
        type=float,
        default=None,
        help="finished front/relief diameter; optional when --measured-diameter is supplied",
    )
    init_parser.add_argument("--measured-diameter", type=float, default=None, help="actual gripping diameter for a fitted cap")
    init_parser.add_argument("--foam-thickness", type=float, default=None, help="uncompressed foam liner thickness in mm")
    init_parser.add_argument(
        "--friction-ribs",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="enable inner-wall friction ribs (default: enabled; use --no-friction-ribs to disable)",
    )
    init_parser.add_argument(
        "--friction-rib-profile",
        choices=FRICTION_RIB_PROFILE_NAMES,
        default=None,
        help="generic rib shape preset; explicit fit dimensions still override it",
    )
    init_parser.add_argument("--nozzle", type=float, default=0.2)
    init_parser.add_argument("--output-dir", default="build")
    init_parser.add_argument("--job-slug", default="my-lens-cap")
    init_parser.add_argument("--force", action="store_true")
    return parser


def _emit(payload: object, *, as_json: bool = False) -> None:
    if as_json or not isinstance(payload, dict):
        print(json.dumps(payload, ensure_ascii=False, indent=2) if isinstance(payload, (dict, list)) else payload)
        return
    print(f"status: {payload.get('status', 'passed')}")
    for key in ("process_master", "scad", "report", "openscad_report", "bambu_handoff", "validation_report", "path"):
        if payload.get(key):
            print(f"{key}: {payload[key]}")


def _init_target(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.suffix.lower() in {".toml", ".tml", ".json"} else expanded / "job.toml"


def _init_source_for_config(value: str, target: Path) -> str:
    """Resolve a CLI source argument and serialize it clone-relatively."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("--source must be a non-empty path")
    raw = Path(value).expanduser()
    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        # A command-line path is normally relative to cwd, while the schema
        # resolves paths from the config directory.  Prefer the cwd file when
        # it exists (the explicit CLI interpretation); otherwise preserve the
        # useful config-relative default such as ``art/master.png``.
        cwd_candidate = (Path.cwd() / raw).resolve()
        config_candidate = (target.parent.resolve() / raw).resolve()
        # A relative argument is first interpreted from the caller's cwd when
        # that file already exists.  If it does not, retain the task-local
        # spelling instead of serializing a path relative to an unrelated cwd.
        # This matters for ``init jobs/name/job.toml`` from an empty checkout:
        # the starter ``art/master.png`` should remain beside that job, not
        # unexpectedly point at ``../art/master.png`` under the caller.
        resolved = cwd_candidate if cwd_candidate.is_file() else config_candidate
    try:
        return Path(os.path.relpath(resolved, target.parent.resolve())).as_posix()
    except (ValueError, OSError):
        # Cross-volume sources cannot have a portable relative spelling; keep
        # the explicit path rather than silently binding a clone to a basename.
        return str(resolved)


def _process_for_stage(config, *, force: bool) -> dict:
    """Reuse a matching passed report; never silently overwrite a job."""
    report_path = config.output_dir / "process-report.json"
    in_progress = config.output_dir / ".process-in-progress"
    if in_progress.is_file() and not force:
        # A prior process may have left a passed-looking report before an
        # output write or manifest commit completed.  Fail closed instead of
        # letting model/export stages consume a mixed transaction.
        raise ProcessError(
            f"an incomplete process transaction is present: {in_progress}; use --force to rebuild"
        )
    if report_path.is_file() and not force:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProcessError(f"cannot read existing process report {report_path}: {exc}; use --force") from exc
        if not isinstance(report, dict):
            raise ProcessError(f"existing process report is not a JSON object: {report_path}; use --force")
        if report.get("status") == "passed" and report.get("config_sha256") == config.digest():
            # A config digest alone does not bind the report to the current
            # artwork bytes: users can replace a source file without editing
            # the TOML/JSON.  Require the immutable source hash and a core
            # master artifact before reusing a prior stage.
            source_section = report.get("source")
            reported_source = source_section.get("sha256") if isinstance(source_section, dict) else None
            try:
                current_source = sha256_file(config.source_path)
            except OSError as exc:
                raise ProcessError(
                    f"source artwork is unavailable while reusing {report_path}; use --force after restoring it"
                ) from exc
            if not reported_source or str(reported_source).lower() != current_source.lower():
                raise ProcessError(
                    f"existing process output is stale for the current source: {report_path}; use --force to rebuild"
                )
            if not (config.output_dir / "process-master.png").is_file():
                raise ProcessError(
                    f"existing process report has no process-master.png: {report_path}; use --force to rebuild"
                )
            return report
        raise ProcessError(f"existing process output is stale or failed: {report_path}; use --force to rebuild")
    return process(config, force=force)


def _model_for_stage(config, report: dict, *, force: bool) -> ModelResult:
    model_dir = config.output_dir / "model"
    scad = model_dir / f"{config.job_slug}.scad"
    geometry_path = model_dir / "geometry-report.json"
    if scad.is_file() and geometry_path.is_file() and not force:
        try:
            geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelError(f"cannot read existing geometry report {geometry_path}: {exc}; use --force") from exc
        current_scad_hash = _sha256_path(scad)
        if (
            geometry.get("status") == "passed"
            and geometry.get("process_report_sha256") == _sha256_path(config.output_dir / "process-report.json")
            and geometry.get("scad_sha256") == current_scad_hash
        ):
            return ModelResult(model_dir, scad, geometry_path, geometry)
        raise ModelError(f"existing model output is stale: {model_dir}; use --force to rebuild")
    return generate_model(config, report, force=force)


def _sha256_path(path: Path) -> str | None:
    if not path.is_file():
        return None
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            target = _init_target(args.config).resolve()
            if target.exists() and not args.force:
                raise ConfigError(f"refusing to overwrite existing config: {target}; use --force")
            if args.face_diameter is None and args.measured_diameter is None:
                raise ConfigError("init needs --face-diameter, or --measured-diameter for a fitted cap")
            if args.foam_thickness is not None and args.measured_diameter is None:
                raise ConfigError("--foam-thickness requires --measured-diameter for a fitted cap")
            # A measured fitted diameter is the default finished face size;
            # keep the optional override only when explicitly supplied.
            face = args.face_diameter if args.face_diameter is not None else args.measured_diameter
            data = template_config(_init_source_for_config(args.source, target), face, args.output_dir)
            data["job_slug"] = args.job_slug
            data["nozzle_mm"] = args.nozzle
            # Keep one fit mapping alive for all optional mechanical flags.
            # Previously this was created only inside the foam branch, so
            # ``init --friction-rib-profile ...`` without ``--foam-thickness``
            # raised UnboundLocalError before it could write a starter job.
            fit_data = data.setdefault("fit", {})
            if args.measured_diameter is not None:
                data["measured_diameter_mm"] = args.measured_diameter
                if args.face_diameter is None:
                    data.pop("face_diameter_mm", None)
                # A bare fitted cap has no compressible liner.  Do not leave
                # the template's foam-only 20% assumption in a no-foam job:
                # it is ignored by the cavity calculation, but it is easy for
                # a new user (or a downstream adapter) to mistake it for a
                # physical claim.  Foam jobs overwrite these fields below.
                fit_data.update(
                    {
                        "foam_liner_status": "none",
                        "compression_fraction": 0.0,
                        "compression_is_assumption": False,
                        "retention_strategy": "bare_wall_plus_neutral_ribs",
                    }
                )
            if args.foam_thickness is not None:
                # Update the template's fit table instead of replacing it.
                # Keeping wall, rib, and printability defaults in the emitted
                # file makes the job self-describing and prevents a future
                # schema change from silently changing an old starter job.
                fit_data.update(
                    {
                        "foam_liner_status": "foam",
                        "liner_thickness_mm": args.foam_thickness,
                        "compression_fraction": 0.20,
                        "compression_is_assumption": True,
                        "retention_strategy": "continuous_foam",
                    }
                )
            if args.friction_rib_profile is not None:
                # Apply the selected preset to the starter values.  The
                # emitted TOML remains fully editable: any later explicit
                # field overrides the profile on reload.
                profile = normalize_friction_rib_profile(args.friction_rib_profile)
                profile_base = (
                    float(args.measured_diameter)
                    if args.measured_diameter is not None
                    else float(face)
                )
                if fit_data.get("foam_liner_status") == "foam" and fit_data.get("liner_thickness_mm") is not None:
                    profile_cavity = profile_base + 2.0 * float(fit_data["liner_thickness_mm"]) * (
                        1.0 - float(fit_data.get("compression_fraction", 0.20))
                    )
                else:
                    profile_cavity = profile_base + float(fit_data.get("bare_clearance_mm", 0.40))
                defaults = friction_rib_profile_defaults(
                    profile,
                    profile_cavity,
                    float(fit_data.get("side_height_mm", 14.0)),
                )
                fit_data["friction_rib_profile"] = profile
                for key, value in defaults.items():
                    fit_data[key] = value
            # Keep the default in the generated config so a job is portable
            # and self-describing.  If the user explicitly chose a flag, mark
            # that decision separately from the enabled/disabled value.
            fit_data = data.setdefault("fit", {})
            if args.friction_ribs is not None:
                fit_data["friction_ribs_enabled"] = bool(args.friction_ribs)
                fit_data["friction_ribs_explicit"] = True
            else:
                fit_data.setdefault("friction_ribs_enabled", True)
                fit_data.setdefault("friction_ribs_explicit", False)
            _write_config(target, data)
            # Keep a placeholder beside the task for a not-yet-supplied
            # relative source.  Derive the directory from the serialized
            # source instead of always creating ``art/``: custom paths such
            # as ``reference/master.png`` should get the matching placeholder,
            # while an explicit path outside the task must never cause mkdir
            # to touch an unrelated directory.
            source_value = Path(str(data.get("source_art", ""))).expanduser()
            if not source_value.is_absolute():
                source_parent = (target.parent / source_value.parent).resolve()
                task_parent = target.parent.resolve()
                try:
                    source_parent.relative_to(task_parent)
                except ValueError:
                    pass
                else:
                    source_parent.mkdir(parents=True, exist_ok=True)
            output_path = Path(str(data.get("output_dir", "build"))).expanduser()
            if not output_path.is_absolute():
                output_path = target.parent / output_path
            output_path.resolve().mkdir(parents=True, exist_ok=True)
            _emit({"status": "passed", "path": str(target.resolve())})
            return 0
        if args.command == "doctor":
            config = load_config(args.config) if args.config else None
            _emit(doctor(config), as_json=args.json)
            return 0

        config = load_config(args.config)
        # External tool paths are runtime overrides, not artwork semantics.
        # Keep them on the immutable normalized config so every downstream
        # stage (export, validate, and handoff provenance) observes the same
        # executable.  The bridge passes an absolute path; direct CLI callers
        # may also use a command name resolved through PATH.
        openscad_override = getattr(args, "openscad", None)
        if openscad_override:
            config = replace(
                config,
                print=replace(config.print, openscad_executable=str(openscad_override)),
            )
        if args.command == "validate":
            report = validate_job(
                config,
                run_external=args.external,
                strict_external=args.strict_external,
            )
            _emit(report, as_json=args.json)
            return 0 if report["status"] == "passed" else 1

        if args.command == "process":
            report = process(config, force=args.force)
            _emit(
                report if args.json else {
                    "status": report["status"],
                    "report": str(config.output_dir / "process-report.json"),
                },
                as_json=args.json,
            )
            return 0 if report["status"] == "passed" else 1

        if args.command in {"model", "generate-scad"}:
            report = _process_for_stage(config, force=args.force)
            model = _model_for_stage(config, report, force=args.force)
            payload = {"status": "passed", "scad": str(model.scad_path), "report": str(model.geometry_report_path)}
            _emit(payload, as_json=args.json)
            return 0

        # The remaining commands all require a passed process and model.
        report = _process_for_stage(config, force=args.force)
        model = _model_for_stage(config, report, force=args.force)
        payload: dict[str, object] = {
            "status": "passed",
            "process_master": str(config.output_dir / "process-master.png"),
            "scad": str(model.scad_path),
            "report": str(model.geometry_report_path),
        }
        external_pending = False
        if args.command in {"export-openscad", "mesh"}:
            exported = export_openscad(config, model, parts=args.parts, force=args.force)
            payload["openscad_report"] = str(exported.report_path)
            if (
                exported.report.get("status") == "unverifiable"
                or exported.report.get("tool_status") != "passed"
            ):
                external_pending = True
                payload["status"] = "failed" if getattr(args, "strict_external", False) else "unverifiable"
        elif args.command in {"bambu-handoff", "handoff"}:
            handoff = write_bambu_handoff(config, model)
            payload["bambu_handoff"] = str(handoff.report_path)
            # ``available`` means a portable manifest was written; no STL (or
            # no slicer verification) remains an explicitly pending state.
            if (
                handoff.report.get("status") == "unverifiable"
                or handoff.report.get("tool_status") != "passed"
                or (handoff.report.get("mesh_provenance") or {}).get("status") != "passed"
            ):
                external_pending = True
                payload["status"] = "unverifiable"
        elif args.command in {"run", "build"}:
            if args.export_openscad:
                exported = export_openscad(config, model, force=args.force)
                payload["openscad_report"] = str(exported.report_path)
                external_pending = (
                    exported.report.get("status") == "unverifiable"
                    or exported.report.get("tool_status") != "passed"
                )
            if args.bambu_handoff:
                handoff = write_bambu_handoff(config, model)
                payload["bambu_handoff"] = str(handoff.report_path)
                external_pending = (
                    external_pending
                    or handoff.report.get("status") == "unverifiable"
                    or handoff.report.get("tool_status") != "passed"
                    or (handoff.report.get("mesh_provenance") or {}).get("status") != "passed"
                )
            validation = validate_job(
                config,
                run_external=args.external,
                strict_external=args.strict_external,
            )
            payload["validation_report"] = str(config.output_dir / "validation-report.json")
            payload["status"] = validation["status"]
            if payload["status"] == "passed" and external_pending:
                # Optional desktop stages do not invalidate the deterministic
                # core, but the command must not present an unverified handoff
                # as a complete production PASS.
                payload["status"] = "failed" if args.strict_external else "unverifiable"
        _emit(payload, as_json=args.json)
        # An unavailable optional adapter is a truthful pending result, not a
        # process error; strict mode promotes it to a failing exit status.
        return 0 if payload["status"] in {"passed", "unverifiable", "available"} else 1
    except (ConfigError, ProcessError, ModelError, ExternalToolError, OSError, ValueError) as exc:
        print(f"lens-cap: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
