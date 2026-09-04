"""Configuration loading and validation for the lens-cap pipeline.

The public configuration is intentionally small and boring: a JSON or TOML
file describes one artwork, one circular crop, and a palette whose roles are
explicit.  This module turns that file into an immutable, path-resolved
``PipelineConfig`` object.  No lens name, workspace path, or previous job is
used as an implicit default.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class ConfigError(ValueError):
    """Raised when a pipeline configuration is unsafe or incomplete."""


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_CHANNELS = {"r": 0, "g": 1, "b": 2}

# Mechanical retention profiles are deliberately generic.  They describe
# the *shape family* of an inner-wall friction feature, not a particular
# maker, lens, or third-party model.  The numeric values are only defaults;
# every field remains directly overridable in a job's ``[fit]`` table.
FRICTION_RIB_PROFILE_NAMES = ("light_tapered", "wide_tapered")


def normalize_friction_rib_profile(value: Any) -> str:
    """Return a canonical retention profile name.

    Keeping this small parser shared by config/model/CLI prevents spelling
    drift at the boundaries.  We intentionally accept only the two canonical
    names so a typo cannot silently select a different mechanical preset.
    """

    if value is None:
        return "light_tapered"
    profile = str(value).strip().lower()
    if profile not in FRICTION_RIB_PROFILE_NAMES:
        choices = ", ".join(FRICTION_RIB_PROFILE_NAMES)
        raise ValueError(f"friction_rib_profile must be one of: {choices}")
    return profile


def friction_rib_profile_defaults(
    profile: str,
    cavity_diameter_mm: float,
    side_height_mm: float,
) -> dict[str, float | int]:
    """Resolve defaults for fields omitted from a ``[fit]`` table.

    ``wide_tapered`` uses an 8-degree *angular* base footprint.  Its
    tangential width is therefore derived from the current cavity diameter,
    which keeps the visual/mechanical proportion stable across lens sizes.
    The caller is responsible for applying these values only to keys that
    were not explicitly supplied by the user.
    """

    canonical = normalize_friction_rib_profile(profile)
    try:
        cavity = float(cavity_diameter_mm)
        side = float(side_height_mm)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("cavity_diameter_mm and side_height_mm must be finite numbers") from exc
    if not math.isfinite(cavity) or cavity <= 0:
        raise ValueError("cavity_diameter_mm must be a positive finite number")
    if not math.isfinite(side):
        raise ValueError("side_height_mm must be finite")
    if canonical == "wide_tapered":
        # Arc length at the cavity radius for an 8-degree base angle.  The
        # model later recomputes/records the actual angle after any explicit
        # width override.
        defaults: dict[str, float | int] = {
            "friction_rib_count": 6,
            "friction_rib_protrusion_mm": 0.30,
            "friction_rib_width_mm": math.pi * cavity * 8.0 / 360.0,
            "friction_rib_height_mm": side - 1.50,
            "friction_rib_start_mm": 1.0,
        }
    else:
        defaults = {
            "friction_rib_count": 12,
            "friction_rib_protrusion_mm": 0.10,
            "friction_rib_width_mm": 1.20,
            "friction_rib_height_mm": 8.0,
            "friction_rib_start_mm": 1.0,
        }
    # Inputs are individually finite, but the derived arc length can still
    # overflow for an extreme (yet syntactically valid) diameter.  Reject it
    # here so direct callers and ``init`` cannot emit ``inf`` into TOML/SCAD.
    if not all(
        math.isfinite(float(value))
        for value in defaults.values()
        if isinstance(value, (int, float))
    ):
        raise ValueError("friction rib profile defaults must be finite")
    return defaults


@dataclass(frozen=True)
class PaletteSpec:
    """One output colour and its geometry role."""

    name: str
    index: int
    rgb: tuple[int, int, int]
    role: str
    height_mm: float
    required: bool
    detector: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "index": self.index,
            "rgb": list(self.rgb),
            "role": self.role,
            "height_mm": self.height_mm,
            "required": self.required,
        }
        if self.detector is not None:
            result["detector"] = self.detector
        return result


@dataclass(frozen=True)
class CircleSpec:
    center_px: tuple[float, float] | None
    radius_px: float | None
    allow_outside: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "center_px": list(self.center_px) if self.center_px is not None else None,
            "radius_px": self.radius_px,
            "allow_outside": self.allow_outside,
        }


@dataclass(frozen=True)
class PrefilterSpec:
    name: str = "median"
    size: int = 5
    radius: float = 0.8

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "size": self.size, "radius": self.radius}


@dataclass(frozen=True)
class CleanupSpec:
    enabled: bool = True
    max_area_px: int = 8
    max_dimension_px: int = 3
    ring_px: int = 2
    dominance: float = 0.60
    apply_to: tuple[str, ...] = ("relief",)

    def public(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_area_px": self.max_area_px,
            "max_dimension_px": self.max_dimension_px,
            "ring_px": self.ring_px,
            "dominance": self.dominance,
            "apply_to": list(self.apply_to),
        }


@dataclass(frozen=True)
class FitSpec:
    """Mechanical interface inputs for a fitted cap.

    ``measured_diameter_mm`` lives on :class:`PipelineConfig` because it is
    also the default face diameter.  The remaining values describe the
    replaceable liner and the conservative, parameterised body template.
    """

    foam_liner_status: str = "none"
    liner_material: str | None = None
    liner_thickness_mm: float | None = None
    compression_fraction: float = 0.20
    compression_is_assumption: bool = True
    wall_thickness_mm: float = 2.4
    bottom_thickness_mm: float = 2.0
    side_height_mm: float = 14.0
    bare_clearance_mm: float = 0.40
    retention_strategy: str = "auto"
    # Vertical interference ribs are a generic retention aid for fitted caps.
    # They are enabled by default, but the decision is explicit in the
    # normalized manifest so a user can opt out for a smooth inner wall.
    friction_ribs_enabled: bool = True
    friction_ribs_explicit: bool = False
    friction_rib_count: int = 12
    # 0.10 mm radial intrusion removes about 0.20 mm from the nominal cavity
    # diameter at each rib.  Whether that is actual interference (rather than
    # residual clearance) depends on ``bare_clearance_mm``; with a liner it is
    # only a light additional compression and must be checked on a coupon.
    friction_rib_protrusion_mm: float = 0.10
    friction_rib_width_mm: float = 1.20
    friction_rib_height_mm: float = 8.0
    friction_rib_start_mm: float = 1.0
    # Canonical shape family used only for omitted rib fields.  Kept at the
    # end of the dataclass to preserve positional compatibility with the
    # original FitSpec constructor.
    friction_rib_profile: str = "light_tapered"
    # ``init`` writes the resolved profile values for discoverability, but
    # marks them as derived so changing the mating diameter can safely
    # recompute scale-dependent defaults.  A user edit to any numeric field
    # automatically turns the values into explicit overrides at load time.
    friction_rib_profile_derived: bool = False
    friction_rib_profile_reference_cavity_mm: float | None = None

    def public(self) -> dict[str, Any]:
        return {
            "foam_liner_status": self.foam_liner_status,
            "liner_material": self.liner_material,
            "liner_thickness_mm": self.liner_thickness_mm,
            "compression_fraction": self.compression_fraction,
            "compression_is_assumption": self.compression_is_assumption,
            "wall_thickness_mm": self.wall_thickness_mm,
            "bottom_thickness_mm": self.bottom_thickness_mm,
            "side_height_mm": self.side_height_mm,
            "bare_clearance_mm": self.bare_clearance_mm,
            "friction_ribs_enabled": self.friction_ribs_enabled,
            "friction_ribs_explicit": self.friction_ribs_explicit,
            "friction_rib_count": self.friction_rib_count,
            "friction_rib_protrusion_mm": self.friction_rib_protrusion_mm,
            "friction_rib_width_mm": self.friction_rib_width_mm,
            "friction_rib_height_mm": self.friction_rib_height_mm,
            "friction_rib_start_mm": self.friction_rib_start_mm,
            "friction_rib_profile": self.friction_rib_profile,
            "friction_rib_profile_derived": self.friction_rib_profile_derived,
            "friction_rib_profile_reference_cavity_mm": self.friction_rib_profile_reference_cavity_mm,
            "retention_strategy": self.retention_strategy,
        }


@dataclass(frozen=True)
class PrintSpec:
    """Printer/tool hints.  They never alter artwork semantics."""

    nozzle_mm: float = 0.2
    layer_height_mm: float = 0.1
    printer: str = ""
    openscad_executable: str | None = None
    bambu_executable: str | None = None
    filament_slots: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "nozzle_mm": self.nozzle_mm,
            "layer_height_mm": self.layer_height_mm,
            "printer": self.printer,
            "openscad_executable": self.openscad_executable,
            "bambu_executable": self.bambu_executable,
            "filament_slots": list(self.filament_slots),
        }


@dataclass(frozen=True)
class PipelineConfig:
    """Validated, path-resolved configuration for one run."""

    config_path: Path
    job_slug: str
    source_path: Path
    output_dir: Path
    face_diameter_mm: float
    measured_diameter_mm: float | None
    grid_size: int
    nozzle_mm: float
    nozzle_explicit: bool
    safe_border_mm: float
    circle: CircleSpec
    prefilter: PrefilterSpec
    cleanup: CleanupSpec
    palette: tuple[PaletteSpec, ...]
    fit: FitSpec
    print: PrintSpec
    assembly_mode: str
    source_sha256: str | None
    metadata: dict[str, Any]
    raw: dict[str, Any]

    @property
    def base(self) -> PaletteSpec:
        return next(p for p in self.palette if p.role == "base")

    @property
    def relief(self) -> tuple[PaletteSpec, ...]:
        return tuple(p for p in self.palette if p.role == "relief")

    @property
    def is_fitted(self) -> bool:
        return self.measured_diameter_mm is not None

    @property
    def cavity_diameter_mm(self) -> float:
        """Return the nominal bare cavity used by the parametric template."""
        if self.measured_diameter_mm is None:
            raise ConfigError("measured_diameter_mm is required for a fitted model")
        if self.fit.foam_liner_status == "foam":
            assert self.fit.liner_thickness_mm is not None
            return self.measured_diameter_mm + 2.0 * self.fit.liner_thickness_mm * (
                1.0 - self.fit.compression_fraction
            )
        return self.measured_diameter_mm + self.fit.bare_clearance_mm

    @property
    def outer_diameter_mm(self) -> float:
        return self.cavity_diameter_mm + 2.0 * self.fit.wall_thickness_mm

    def public(self) -> dict[str, Any]:
        """Return a JSON-serialisable normalized representation."""
        return {
            "schema_version": 1,
            "job_slug": self.job_slug,
            "source_art": str(self.source_path),
            "output_dir": str(self.output_dir),
            "face_diameter_mm": self.face_diameter_mm,
            "measured_diameter_mm": self.measured_diameter_mm,
            "grid_size": self.grid_size,
            "nozzle_mm": self.nozzle_mm,
            "nozzle_explicit": self.nozzle_explicit,
            "safe_border_mm": self.safe_border_mm,
            "circle": self.circle.public(),
            "prefilter": self.prefilter.public(),
            "cleanup": self.cleanup.public(),
            "palette": {p.name: p.public() for p in self.palette},
            "fit": self.fit.public(),
            "print": self.print.public(),
            "assembly_mode": self.assembly_mode,
            "source_sha256": self.source_sha256,
            "metadata": self.metadata,
        }

    def portable_public(self) -> dict[str, Any]:
        """Return the normalized config with clone-relative core paths.

        ``PipelineConfig`` keeps resolved :class:`~pathlib.Path` objects for
        execution, but checked-in/run artifacts should not acquire a machine's
        absolute checkout prefix. Paths are emitted relative to the config
        directory, including parent segments for a source kept beside the job.
        Only a cross-volume path that cannot be represented relatively remains
        absolute (and is therefore called out by the release checklist).
        """
        payload = self.public()
        base = self.config_path.parent.resolve()
        for key, path in (("source_art", self.source_path), ("output_dir", self.output_dir)):
            try:
                payload[key] = Path(os.path.relpath(path.resolve(), base)).as_posix()
            except (ValueError, OSError):
                # Windows drives (or another filesystem boundary) have no
                # meaningful relative spelling; retaining the absolute path is
                # safer than silently binding a clone to the wrong file.
                payload[key] = str(path.resolve())
        # Desktop-tool locations are runtime details rather than artwork or
        # geometry inputs.  Keep only the executable basename in the archived
        # normalized config so an absolute `/Applications/...` path cannot
        # make otherwise identical jobs hash differently after cloning.  The
        # adapter report records the resolved executable basename and version;
        # the original TOML still retains the local path used for execution.
        print_payload = payload.get("print")
        if isinstance(print_payload, dict):
            for key in ("openscad_executable", "bambu_executable"):
                value = print_payload.get(key)
                if isinstance(value, str) and Path(value).is_absolute():
                    print_payload[key] = Path(value).name
        return payload

    def digest(self) -> str:
        # Resolve paths for the human-readable normalized config, but hash
        # paths relative to the config file whenever possible.  This keeps a
        # checked-in job reproducible after cloning the repository elsewhere;
        # the source SHA-256 still binds the run to the actual bytes.
        payload_obj = self.portable_public()
        payload = json.dumps(payload_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _number(value: Any, field: str, *, integer: bool = False) -> int | float:
    if isinstance(value, bool):
        raise ConfigError(f"{field} must be numeric")
    try:
        result = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} must be numeric") from exc
    if integer:
        try:
            if float(value) != float(result):
                raise ConfigError(f"{field} must be an integer")
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{field} must be numeric") from exc
    if not math.isfinite(float(result)):
        raise ConfigError(f"{field} must be finite")
    return result


def _boolean(value: Any, field: str, *, default: bool = False) -> bool:
    """Parse a configuration boolean without Python's truthiness traps.

    In particular, bool('false') is true. Job files are often edited by hand,
    so accepting only explicit boolean spellings keeps a typo from changing
    the circle exclusion or cleanup policy silently.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    raise ConfigError(f"{field} must be a boolean")


def _path(value: Any, field: str, config_dir: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty path")
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else (config_dir / candidate).resolve()


def _palette_items(value: Any) -> list[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        items: list[tuple[str, Mapping[str, Any]]] = []
        for name, spec in value.items():
            if not isinstance(name, str) or not isinstance(spec, Mapping):
                raise ConfigError("palette must map names to tables")
            items.append((name, spec))
        return items
    if isinstance(value, list):
        items = []
        for pos, spec in enumerate(value):
            if not isinstance(spec, Mapping) or not isinstance(spec.get("name"), str):
                raise ConfigError(f"palette[{pos}] needs a name and a table")
            items.append((str(spec["name"]), spec))
        return items
    raise ConfigError("palette must be a table/map or list")


def _detector(spec: Mapping[str, Any], field: str) -> dict[str, Any] | None:
    raw = spec.get("detect", spec.get("detector"))
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{field}.detect must be a table")
    channel = str(raw.get("channel", "r")).lower()
    if channel not in _CHANNELS:
        raise ConfigError(f"{field}.detect.channel must be r, g, or b")
    minimum = _number(raw.get("minimum", raw.get("min", 0)), f"{field}.detect.minimum")
    if not 0 <= minimum <= 255:
        raise ConfigError(f"{field}.detect.minimum must be in [0,255]")
    deltas_raw = raw.get("deltas", {})
    if not isinstance(deltas_raw, Mapping):
        raise ConfigError(f"{field}.detect.deltas must be a table")
    deltas: dict[str, int] = {}
    for other, amount in deltas_raw.items():
        key = str(other).lower()
        if key not in _CHANNELS or key == channel:
            raise ConfigError(f"{field}.detect.deltas has invalid channel {other!r}")
        value = _number(amount, f"{field}.detect.deltas.{key}", integer=True)
        if value < 0 or value > 255:
            raise ConfigError(f"{field}.detect.deltas.{key} must be in [0,255]")
        deltas[key] = value
    return {"channel": channel, "minimum": int(minimum), "deltas": deltas}


def _load_raw(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    try:
        if suffix == ".json":
            data = json.loads(text)
        elif suffix in {".toml", ".tml"}:
            try:
                import tomllib  # type: ignore
            except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
                import tomli as tomllib  # type: ignore
            data = tomllib.loads(text)
        else:
            raise ConfigError("config extension must be .json or .toml")
    except ConfigError:
        raise
    except Exception as exc:  # JSONDecodeError/TOMLDecodeError
        raise ConfigError(f"invalid config syntax in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config root must be an object/table")
    return data


def load_config(path: str | Path) -> PipelineConfig:
    """Load either JSON or TOML and validate the canonical schema."""
    config_path = Path(path).expanduser().resolve()
    raw = _load_raw(config_path)
    config_dir = config_path.parent

    schema_version = raw.get("schema_version", 1)
    if schema_version != 1:
        raise ConfigError(f"unsupported schema_version {schema_version!r}; expected 1")

    # A few explicit aliases make migration from a hand-written manifest easy,
    # while the normalized output always uses the canonical names.
    job_slug = raw.get("job_slug", raw.get("job", {}).get("slug") if isinstance(raw.get("job"), Mapping) else None)
    if not isinstance(job_slug, str) or not job_slug.strip():
        raise ConfigError("job_slug is required")
    job_slug = job_slug.strip()
    if not _NAME_RE.match(job_slug):
        raise ConfigError("job_slug may contain only letters, digits, _, ., and -")

    # Accept the short flat spelling used by the first release and the
    # grouped spelling used in the documentation.  The normalized manifest
    # always emits the flat canonical fields below.
    art_section = raw.get("art", {})
    if not isinstance(art_section, Mapping):
        art_section = {}
    # ``art_master`` appeared in early grouped manifests both as a plain
    # path and as ``{path = ...}``.  Resolve the table form before validation;
    # otherwise a documented migration alias is passed to ``_path`` as a
    # mapping and is rejected as if it were malformed input.
    source_value = raw.get("source_art")
    if source_value is None:
        art_master_value = raw.get("art_master")
        if isinstance(art_master_value, Mapping):
            source_value = art_master_value.get("path", art_master_value.get("source_art"))
        else:
            source_value = art_master_value
    if source_value is None:
        source_value = raw.get("source")
    source_path = _path(source_value, "source_art", config_dir)
    if not source_path.is_file():
        raise ConfigError(f"source_art does not exist: {source_path}")

    output_value = raw.get("output_dir", raw.get("output", "build"))
    output_dir = _path(output_value, "output_dir", config_dir)

    face_section = raw.get("face", {})
    if not isinstance(face_section, Mapping):
        raise ConfigError("face must be a table/object")
    measured_raw = raw.get("measured_diameter_mm", face_section.get("measured_diameter_mm"))
    measured = None if measured_raw is None else float(_number(measured_raw, "measured_diameter_mm"))
    if measured is not None and measured <= 0:
        raise ConfigError("measured_diameter_mm must be > 0")
    face_raw_value = raw.get(
        "face_diameter_mm",
        raw.get("face_target_mm", face_section.get("face_target_mm", face_section.get("face_diameter_mm"))),
    )
    if face_raw_value is None:
        face = measured
    else:
        face = float(_number(face_raw_value, "face_diameter_mm"))
    if face is None or face <= 0:
        raise ConfigError("face_diameter_mm (or measured_diameter_mm) is required and must be > 0")
    grid_raw = raw.get("grid_size", face_section.get("grid_size", face_section.get("grid", 1000)))
    if isinstance(grid_raw, (list, tuple)):
        if len(grid_raw) != 2 or int(_number(grid_raw[0], "face.grid[0]", integer=True)) != int(_number(grid_raw[1], "face.grid[1]", integer=True)):
            raise ConfigError("this release requires a square grid; set grid_size or face.grid=[n,n]")
        grid_raw = grid_raw[0]
    grid = _number(grid_raw, "grid_size", integer=True)
    if grid < 64 or grid > 4096:
        raise ConfigError("grid_size must be between 64 and 4096")
    print_section = raw.get("print", {})
    if not isinstance(print_section, Mapping):
        raise ConfigError("print must be a table/object")
    nozzle_explicit = (
        "nozzle_mm" in raw
        or "nozzle_mm" in print_section
        or "nozzle_diameter_mm" in print_section
    )
    nozzle = _number(raw.get("nozzle_mm", print_section.get("nozzle_mm", print_section.get("nozzle_diameter_mm", 0.2))), "nozzle_mm")
    if nozzle <= 0:
        raise ConfigError("nozzle_mm must be > 0")
    safe = _number(raw.get("safe_border_mm", face_section.get("safe_border_mm", 0.0)), "safe_border_mm")
    if safe < 0 or safe >= face / 2:
        raise ConfigError("safe_border_mm must be >= 0 and less than half the face diameter")

    circle_raw = raw.get("circle", face_section.get("circle", {}))
    if not isinstance(circle_raw, Mapping):
        raise ConfigError("circle must be a table/object")
    center_raw = circle_raw.get("center_px")
    center: tuple[float, float] | None
    if center_raw is None:
        center = None
    else:
        if not isinstance(center_raw, (list, tuple)) or len(center_raw) != 2:
            raise ConfigError("circle.center_px must contain [x, y]")
        center = (float(_number(center_raw[0], "circle.center_px[0]")), float(_number(center_raw[1], "circle.center_px[1]")))
    radius_raw = circle_raw.get("radius_px")
    radius = None if radius_raw is None else float(_number(radius_raw, "circle.radius_px"))
    if radius is not None and radius <= 0:
        raise ConfigError("circle.radius_px must be > 0")
    circle = CircleSpec(center, radius, _boolean(circle_raw.get("allow_outside"), "circle.allow_outside"))

    pre_raw = raw.get("prefilter", art_section.get("prefilter", {}))
    if isinstance(pre_raw, str):
        pre_raw = {"name": pre_raw}
    if not isinstance(pre_raw, Mapping):
        raise ConfigError("prefilter must be a table/object")
    pre_name = str(pre_raw.get("name", pre_raw.get("filter", art_section.get("filter", "median")))).lower()
    pre_name = {"median_5": "median", "gaussian_08": "gaussian"}.get(pre_name, pre_name)
    if pre_name not in {"none", "median", "gaussian"}:
        raise ConfigError("prefilter.name must be none, median, or gaussian")
    pre_size = _number(pre_raw.get("size", 5), "prefilter.size", integer=True)
    # Median kernels must be odd so Pillow's result is deterministic.  The
    # upper bound avoids accidentally allocating an impractically large
    # neighbourhood from an untrusted config file.
    if pre_size < 3 or pre_size > 15 or pre_size % 2 == 0:
        raise ConfigError("prefilter.size must be an odd integer in [3,15]")
    pre_radius = _number(pre_raw.get("radius", 0.8), "prefilter.radius")
    if pre_radius < 0:
        raise ConfigError("prefilter.radius must be >= 0")
    prefilter = PrefilterSpec(pre_name, int(pre_size), float(pre_radius))

    clean_raw = raw.get("cleanup", art_section.get("cleanup", {}))
    if not isinstance(clean_raw, Mapping):
        raise ConfigError("cleanup must be a table/object")
    apply_to_raw = clean_raw.get("apply_to", ["relief"])
    if isinstance(apply_to_raw, str):
        apply_to_raw = [apply_to_raw]
    if not isinstance(apply_to_raw, (list, tuple)):
        raise ConfigError("cleanup.apply_to must be a list")
    apply_to = tuple(str(x).lower() for x in apply_to_raw)
    if any(x not in {"base", "relief", "all"} for x in apply_to):
        raise ConfigError("cleanup.apply_to values must be base, relief, or all")
    cleanup = CleanupSpec(
        _boolean(clean_raw.get("enabled"), "cleanup.enabled", default=True),
        int(_number(clean_raw.get("max_area_px", art_section.get("minimum_component_area_px", 8)), "cleanup.max_area_px", integer=True)),
        int(_number(clean_raw.get("max_dimension_px", art_section.get("minimum_component_dimension_px", 3)), "cleanup.max_dimension_px", integer=True)),
        int(_number(clean_raw.get("ring_px", 2), "cleanup.ring_px", integer=True)),
        float(_number(clean_raw.get("dominance", 0.60), "cleanup.dominance")),
        apply_to,
    )
    if cleanup.max_area_px < 1 or cleanup.max_dimension_px < 1 or cleanup.ring_px < 1:
        raise ConfigError("cleanup pixel limits must be positive")
    if not 0.5 <= cleanup.dominance <= 1:
        raise ConfigError("cleanup.dominance must be in [0.5,1]")

    palette_value = raw.get("palette")
    if palette_value is None:
        raise ConfigError("palette is required; declare at least one base and one relief colour")
    palette_base_label: str | None = None
    if isinstance(palette_value, Mapping) and "colors" in palette_value:
        palette_base_label = palette_value.get("base_label") if isinstance(palette_value.get("base_label"), str) else None
        palette_value = palette_value.get("colors")
    specs: list[PaletteSpec] = []
    for position, (name, item) in enumerate(_palette_items(palette_value)):
        if not _NAME_RE.match(name):
            raise ConfigError(f"palette name {name!r} is not filename-safe")
        if name.lower() in {"outside", "base", "relief", "safe-border", "process-master", "process-preview", "role-overlay"}:
            raise ConfigError(f"palette name {name!r} is reserved for pipeline outputs")
        rgb_raw = item.get("rgb", item.get("color"))
        if not isinstance(rgb_raw, (list, tuple)) or len(rgb_raw) != 3:
            raise ConfigError(f"palette.{name}.rgb must contain three values")
        rgb_values = tuple(int(_number(v, f"palette.{name}.rgb", integer=True)) for v in rgb_raw)
        if any(v < 0 or v > 255 for v in rgb_values):
            raise ConfigError(f"palette.{name}.rgb values must be in [0,255]")
        index = int(_number(item.get("index", position), f"palette.{name}.index", integer=True))
        role = str(item.get("role", "relief")).lower()
        role = {
            "face_base": "base",
            "base_only": "base",
            "face_base/base_only": "base",
            "positive_relief": "relief",
            "separate_inlay": "relief",
        }.get(role, role)
        if role not in {"base", "relief"}:
            raise ConfigError(f"palette.{name}.role must be base or relief")
        height = float(_number(item.get("height_mm", 0.0 if role == "base" else 0.4), f"palette.{name}.height_mm"))
        if height < 0:
            raise ConfigError(f"palette.{name}.height_mm must be >= 0")
        if role == "base" and height != 0:
            raise ConfigError(f"palette.{name}: base height_mm must be 0")
        if role == "relief" and height <= 0:
            raise ConfigError(f"palette.{name}: relief height_mm must be > 0")
        specs.append(
            PaletteSpec(
                name,
                index,
                rgb_values,
                role,
                height,
                _boolean(item.get("required"), f"palette.{name}.required", default=role == "base"),
                _detector(item, f"palette.{name}"),
            )
        )
    # Output filenames are consumed on case-insensitive filesystems (the
    # common macOS/Windows setup).  Names that differ only by case would
    # silently overwrite one another there, so reject that ambiguity at the
    # config gate instead of producing a non-auditable mask set.
    if len({p.name.casefold() for p in specs}) != len(specs):
        raise ConfigError("palette names must be unique ignoring case")
    if len({p.index for p in specs}) != len(specs):
        raise ConfigError("palette indices must be unique")
    if len(specs) > 16:
        raise ConfigError("at most 16 palette colours are supported by the indexed process master")
    specs.sort(key=lambda p: p.index)
    if [p.index for p in specs] != list(range(len(specs))):
        raise ConfigError("palette indices must be contiguous starting at 0")
    if sum(p.role == "base" for p in specs) != 1:
        raise ConfigError("palette must contain exactly one role=base colour")
    if not any(p.role == "relief" for p in specs):
        raise ConfigError("palette must contain at least one role=relief colour")

    # Mechanical fit is deliberately explicit and independent of optical
    # identity.  ``assembly_mode=auto`` is resolved by the model adapter; it
    # is not a question the user has to answer for every lens.
    fit_raw = raw.get("fit", {})
    if not isinstance(fit_raw, Mapping):
        raise ConfigError("fit must be a table/object")
    foam_status = str(fit_raw.get("foam_liner_status", fit_raw.get("foam", "none"))).lower()
    if isinstance(fit_raw.get("foam"), bool):
        foam_status = "foam" if fit_raw.get("foam") else "none"
    if foam_status not in {"none", "foam"}:
        raise ConfigError("fit.foam_liner_status must be 'none' or 'foam'")
    liner_raw = fit_raw.get("liner_thickness_mm", fit_raw.get("foam_thickness_mm"))
    liner = None if liner_raw is None else float(_number(liner_raw, "fit.liner_thickness_mm"))
    if liner is not None and liner <= 0:
        raise ConfigError("fit.liner_thickness_mm must be > 0")
    if foam_status == "foam" and liner is None:
        raise ConfigError("fit.liner_thickness_mm is required when foam_liner_status='foam'")
    # A compression fraction has physical meaning only for a compressible
    # liner.  Keep the historical 20% provisional assumption for foam jobs,
    # but make a bare-wall job unambiguously zero when the field is omitted.
    compression_default = 0.20 if foam_status == "foam" else 0.0
    compression = float(
        _number(fit_raw.get("compression_fraction", compression_default), "fit.compression_fraction")
    )
    if compression < 0 or compression >= 1:
        raise ConfigError("fit.compression_fraction must be in [0,1)")
    wall = float(_number(fit_raw.get("wall_thickness_mm", 2.4), "fit.wall_thickness_mm"))
    bottom = float(_number(fit_raw.get("bottom_thickness_mm", 2.0), "fit.bottom_thickness_mm"))
    side = float(_number(fit_raw.get("side_height_mm", 14.0), "fit.side_height_mm"))
    bare_clearance = float(_number(fit_raw.get("bare_clearance_mm", 0.40), "fit.bare_clearance_mm"))
    if wall < 0.4 or bottom < 0.8 or side < 1.0 or bare_clearance < 0:
        raise ConfigError("fit wall/bottom/side/clearance values are outside safe limits")
    try:
        rib_profile = normalize_friction_rib_profile(
            fit_raw.get("friction_rib_profile", "light_tapered")
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    # Retention ribs are intentionally independent of optical identity.  The
    # canonical spelling is ``friction_ribs_enabled``; the short ``friction_ribs``
    # alias keeps hand-written jobs readable.  Missing values mean enabled,
    # while ``friction_ribs_explicit`` lets the manifest distinguish a default
    # from a deliberate opt-out.
    friction_key = (
        "friction_ribs_enabled"
        if "friction_ribs_enabled" in fit_raw
        else "friction_ribs"
        if "friction_ribs" in fit_raw
        else None
    )
    friction_enabled = _boolean(
        fit_raw.get(friction_key) if friction_key is not None else None,
        f"fit.{friction_key or 'friction_ribs_enabled'}",
        default=True,
    )
    friction_explicit = _boolean(
        fit_raw.get("friction_ribs_explicit"),
        "fit.friction_ribs_explicit",
        default=friction_key is not None,
    )
    # Resolve the profile against the derived cavity, while preserving the
    # precedence rule that an explicitly supplied field always wins.  A
    # process-only config has no measured mating diameter yet; its face size
    # is a provisional scale for the width default and will be recomputed at
    # the model gate once a measurement is present.
    profile_diameter = float(measured if measured is not None else face)
    profile_cavity = (
        profile_diameter + 2.0 * liner * (1.0 - compression)
        if foam_status == "foam" and liner is not None
        else profile_diameter + bare_clearance
    )
    try:
        profile_defaults = friction_rib_profile_defaults(
            rib_profile,
            profile_cavity,
            side,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    # ``lens-cap init`` keeps resolved numbers visible in TOML, but marks
    # them as derived.  If the user later changes the measured diameter (or
    # foam stack), recognise unchanged derived values and recompute the
    # scale-dependent profile instead of freezing the old circumference.  A
    # deliberate edit to any numeric rib field is treated as an explicit
    # override and is preserved exactly.
    profile_derived = False
    profile_reference: float | None = None
    if _boolean(
        fit_raw.get("friction_rib_profile_derived"),
        "fit.friction_rib_profile_derived",
        default=False,
    ):
        reference_raw = fit_raw.get("friction_rib_profile_reference_cavity_mm")
        if reference_raw is not None:
            reference = float(
                _number(
                    reference_raw,
                    "fit.friction_rib_profile_reference_cavity_mm",
                )
            )
            if reference <= 0:
                raise ConfigError("fit.friction_rib_profile_reference_cavity_mm must be > 0")
            try:
                reference_defaults = friction_rib_profile_defaults(rib_profile, reference, side)
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
            profile_keys = (
                "friction_rib_count",
                "friction_rib_protrusion_mm",
                "friction_rib_width_mm",
                "friction_rib_height_mm",
                "friction_rib_start_mm",
            )
            profile_derived = True
            for key in profile_keys:
                supplied = fit_raw.get(key)
                if supplied is None:
                    continue
                try:
                    supplied_value = float(supplied)
                    expected_value = float(reference_defaults[key])
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ConfigError(f"fit.{key} must be numeric") from exc
                if not math.isfinite(supplied_value) or not math.isclose(
                    supplied_value, expected_value, rel_tol=0.0, abs_tol=1e-7
                ):
                    profile_derived = False
                    break
            if profile_derived:
                profile_reference = profile_cavity

    def profile_value(key: str) -> Any:
        if profile_derived:
            return profile_defaults[key]
        return fit_raw.get(key, profile_defaults[key])

    rib_count = int(
        _number(
            profile_value("friction_rib_count"),
            "fit.friction_rib_count",
            integer=True,
        )
    )
    rib_protrusion = float(
        _number(
            profile_value("friction_rib_protrusion_mm"),
            "fit.friction_rib_protrusion_mm",
        )
    )
    rib_width = float(
        _number(
            profile_value("friction_rib_width_mm"),
            "fit.friction_rib_width_mm",
        )
    )
    rib_height = float(
        _number(
            profile_value("friction_rib_height_mm"),
            "fit.friction_rib_height_mm",
        )
    )
    rib_start = float(
        _number(
            profile_value("friction_rib_start_mm"),
            "fit.friction_rib_start_mm",
        )
    )
    # A process-only artwork job has no fitted body yet, so do not reject it
    # for a retention dimension that the model stage will never consume.  The
    # full guards apply once an actual mating diameter is present.
    if friction_enabled and measured is not None:
        if rib_count < 3 or rib_count > 128:
            raise ConfigError("fit.friction_rib_count must be an integer in [3,128]")
        if rib_protrusion <= 0:
            raise ConfigError("fit.friction_rib_protrusion_mm must be > 0")
        if rib_width <= 0 or rib_height <= 0:
            raise ConfigError("fit friction rib width/height must be > 0")
        if rib_start < 0 or rib_start + rib_height > side:
            raise ConfigError("fit friction ribs must lie within side_height_mm")
        # Use the actual mating diameter for the circumferential pitch.  A
        # synthetic 1 mm floor would make tiny, otherwise valid fixtures pass
        # the width/protrusion gate and then fail later in the model stage.
        # Angular width follows the derived cavity, while the broad
        # intrusion sanity bound remains tied to the actual mating diameter
        # (or the declared face scale for a process-only job).
        pitch_diameter = profile_cavity
        mating_diameter = float(measured if measured is not None else face)
        pitch = math.pi * pitch_diameter / rib_count
        if rib_width >= pitch * 0.9:
            raise ConfigError("fit.friction_rib_width_mm is too wide for the selected rib count")
        if rib_protrusion >= mating_diameter / 4.0:
            raise ConfigError("fit.friction_rib_protrusion_mm is too large for the mating diameter")
        rib_angle_deg = min(
            8.0,
            360.0 * rib_width / (math.pi * pitch_diameter),
            180.0 / rib_count,
        )
        tip_angle_deg = rib_angle_deg * 0.55
        tip_radius = pitch_diameter / 2.0 - rib_protrusion
        tip_chord_mm = 2.0 * tip_radius * math.sin(math.radians(tip_angle_deg / 2.0))
        if tip_chord_mm < nozzle:
            raise ConfigError(
                "fit friction rib contact-tip width must be >= nozzle_mm; "
                "increase friction_rib_width_mm"
            )
        if foam_status == "foam":
            assert liner is not None
            compressed_radial_gap = liner * (1.0 - compression)
            if rib_protrusion >= compressed_radial_gap - 1e-9:
                raise ConfigError(
                    "fit.friction_rib_protrusion_mm must stay below the compressed foam radial gap"
                )
    # Guard derived dimensions as well as their individual finite inputs. A
    # pair of very large, individually finite TOML values can overflow when
    # summed and would otherwise produce an invalid OpenSCAD literal later.
    if measured is not None:
        derived_cavity = (
            measured + 2.0 * liner * (1.0 - compression)
            if foam_status == "foam" and liner is not None
            else measured + bare_clearance
        )
        derived_outer = derived_cavity + 2.0 * wall
        derived_height = bottom + side
        if not all(math.isfinite(value) for value in (derived_cavity, derived_outer, derived_height)):
            raise ConfigError("derived fitted-cap dimensions must be finite")
        if derived_outer <= derived_cavity or derived_height <= 0:
            raise ConfigError("derived fitted-cap dimensions are invalid")
    fit = FitSpec(
        foam_liner_status=foam_status,
        liner_material=(str(fit_raw["liner_material"]) if fit_raw.get("liner_material") is not None else None),
        liner_thickness_mm=liner,
        compression_fraction=compression,
        compression_is_assumption=_boolean(
            fit_raw.get("compression_is_assumption"),
            "fit.compression_is_assumption",
            default="compression_fraction" not in fit_raw and foam_status == "foam",
        ),
        wall_thickness_mm=wall,
        bottom_thickness_mm=bottom,
        side_height_mm=side,
        bare_clearance_mm=bare_clearance,
        friction_ribs_enabled=friction_enabled,
        friction_ribs_explicit=friction_explicit,
        friction_rib_count=rib_count,
        friction_rib_protrusion_mm=rib_protrusion,
        friction_rib_width_mm=rib_width,
        friction_rib_height_mm=rib_height,
        friction_rib_start_mm=rib_start,
        retention_strategy=str(fit_raw.get("retention_strategy", "auto")),
        friction_rib_profile=rib_profile,
        friction_rib_profile_derived=profile_derived,
        friction_rib_profile_reference_cavity_mm=profile_reference,
    )

    layer = float(_number(print_section.get("layer_height_mm", 0.1), "print.layer_height_mm"))
    if layer <= 0 or layer > nozzle:
        raise ConfigError("print.layer_height_mm must be > 0 and <= nozzle_mm")
    slots_raw = print_section.get("filament_slots", ())
    if isinstance(slots_raw, str):
        slots_raw = [slots_raw]
    if not isinstance(slots_raw, (list, tuple)):
        raise ConfigError("print.filament_slots must be a list")
    print_spec = PrintSpec(
        nozzle_mm=float(nozzle),
        layer_height_mm=layer,
        printer=str(print_section.get("printer", "")),
        openscad_executable=(str(print_section["openscad_executable"]) if print_section.get("openscad_executable") else None),
        bambu_executable=(str(print_section["bambu_executable"]) if print_section.get("bambu_executable") else None),
        filament_slots=tuple(str(item) for item in slots_raw),
    )
    assembly_mode = str(raw.get("assembly_mode", "auto")).lower()
    if assembly_mode not in {"auto", "integrated_part", "separate_parts", "inlay"}:
        raise ConfigError("assembly_mode must be auto, integrated_part, separate_parts, or inlay")
    metadata_raw = raw.get("metadata", {})
    if not isinstance(metadata_raw, Mapping):
        raise ConfigError("metadata must be a table/object")
    metadata = dict(metadata_raw)
    adapter_nominal_raw = metadata.get("adapter_nominal_ring_mm")
    adapter_wall_raw = metadata.get("adapter_radial_wall_mm")
    if (adapter_nominal_raw is None) != (adapter_wall_raw is None):
        raise ConfigError(
            "metadata.adapter_nominal_ring_mm and metadata.adapter_radial_wall_mm must be declared together"
        )
    if adapter_nominal_raw is not None:
        if measured is None:
            raise ConfigError("adapter envelope metadata requires measured_diameter_mm")
        adapter_nominal = float(
            _number(adapter_nominal_raw, "metadata.adapter_nominal_ring_mm")
        )
        adapter_wall = float(
            _number(adapter_wall_raw, "metadata.adapter_radial_wall_mm")
        )
        if adapter_nominal <= 0 or adapter_wall < 0:
            raise ConfigError(
                "metadata adapter nominal diameter must be > 0 and radial wall must be >= 0"
            )
        adapter_derived = adapter_nominal + 2.0 * adapter_wall
        if not math.isclose(adapter_derived, measured, rel_tol=0.0, abs_tol=0.05):
            raise ConfigError(
                "measured_diameter_mm must equal metadata.adapter_nominal_ring_mm + "
                "2 * metadata.adapter_radial_wall_mm (within 0.05 mm)"
            )
        declared_derived = metadata.get("adapter_derived_mating_diameter_mm")
        if declared_derived is not None:
            declared_value = float(
                _number(
                    declared_derived,
                    "metadata.adapter_derived_mating_diameter_mm",
                )
            )
            if not math.isclose(declared_value, adapter_derived, rel_tol=0.0, abs_tol=1e-6):
                raise ConfigError(
                    "metadata.adapter_derived_mating_diameter_mm disagrees with nominal ring + 2 * wall"
                )
        metadata["adapter_nominal_ring_mm"] = adapter_nominal
        metadata["adapter_radial_wall_mm"] = adapter_wall
        metadata["adapter_derived_mating_diameter_mm"] = adapter_derived
    if palette_base_label is not None:
        metadata.setdefault("palette_base_label", palette_base_label)
    metadata.setdefault("face_target_derived_from_measured", face_raw_value is None and measured is not None)
    try:
        # Reports are JSON contracts.  TOML permits native date/time and NaN
        # values that ``json.dumps`` would otherwise reject later, after a
        # marker or partial output has already been written; fail at the
        # configuration boundary with an actionable message instead.
        json.dumps(metadata, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ConfigError("metadata must contain only JSON-serialisable values") from exc
    source_sha256 = raw.get("source_sha256")
    if source_sha256 is not None:
        if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256):
            raise ConfigError("source_sha256 must be a 64-character hexadecimal digest")

    return PipelineConfig(
        config_path=config_path,
        job_slug=job_slug,
        source_path=source_path,
        output_dir=output_dir,
        face_diameter_mm=float(face),
        measured_diameter_mm=measured,
        grid_size=int(grid),
        nozzle_mm=float(nozzle),
        nozzle_explicit=nozzle_explicit,
        safe_border_mm=float(safe),
        circle=circle,
        prefilter=prefilter,
        cleanup=cleanup,
        palette=tuple(specs),
        fit=fit,
        print=print_spec,
        assembly_mode=assembly_mode,
        source_sha256=(str(source_sha256).lower() if source_sha256 is not None else None),
        metadata=metadata,
        raw=raw,
    )


def write_normalized_config(config: PipelineConfig, path: Path) -> None:
    """Write the exact normalized config used by a run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.portable_public(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def template_config(
    source: str,
    face_diameter_mm: float | None,
    output_dir: str = "build",
    nozzle_mm: float = 0.2,
) -> dict[str, Any]:
    """Return a portable starter config for ``lens-cap init``."""
    manufacturing_grid = 1000
    if face_diameter_mm is not None and face_diameter_mm > 0 and nozzle_mm > 0:
        # Geometry coordinates need finer sampling than the extrusion width:
        # equating one raster cell with one nozzle width visibly stair-steps
        # type and arcs before the slicer sees them. Two deterministic samples
        # per nozzle retain sub-bead path placement while the independent
        # minimum-feature gate still rejects unprintable hairlines.
        manufacturing_grid = max(
            64,
            min(1600, int(math.ceil(2.0 * face_diameter_mm / nozzle_mm - 1e-9))),
        )
    return {
        "schema_version": 1,
        "job_slug": "my-lens-cap",
        "source_art": source,
        "output_dir": output_dir,
        "face_diameter_mm": face_diameter_mm,
        "grid_size": manufacturing_grid,
        "nozzle_mm": nozzle_mm,
        "safe_border_mm": 0.4,
        "circle": {"center_px": None, "radius_px": None, "allow_outside": False},
        "prefilter": {"name": "median", "size": 5, "radius": 0.8},
        "cleanup": {"enabled": True, "max_area_px": 8, "max_dimension_px": 3, "ring_px": 2, "dominance": 0.6, "apply_to": ["relief"]},
        "fit": {
            "foam_liner_status": "none",
            "compression_fraction": 0.0,
            "compression_is_assumption": False,
            "wall_thickness_mm": 2.4,
            "bottom_thickness_mm": 2.0,
            "side_height_mm": 14.0,
            "bare_clearance_mm": 0.40,
            "friction_ribs_enabled": True,
            "friction_ribs_explicit": False,
            "friction_rib_profile": "light_tapered",
            "friction_rib_count": 12,
            "friction_rib_protrusion_mm": 0.10,
            "friction_rib_width_mm": 1.20,
            "friction_rib_height_mm": 8.0,
            "friction_rib_start_mm": 1.0,
            "retention_strategy": "auto",
        },
        "print": {
            "nozzle_mm": nozzle_mm,
            "layer_height_mm": min(0.1, nozzle_mm),
            "printer": "",
            "filament_slots": [],
        },
        "assembly_mode": "auto",
        "palette": {
            "black": {"index": 0, "rgb": [17, 18, 17], "role": "base", "height_mm": 0.0, "required": True},
            "gray": {"index": 1, "rgb": [116, 116, 113], "role": "relief", "height_mm": 0.4, "required": False},
            "ivory": {"index": 2, "rgb": [242, 231, 211], "role": "relief", "height_mm": 0.6, "required": False},
            "red": {"index": 3, "rgb": [190, 31, 35], "role": "relief", "height_mm": 0.8, "required": False, "detect": {"channel": "r", "minimum": 80, "deltas": {"g": 45, "b": 40}}},
        },
    }
