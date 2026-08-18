from __future__ import annotations

import importlib.util
from pathlib import Path


def load_script():
    script = Path(__file__).parents[1] / "scripts" / "audit_seedance25_account_pool.py"
    spec = importlib.util.spec_from_file_location("audit_seedance25_account_pool", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_line_allows_missing_token_without_exposing_password() -> None:
    module = load_script()

    assert module.split_export_line_allow_empty_token(
        "user@example.test|password-with|pipe|"
    ) == ("user@example.test", "")
    assert module.split_export_line_allow_empty_token(
        "user@example.test|password|jwt.value.signature"
    ) == ("user@example.test", "jwt.value.signature")


def test_catalog_verdict_distinguishes_visibility_from_current_usability() -> None:
    module = load_script()
    rows = [
        {
            "status": "ACTIVE",
            "token_expired": False,
            "feature_flag": True,
            "model_release": "release",
            "has_seedance25": None,
        },
        {
            "status": "ACTIVE",
            "token_expired": True,
            "feature_flag": True,
            "model_release": "release",
            "has_seedance25": None,
        },
        {
            "status": "LOW_BALANCE_DISABLED",
            "token_expired": False,
            "feature_flag": True,
            "model_release": "release",
            "has_seedance25": None,
        },
        {
            "status": "ACTIVE",
            "token_expired": False,
            "feature_flag": False,
            "model_release": "release",
            "has_seedance25": None,
        },
    ]
    catalogs = {
        "release": {
            "seedance25": {"slug": "bytedance/seedance-2.5", "generate": True}
        }
    }

    module.apply_catalog_verdicts(rows, catalogs)

    assert [row["has_seedance25"] for row in rows] == [True, True, True, False]
    assert [row["usable_now"] for row in rows] == [True, False, False, False]


def test_count_by_returns_sorted_status_counts() -> None:
    module = load_script()

    assert module.count_by(
        [{"status": "TOKEN_EXPIRED"}, {"status": "ACTIVE"}, {"status": "ACTIVE"}],
        "status",
    ) == {"ACTIVE": 2, "TOKEN_EXPIRED": 1}
