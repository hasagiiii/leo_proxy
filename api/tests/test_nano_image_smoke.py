from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "smoke_nano_images.py"
SPEC = importlib.util.spec_from_file_location("smoke_nano_images", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_full_contract_matrix_covers_120_parameter_combinations():
    cases = MODULE.all_contract_cases()

    assert len(cases) == 120
    assert {case.model for case in cases} == {"nano-banana-2", "nano-banana-pro"}
    assert {case.size for case in cases} == {"SMALL", "MEDIUM", "LARGE"}
    assert {case.aspect_ratio for case in cases} == set(MODULE.ASPECT_RATIOS)
    assert {case.mode for case in cases if case.model == "nano-banana-2"} == {
        "text-to-image",
        "image-to-image",
    }
    assert {case.mode for case in cases if case.model == "nano-banana-pro"} == {
        "text-to-image",
        "image-to-image",
    }


@pytest.mark.parametrize("case", MODULE.all_contract_cases(), ids=lambda case: case.name)
def test_every_contract_smoke_case_passes(case):
    result = MODULE.validate_contract_case(case)

    assert result["passed"] is True, result
    assert result["checks"]["public_fixed"] is True
    assert result["checks"]["request_dimensions"] is True
    assert result["checks"]["estimated_credits"] is True
    assert result["checks"]["direct_quote"] is True


def test_fixed_field_smoke_checks_all_pass():
    results = MODULE.validate_fixed_field_contracts()

    assert len(results) == 9
    assert all(result["passed"] is True for result in results), results


def test_live_smoke_matrix_is_bounded_but_covers_all_axes():
    cases = MODULE.LIVE_CASES

    assert len(cases) == 12
    assert {case.model for case in cases} == {"nano-banana-2", "nano-banana-pro"}
    assert {case.mode for case in cases if case.model == "nano-banana-2"} == {
        "text-to-image",
        "image-to-image",
    }
    assert {case.mode for case in cases if case.model == "nano-banana-pro"} == {
        "text-to-image",
        "image-to-image",
    }
    assert {case.size for case in cases} == {"SMALL", "MEDIUM", "LARGE"}
    assert {case.aspect_ratio for case in cases} == set(MODULE.ASPECT_RATIOS)
    assert sum(case.expected_credits for case in cases) == 1780
