from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_smoke_module() -> ModuleType:
    script = Path(__file__).parents[1] / "scripts" / "smoke_gpt_image_2.py"
    spec = importlib.util.spec_from_file_location("smoke_gpt_image_2", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_contract_matrix_covers_all_180_mode_parameter_combinations() -> None:
    smoke = load_smoke_module()
    cases = smoke.all_contract_cases()
    assert len(cases) == 180
    assert all(smoke.validate_contract_case(case)["passed"] for case in cases)
    assert {case.mode for case in cases} == {"text-to-image", "image-to-image"}
    assert {case.quality for case in cases} == {"LOW", "MEDIUM", "HIGH"}
    assert {case.size for case in cases} == {"SMALL", "MEDIUM", "LARGE"}
    assert {case.aspect_ratio for case in cases} == {
        "21:9", "16:9", "3:2", "4:3", "5:4",
        "1:1", "4:5", "3:4", "2:3", "9:16",
    }


def test_live_matrix_covers_quality_size_and_all_ratios() -> None:
    smoke = load_smoke_module()
    assert smoke.coverage_summary(smoke.LIVE_CASES) == {
        "text_qualities": ["HIGH", "LOW", "MEDIUM"],
        "text_sizes": ["LARGE", "MEDIUM", "SMALL"],
        "image_qualities": ["HIGH", "LOW", "MEDIUM"],
        "image_sizes": ["LARGE", "MEDIUM", "SMALL"],
        "aspect_ratios": [
            "16:9", "1:1", "21:9", "2:3", "3:2",
            "3:4", "4:3", "4:5", "5:4", "9:16",
        ],
    }
    assert sum(case.expected_credits for case in smoke.LIVE_CASES) == 1012


def test_fixed_fields_and_conflicting_resolution_are_rejected() -> None:
    smoke = load_smoke_module()
    results = smoke.validate_fixed_field_contracts()
    assert [result["field"] for result in results] == [
        "prompt_enhance", "style", "style_ids", "quantity", "resolution"
    ]
    assert all(result["passed"] for result in results)
