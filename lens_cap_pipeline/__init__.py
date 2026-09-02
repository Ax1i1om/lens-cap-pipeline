"""Open, reproducible lens-cap artwork processing tools."""

from .config import ConfigError, FitSpec, PipelineConfig, PrintSpec, load_config
from .external import ExternalResult, ExternalToolError, export_openscad, write_bambu_handoff
from .model import ModelError, ModelResult, generate_model, generate_scad
from .process import ProcessError, process, sha256_file
from .validate import validate, validate_job

__all__ = [
    "ConfigError",
    "ExternalResult",
    "ExternalToolError",
    "FitSpec",
    "ModelError",
    "ModelResult",
    "PipelineConfig",
    "ProcessError",
    "PrintSpec",
    "export_openscad",
    "generate_model",
    "generate_scad",
    "load_config",
    "process",
    "sha256_file",
    "validate",
    "validate_job",
    "write_bambu_handoff",
]
__version__ = "0.1.0"
