from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit


def load_script():
    path = Path(__file__).parents[1] / "scripts" / "audit_unused_8500_registrations.py"
    spec = importlib.util.spec_from_file_location("audit_unused_8500_registrations", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_classify_code_is_conservative() -> None:
    module = load_script()
    assert module.classify_code("PROTOCOL_SESSION_UNAUTHORIZED") == "BLOCKED"
    assert module.classify_code("UPSTREAM_UNAUTHORIZED") == "BLOCKED"
    assert module.classify_code("PROTOCOL_RATE_LIMITED") == "INDETERMINATE"
    assert module.classify_code("UPSTREAM_ACCOUNT_VALIDATION_FAILED") == "INDETERMINATE"
    assert module.classify_code("UPSTREAM_ACCOUNT_VALID", valid=True) == "NORMAL"


def test_db_only_never_promotes_or_writes() -> None:
    module = load_script()
    row = SimpleNamespace(
        id=123,
        verified_email="Account@Example.test",
        email_snapshot="fallback@example.test",
    )
    result = module.check_db_only(row)
    assert result.classification == "INDETERMINATE"
    assert result.code == "PROVIDER_STATUS_NOT_PERSISTED"
    assert result.email == "account@example.test"


def test_source_statement_is_select_only() -> None:
    module = load_script()
    statement = module.source_statement(limit=50, start_after_id=0)
    assert statement.is_select is True
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True})).upper()
    assert compiled.startswith("SELECT ")
    assert "INSERT " not in compiled
    assert "UPDATE " not in compiled
    assert "DELETE " not in compiled
    assert "ORDER BY REGISTRATION_RECORDS.CREATED_AT ASC" in compiled


def test_source_statement_can_restrict_retry_ids() -> None:
    module = load_script()
    statement = module.source_statement(
        limit=None,
        start_after_id=0,
        registration_ids={42, 7},
    )
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True})).upper()
    assert "REGISTRATION_RECORDS.ID IN (7, 42)" in compiled


def test_provider_blocked_verdict_requires_boolean_and_matching_identity() -> None:
    module = load_script()
    blocked_payload = {
        "data": {
            "users": [
                {
                    "blocked": True,
                    "user_details": [{"auth0Email": "Account@Example.test"}],
                }
            ]
        }
    }
    normal_payload = {
        "data": {
            "users": [
                {
                    "blocked": False,
                    "user_details": [{"auth0Email": "Account@Example.test"}],
                }
            ]
        }
    }
    assert module.blocked_verdict(
        blocked_payload, expected_email="account@example.test"
    ) == (True, "UPSTREAM_ACCOUNT_BLOCKED")
    assert module.blocked_verdict(
        normal_payload, expected_email="account@example.test"
    ) == (False, "UPSTREAM_ACCOUNT_NOT_BLOCKED")
    assert module.blocked_verdict(
        blocked_payload, expected_email="different@example.test"
    ) == (None, "UPSTREAM_BLOCKED_IDENTITY_MISMATCH")
    blocked_payload["data"]["users"][0]["blocked"] = None
    assert module.blocked_verdict(
        blocked_payload, expected_email="account@example.test"
    ) == (None, "UPSTREAM_BLOCKED_STATUS_UNAVAILABLE")


def test_live_query_requests_explicit_provider_blocked_field() -> None:
    module = load_script()
    assert "users(where:" in module.ACCOUNT_BLOCKED_QUERY
    assert "blocked" in module.ACCOUNT_BLOCKED_QUERY
    source = Path(module.__file__).read_text()
    assert "await query_provider_blocked(" in source


def test_loader_enforces_mysql_read_only_session() -> None:
    module = load_script()
    source = Path(module.__file__).read_text()
    assert 'exec_driver_sql("SET SESSION TRANSACTION READ ONLY")' in source


def test_render_report_has_independent_sections_without_tokens() -> None:
    module = load_script()
    stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    results = [
        module.AuditResult(
            1, "blocked@example.test", "BLOCKED", "UPSTREAM_UNAUTHORIZED", None, stamp
        ),
        module.AuditResult(
            2, "normal@example.test", "NORMAL", "UPSTREAM_ACCOUNT_VALID", 8500, stamp
        ),
        module.AuditResult(
            3,
            "unknown@example.test",
            "INDETERMINATE",
            "PROVIDER_STATUS_NOT_PERSISTED",
            None,
            stamp,
        ),
    ]
    report = module.render_report(results, mode="db", generated_at=stamp)
    assert "account_pool_write=false" in report
    assert "image_task_submission=false" in report
    assert "[BLOCKED]" in report
    assert "[NORMAL]" in report
    assert "[INDETERMINATE]" in report
    assert "Bearer" not in report
    assert "eyJ" not in report


def test_write_report_uses_private_mode(tmp_path: Path) -> None:
    module = load_script()
    path = tmp_path / "audit.txt"
    module.write_report(path, "[NORMAL]\n")
    assert path.read_text() == "[NORMAL]\n"
    assert path.stat().st_mode & 0o777 == 0o600


def test_incremental_writer_persists_each_result_before_finalize(tmp_path: Path) -> None:
    module = load_script()
    stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    path = tmp_path / "audit.txt"
    writer = module.IncrementalReportWriter(path, mode="live", source_rows=2)
    first = module.AuditResult(
        1, "first@example.test", "NORMAL", "UPSTREAM_ACCOUNT_VALID", 8500, stamp
    )
    writer.write_result(first)
    checkpoint = path.read_text()
    assert "[RESULTS]" in checkpoint
    assert "NORMAL\tfirst@example.test\t1\tUPSTREAM_ACCOUNT_VALID" in checkpoint
    assert "[BLOCKED]" not in checkpoint
    writer.finalize([first])
    assert "[NORMAL]" in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600


def test_retry_report_reads_only_indeterminate_ids(tmp_path: Path) -> None:
    module = load_script()
    finalized = tmp_path / "finalized.txt"
    finalized.write_text(
        "[BLOCKED]\nblocked@example.test\t1\tUPSTREAM_ACCOUNT_BLOCKED\t\tstamp\n"
        "[INDETERMINATE]\nunknown@example.test\t3\tPROTOCOL_RATE_LIMITED\t\tstamp\n"
    )
    assert module.registration_ids_from_retry_report(finalized) == {3}

    in_progress = tmp_path / "in-progress.txt"
    in_progress.write_text(
        "[RESULTS]\n"
        "NORMAL\tnormal@example.test\t2\tUPSTREAM_ACCOUNT_NOT_BLOCKED\t8500\tstamp\n"
        "INDETERMINATE\tunknown@example.test\t3\tPROTOCOL_RATE_LIMITED\t\tstamp\n"
    )
    assert module.registration_ids_from_retry_report(in_progress) == {3}
    assert module.decided_registration_ids_from_report(in_progress) == {2}
    assert module.decided_registration_ids_from_report(finalized) == {1}


def test_dynamic_proxy_materializes_distinct_worker_sessions() -> None:
    module = load_script()
    values = {
        "LEONARDO_PROXY_ENABLED": "true",
        "LEONARDO_PROXY_SERVER": "proxy.example.test:3010",
        "LEONARDO_PROXY_USERNAME": "fixture-region-Rand-sid-old-t-5",
        "LEONARDO_PROXY_PASSWORD": "proxy-secret",
    }
    first = urlsplit(module.dynamic_proxy_url(values, country="HK"))
    second = urlsplit(module.dynamic_proxy_url(values, country="HK"))
    assert first.username != second.username
    assert "-region-HK-" in (first.username or "")
    assert first.password == "proxy-secret"


def test_proxy_manifest_keeps_only_exit_hashes(tmp_path: Path) -> None:
    module = load_script()
    path = tmp_path / "proxies.json"
    module.write_proxy_manifest(
        path,
        endpoints=[
            module.ProxyEndpoint(
                worker=1,
                source="cliproxy-api",
                proxy_url="socks5://proxy-user:proxy-secret@proxy.example.test:1080",
                exit_ip_hash="abc123",
                country="HK",
                allocation_port=443,
            )
        ],
        requested_mode="auto",
        selected_mode="cliproxy-api",
        fallback_code="",
        api_key_fingerprint="keyhash",
    )
    raw = path.read_text()
    payload = json.loads(raw)
    assert payload["unique_exit_ips"] == 1
    assert payload["endpoints"][0]["exit_ip_sha256_prefix"] == "abc123"
    assert "proxy-secret" not in raw
    assert "proxy-user" not in raw


def test_parser_defaults_to_live_without_account_pool_options() -> None:
    module = load_script()
    args = module.build_parser().parse_args([])
    assert args.mode == "live"
    assert args.interval_seconds == 2.0
    assert args.concurrency == 20
    assert args.proxy_mode == "direct"
    assert not hasattr(args, "promote")
