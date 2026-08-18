from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from video_task_service.account_ledger import select_credit_records
from video_task_service.schemas import AccountLedgerImportRequest


def build_import_payload(
    source_path: Path,
    *,
    space_uuid: str,
    credits_total: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = source_path.read_bytes()
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError("ledger root must be an object")
    records = envelope.get("records")
    if not isinstance(records, list):
        raise ValueError("ledger records must be a list")
    declared_count = envelope.get("count")
    if declared_count != len(records):
        raise ValueError(
            f"ledger declared count {declared_count!r} does not match {len(records)} records"
        )
    selected, rejected = select_credit_records(envelope, credits_total=credits_total)
    source_name = str(envelope.get("source") or source_path.stem)
    source_sha256 = hashlib.sha256(raw).hexdigest()
    payload = {
        "space_uuid": space_uuid,
        "source": source_name,
        "source_kind": envelope.get("kind"),
        "source_raw": envelope.get("raw"),
        "source_exported_at": envelope.get("exportedAt"),
        "source_count": envelope.get("count"),
        "source_file_sha256": source_sha256,
        "records": selected,
    }
    AccountLedgerImportRequest.model_validate(payload)
    summary = {
        "source_file": str(source_path.resolve()),
        "source_sha256": source_sha256,
        "credits_total_filter": credits_total,
        "input_records": len(records),
        "selected_records": len(selected),
        "rejected_records": rejected,
    }
    return payload, summary


def post_json(url: str, payload: dict[str, Any], admin_key: str) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Admin-Key": admin_key,
        },
    )
    try:
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ledger import failed with HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("ledger import response must be an object")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import one credit tier from a child ledger")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--space-uuid", required=True)
    parser.add_argument("--credits-total", type=int, default=8500)
    parser.add_argument("--api-base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--audit-output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload, summary = build_import_payload(
        args.file,
        space_uuid=args.space_uuid,
        credits_total=args.credits_total,
    )
    if not payload["records"]:
        raise RuntimeError("ledger filter selected zero records")
    if args.dry_run:
        result: dict[str, Any] = {"dry_run": True}
    else:
        admin_key = os.environ.get("VIDEO_SERVICE_ADMIN_AUTH_KEY", "")
        if not admin_key:
            raise RuntimeError("VIDEO_SERVICE_ADMIN_AUTH_KEY is required")
        endpoint = f"{args.api_base_url.rstrip('/')}/admin/accounts/ledger-import"
        result = post_json(endpoint, payload, admin_key)
    audit = {**summary, "api_base_url": args.api_base_url, "result": result}
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
