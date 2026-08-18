#!/usr/bin/env python3
"""Audit Seedance 2.5 feature visibility across the FRAME OPS account pool.

The script reads an account snapshot from the existing admin API, exports
stored access tokens in bounded batches, evaluates the same LaunchDarkly flags
as the web application, and resolves each referenced public model release.
Passwords and access tokens stay in memory and are never written to reports.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audit_seedance25_accounts as core  # noqa: E402


def split_export_line_allow_empty_token(line: str) -> tuple[str, str]:
    login_name, separator, remainder = line.partition("|")
    if not separator or not login_name.strip():
        raise core.AuditError("credential_export_invalid_line")
    _, separator, token = remainder.rpartition("|")
    if not separator:
        raise core.AuditError("credential_export_invalid_line")
    return login_name.strip().lower(), token.strip()


def load_account_snapshot(
    client: core.HttpClient,
    *,
    base_url: str,
    admin_headers: dict[str, str],
    status: str,
) -> list[dict[str, Any]]:
    url = f"{base_url}/admin/accounts"
    if status != "ALL":
        url += f"?status={urllib.parse.quote(status)}"
    _, _, value = client.request_json("GET", url, headers=admin_headers)
    if not isinstance(value, list):
        raise core.AuditError("account_list_invalid")
    return [
        account
        for account in value
        if isinstance(account, dict)
        and (status == "ALL" or str(account.get("status") or "") == status)
    ]


def export_tokens(
    client: core.HttpClient,
    accounts: list[dict[str, Any]],
    *,
    base_url: str,
    admin_headers: dict[str, str],
    batch_size: int,
) -> tuple[dict[str, str], dict[str, str]]:
    tokens: dict[str, str] = {}
    errors: dict[str, str] = {}

    def export_batch(batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        account_uuids = [str(account.get("account_uuid") or "") for account in batch]
        try:
            _, _, raw = client.request_bytes(
                "POST",
                f"{base_url}/admin/accounts/export",
                headers=admin_headers,
                body={"account_uuids": account_uuids},
            )
        except core.AuditError as exc:
            if len(batch) > 1:
                middle = len(batch) // 2
                export_batch(batch[:middle])
                export_batch(batch[middle:])
                return
            errors[account_uuids[0]] = f"credential_export_failed:{exc}"
            return

        lines = raw.decode("utf-8").splitlines()
        if len(lines) != len(batch):
            if len(batch) > 1:
                middle = len(batch) // 2
                export_batch(batch[:middle])
                export_batch(batch[middle:])
                return
            errors[account_uuids[0]] = "credential_export_count_mismatch"
            return

        for index, account in enumerate(batch):
            line = lines[index]
            account_uuid = str(account.get("account_uuid") or "")
            try:
                login_name, token = split_export_line_allow_empty_token(line)
                expected = str(account.get("login_name") or "").strip().lower()
                if login_name != expected:
                    raise core.AuditError("credential_export_identity_mismatch")
                if token:
                    tokens[account_uuid] = token
                else:
                    errors[account_uuid] = "stored_token_missing"
            except core.AuditError as exc:
                errors[account_uuid] = str(exc)

    for index in range(0, len(accounts), batch_size):
        export_batch(accounts[index : index + batch_size])
    return tokens, errors


def inspect_account(
    account: dict[str, Any],
    token: str,
    token_error: str | None,
    client: core.HttpClient,
    *,
    launchdarkly_client_id: str,
    plan: str,
) -> dict[str, Any]:
    result = {
        "account_uuid": str(account.get("account_uuid") or ""),
        "login_name": str(account.get("login_name") or ""),
        "status": str(account.get("status") or ""),
        "disabled_reason": account.get("disabled_reason"),
        "credential_source": str(account.get("credential_source") or ""),
        "balance_credits": account.get("balance_credits"),
        "token_configured": bool(account.get("token_configured")),
        "token_expires_at": account.get("token_expires_at"),
        "token_expired": None,
        "plan": plan,
        "feature_flag": None,
        "model_release": None,
        "catalog_has_seedance25": None,
        "has_seedance25": None,
        "usable_now": False,
        "reason": "unknown",
        "error": None,
    }
    try:
        if token_error:
            raise core.AuditError(token_error)
        payload = core.decode_jwt_payload(token)
        try:
            expires_at = int(payload.get("exp") or 0)
        except (TypeError, ValueError) as exc:
            raise core.AuditError("token_expiry_invalid") from exc
        result["token_expired"] = expires_at <= int(time.time())
        context = core.build_launchdarkly_context_from_token(
            payload,
            registration_uuid=result["account_uuid"],
            plan=plan,
        )
        flags = core.launchdarkly_flags(
            client,
            context,
            client_id=launchdarkly_client_id,
        )
        result["feature_flag"] = core.launchdarkly_flag_value(
            flags, "isSeedance25Enabled"
        )
        result["model_release"] = core.launchdarkly_flag_value(
            flags, "modelMatrixActiveRelease"
        )
        if not isinstance(result["feature_flag"], bool):
            raise core.AuditError("seedance25_feature_flag_missing")
        if not isinstance(result["model_release"], str) or not result["model_release"]:
            raise core.AuditError("model_release_flag_missing")
        if result["feature_flag"] is False:
            result["has_seedance25"] = False
            result["reason"] = "feature_flag_disabled"
    except core.AuditError as exc:
        result["error"] = str(exc)
        result["reason"] = "inspection_error"
    except Exception as exc:  # pragma: no cover - defensive redaction boundary
        result["error"] = f"unexpected_error:{type(exc).__name__}"
        result["reason"] = "inspection_error"
    return result


def apply_catalog_verdicts(
    results: list[dict[str, Any]], catalogs: dict[str, dict[str, Any]]
) -> None:
    core.apply_catalog_verdicts(results, catalogs)
    for result in results:
        result["usable_now"] = bool(
            result.get("has_seedance25") is True
            and result.get("status") == "ACTIVE"
            and result.get("token_expired") is False
        )


CSV_FIELDS = [
    "account_uuid",
    "login_name",
    "status",
    "disabled_reason",
    "credential_source",
    "balance_credits",
    "token_configured",
    "token_expires_at",
    "token_expired",
    "plan",
    "feature_flag",
    "model_release",
    "catalog_has_seedance25",
    "has_seedance25",
    "usable_now",
    "reason",
    "error",
]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    path.chmod(0o600)


def count_by(
    rows: list[dict[str, Any]], field: str, expected: Any | None = None
) -> dict[str, int]:
    counter = Counter(
        str(row.get(field) or "")
        for row in rows
        if expected is None or row.get(field) is expected
    )
    return dict(sorted(counter.items()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit Seedance 2.5 visibility across every account-pool status."
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env.local"))
    parser.add_argument("--base-url", default="")
    parser.add_argument("--admin-key", default="")
    parser.add_argument(
        "--status",
        default="ALL",
        help="Account status to scan; default ALL scans the complete pool.",
    )
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--export-batch-size", type=int, default=100)
    parser.add_argument("--plan", default="BASIC")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--release", default="", help="Optional catalog release fallback")
    parser.add_argument(
        "--launchdarkly-client-id", default=core.LAUNCHDARKLY_CLIENT_ID
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-on-unknown", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    env_file = core.load_env_file(args.env_file)
    base_url = (
        args.base_url
        or os.environ.get("FRAME_OPS_API_URL")
        or env_file.get("FRAME_OPS_API_URL")
        or ""
    ).rstrip("/")
    admin_key = (
        args.admin_key
        or os.environ.get("VIDEO_SERVICE_ADMIN_AUTH_KEY")
        or env_file.get("VIDEO_SERVICE_ADMIN_AUTH_KEY")
        or ""
    )
    if not base_url:
        parser.error("FRAME_OPS_API_URL/--base-url is required")
    if not admin_key:
        parser.error("VIDEO_SERVICE_ADMIN_AUTH_KEY/--admin-key is required")
    if not 1 <= args.concurrency <= 64:
        parser.error("--concurrency must be between 1 and 64")
    if not 1 <= args.export_batch_size <= 1000:
        parser.error("--export-batch-size must be between 1 and 1000")
    if args.offset < 0 or args.limit < 0:
        parser.error("--offset/--limit must be zero or positive")

    status = str(args.status or "ALL").strip().upper()
    client = core.HttpClient(timeout=args.timeout, retries=args.retries)
    admin_headers = {"X-Admin-Key": admin_key, "Cache-Control": "no-store"}
    snapshot = load_account_snapshot(
        client,
        base_url=base_url,
        admin_headers=admin_headers,
        status=status,
    )
    source_count = len(snapshot)
    accounts = snapshot[args.offset :]
    if args.limit:
        accounts = accounts[: args.limit]
    inventory = {
        "stage": "inventory",
        "status": status,
        "source_account_count": source_count,
        "selected_account_count": len(accounts),
        "status_counts": count_by(accounts, "status"),
        "token_configured": sum(bool(row.get("token_configured")) for row in accounts),
        "dry_run": args.dry_run,
    }
    print(json.dumps(inventory, ensure_ascii=False))
    if args.dry_run:
        return 0

    tokens, token_errors = export_tokens(
        client,
        accounts,
        base_url=base_url,
        admin_headers=admin_headers,
        batch_size=args.export_batch_size,
    )
    results: list[dict[str, Any]] = []
    enabled_so_far = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        pending = {
            executor.submit(
                inspect_account,
                account,
                tokens.get(str(account.get("account_uuid") or ""), ""),
                token_errors.get(str(account.get("account_uuid") or "")),
                client,
                launchdarkly_client_id=args.launchdarkly_client_id,
                plan=args.plan,
            ): account
            for account in accounts
        }
        for index, future in enumerate(as_completed(pending), start=1):
            result = future.result()
            results.append(result)
            if result.get("feature_flag") is True:
                enabled_so_far += 1
            if index == len(accounts) or index % 50 == 0:
                print(
                    f"checked={index}/{len(accounts)} flag_enabled={enabled_so_far}",
                    file=sys.stderr,
                )
    results.sort(key=lambda row: (row["status"], row["login_name"], row["account_uuid"]))

    releases = {
        str(result["model_release"])
        for result in results
        if isinstance(result.get("model_release"), str) and result["model_release"]
    }
    if args.release:
        releases.add(args.release)
    catalogs: dict[str, dict[str, Any]] = {}
    catalog_errors: dict[str, str] = {}
    for release in sorted(releases):
        try:
            catalogs[release] = core.fetch_catalog(client, release)
        except core.AuditError as exc:
            catalog_errors[release] = str(exc)
    apply_catalog_verdicts(results, catalogs)

    summary = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_status": status,
        "source_account_count": source_count,
        "account_count": len(results),
        "status_counts": count_by(results, "status"),
        "seedance25_enabled": sum(row.get("has_seedance25") is True for row in results),
        "seedance25_disabled": sum(row.get("has_seedance25") is False for row in results),
        "unknown": sum(row.get("has_seedance25") is None for row in results),
        "usable_now": sum(row.get("usable_now") is True for row in results),
        "enabled_by_status": count_by(
            [row for row in results if row.get("has_seedance25") is True], "status"
        ),
        "unknown_by_status": count_by(
            [row for row in results if row.get("has_seedance25") is None], "status"
        ),
        "unknown_reasons": dict(
            sorted(
                Counter(
                    str(row.get("error") or row.get("reason") or "unknown")
                    for row in results
                    if row.get("has_seedance25") is None
                ).items()
            )
        ),
        "assumed_plan": args.plan,
        "release_ids": sorted(catalogs),
        "catalog_errors": catalog_errors,
    }
    output_dir = args.output_dir or Path(
        "work", f"seedance25-account-pool-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    report_path = output_dir / "seedance25-account-pool.json"
    csv_path = output_dir / "seedance25-account-pool.csv"
    enabled_path = output_dir / "seedance25-enabled.csv"
    unknown_path = output_dir / "seedance25-unknown.csv"
    catalogs_path = output_dir / "model-catalogs.json"
    core.write_private_text(
        report_path,
        json.dumps({"summary": summary, "accounts": results}, ensure_ascii=False, indent=2)
        + "\n",
    )
    write_csv(csv_path, results)
    write_csv(enabled_path, [row for row in results if row.get("has_seedance25") is True])
    write_csv(unknown_path, [row for row in results if row.get("has_seedance25") is None])
    core.write_private_text(
        catalogs_path,
        json.dumps({"catalogs": catalogs, "errors": catalog_errors}, ensure_ascii=False, indent=2)
        + "\n",
    )
    print(
        json.dumps(
            {
                "summary": summary,
                "artifacts": {
                    "json": str(report_path),
                    "csv": str(csv_path),
                    "enabled_csv": str(enabled_path),
                    "unknown_csv": str(unknown_path),
                    "model_catalogs": str(catalogs_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if args.fail_on_unknown and summary["unknown"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except core.AuditError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
