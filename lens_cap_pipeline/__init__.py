"""Open, reproducible lens-cap artwork processing tools."""

from .config import (
    FRICTION_RIB_PROFILE_NAMES,
    ConfigError,
    FitSpec,
    PipelineConfig,
    PrintSpec,
    friction_rib_profile_defaults,
    load_config,
    normalize_friction_rib_profile,
)
from .external import ExternalResult, ExternalToolError, export_openscad, write_bambu_handoff
from .model import ModelError, ModelResult, generate_model, generate_scad
from .process import ProcessError, process, sha256_file
from .validate import validate, validate_job

__all__ = [
    "ConfigError",
    "FRICTION_RIB_PROFILE_NAMES",
    "ExternalResult",
    "ExternalToolError",
    "FitSpec",
    "ModelError",
    "ModelResult",
    "PipelineConfig",
    "ProcessError",
    "PrintSpec",
    "export_openscad",
    "friction_rib_profile_defaults",
    "generate_model",
    "generate_scad",
    "load_config",
    "normalize_friction_rib_profile",
    "process",
    "sha256_file",
    "validate",
    "validate_job",
    "write_bambu_handoff",
]
__version__ = "0.1.0a1"
