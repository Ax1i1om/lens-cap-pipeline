from __future__ import annotations

from copy import deepcopy

from lens_cap_pipeline.status import accepted_process_status, process_production_status


def _integrity_checks() -> dict[str, bool]:
    return {
        "overflow_guard": True,
        "source_polarity": True,
        "role_partition": True,
        "color_partition": True,
        "safe_border_base_only": True,
        "outside_alpha_zero": True,
        "required_palette_outputs": True,
    }


def test_current_integrity_report_is_a_valid_process_pass() -> None:
    report = {
        "status": "passed",
        "integrity_status": "passed",
        "checks": _integrity_checks(),
    }

    assert accepted_process_status(report) is True
    assert process_production_status(report) == "passed"


def test_any_declared_false_check_blocks_downstream_consumers() -> None:
    report = {
        "status": "passed",
        "integrity_status": "passed",
        "checks": _integrity_checks(),
    }

    unsafe = deepcopy(report)
    unsafe["checks"]["future_integrity_gate"] = False
    assert accepted_process_status(unsafe) is False
    assert process_production_status(unsafe) is None
