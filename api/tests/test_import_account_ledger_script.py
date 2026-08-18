from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "import_account_ledger.py"


def load_script() -> object:
    spec = importlib.util.spec_from_file_location("import_account_ledger", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def script_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": "10000000-0000-0000-0000-000000000001",
        "email": "one@example.test",
        "password": "password",
        "registrationPassword": "registration-password",
        "groupToken": "group-token",
        "authorizationToken": "authorization-token",
        "creditsTotal": 8500,
    }
    record.update(overrides)
    return record


def test_build_import_payload_filters_8500_and_keeps_full_records(tmp_path: Path) -> None:
    source = tmp_path / "ledger.json"
    source.write_text(
        json.dumps(
            {
                "exportedAt": "2026-08-12T08:00:00.000Z",
                "source": "fixture-ledger",
                "kind": "child",
                "raw": True,
                "count": 2,
                "records": [
                    script_record(),
                    script_record(
                        id="10000000-0000-0000-0000-000000000002",
                        email="two@example.test",
                        creditsTotal=None,
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    module = load_script()

    payload, summary = module.build_import_payload(  # type: ignore[attr-defined]
        source,
        space_uuid="7b61daf1-ae28-41bb-bb4c-1b44706a3a75",
        credits_total=8500,
    )

    assert payload == {
        "space_uuid": "7b61daf1-ae28-41bb-bb4c-1b44706a3a75",
        "source": "fixture-ledger",
        "source_kind": "child",
        "source_raw": True,
        "source_exported_at": "2026-08-12T08:00:00.000Z",
        "source_count": 2,
        "source_file_sha256": summary["source_sha256"],
        "records": [script_record()],
    }
    assert summary["input_records"] == 2
    assert summary["selected_records"] == 1
    assert summary["rejected_records"] == 1
    assert len(summary["source_sha256"]) == 64


def test_build_import_payload_rejects_incomplete_selected_record(tmp_path: Path) -> None:
    source = tmp_path / "ledger.json"
    source.write_text(
        json.dumps(
            {
                "exportedAt": "2026-08-12T08:00:00.000Z",
                "source": "fixture-ledger",
                "kind": "child",
                "raw": True,
                "count": 1,
                "records": [
                    {
                        "id": "10000000-0000-0000-0000-000000000001",
                        "email": "one@example.test",
                        "creditsTotal": 8500,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    module = load_script()

    try:
        module.build_import_payload(  # type: ignore[attr-defined]
            source,
            space_uuid="7b61daf1-ae28-41bb-bb4c-1b44706a3a75",
            credits_total=8500,
        )
    except ValueError as exc:
        assert "password" in str(exc)
    else:
        raise AssertionError("incomplete selected ledger record was accepted")


def test_build_import_payload_rejects_declared_count_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "ledger.json"
    source.write_text(
        json.dumps(
            {
                "exportedAt": "2026-08-12T08:00:00.000Z",
                "source": "fixture-ledger",
                "kind": "child",
                "raw": True,
                "count": 2,
                "records": [script_record()],
            }
        ),
        encoding="utf-8",
    )
    module = load_script()

    try:
        module.build_import_payload(  # type: ignore[attr-defined]
            source,
            space_uuid="7b61daf1-ae28-41bb-bb4c-1b44706a3a75",
            credits_total=8500,
        )
    except ValueError as exc:
        assert "declared count" in str(exc)
    else:
        raise AssertionError("ledger count mismatch was accepted")
