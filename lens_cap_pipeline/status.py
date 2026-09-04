"""Shared deterministic process-report guards."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

REQUIRED_PROCESS_CHECKS = {
    "overflow_guard",
    "source_polarity",
    "role_partition",
    "color_partition",
    "safe_border_base_only",
    "outside_alpha_zero",
    "required_palette_outputs",
}


def failed_boolean_checks(report: Mapping[str, Any]) -> set[str]:
    """Return missing required checks and every declared non-passing check."""

    checks = report.get("checks")
    if not isinstance(checks, Mapping):
        return {"missing_checks"}
    if not REQUIRED_PROCESS_CHECKS.issubset(checks):
        return {"missing_checks"}
    return {
        str(name) for name, value in checks.items() if value is not True
    }


def accepted_process_status(report: Any) -> bool:
    """Return true when all deterministic artwork-integrity checks passed."""

    if not isinstance(report, Mapping):
        return False
    return bool(
        report.get("status") == "passed"
        and report.get("integrity_status", "passed") == "passed"
        and failed_boolean_checks(report) == set()
    )


def process_production_status(report: Any) -> str | None:
    """Return ``passed`` for a valid process report, else ``None``.

    Unsliced printability is reported separately by the 3MF stage.
    """

    if not accepted_process_status(report):
        return None
    return "passed"
