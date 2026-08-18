from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from video_task_service.crypto import encrypt_secret
from video_task_service.models import AccountLedgerProfile
from video_task_service.schemas import AccountLedgerProfileView, AccountLedgerRecord


def select_credit_records(
    envelope: Mapping[str, Any],
    *,
    credits_total: int,
) -> tuple[list[dict[str, Any]], int]:
    records = envelope.get("records")
    if not isinstance(records, list):
        raise ValueError("ledger envelope records must be a list")
    selected: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("each ledger record must be an object")
        value = record.get("creditsTotal")
        try:
            matches = value is not None and int(value) == credits_total
        except (TypeError, ValueError):
            matches = False
        if matches:
            selected.append(record)
    return selected, len(records) - len(selected)


def apply_ledger_profile(
    profile: AccountLedgerProfile,
    record: AccountLedgerRecord,
    *,
    source: str,
    source_kind: str,
    source_raw: bool,
    source_exported_at: datetime,
    source_count: int,
    source_file_sha256: str,
    account_uuid: str,
) -> None:
    document = record.source_document()
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    profile.source = source
    profile.source_kind = source_kind
    profile.source_raw = source_raw
    profile.source_exported_at = source_exported_at
    profile.source_count = source_count
    profile.source_file_sha256 = source_file_sha256
    profile.source_record_id = str(record.id)
    profile.parent_account_id = record.parent_account_id
    profile.parent_account = record.parent_account
    profile.invite_status = record.invite_status
    profile.invited_at = record.invited_at
    profile.invite_error = record.invite_error
    profile.invite_attempts = record.invite_attempts
    profile.registration_status = record.registration_status
    profile.registration_attempts = record.registration_attempts
    profile.registered_at = record.registered_at
    profile.registration_account_id = record.registration_account_id
    profile.registration_error = record.registration_error
    profile.credits_total = record.credits_total
    profile.credits_subscription = record.credits_subscription
    profile.credits_purchase = record.credits_purchase
    profile.credits_rollover = record.credits_rollover
    profile.credits_checked_at = record.credits_checked_at
    profile.credits_error = record.credits_error
    profile.credits_attempts = record.credits_attempts
    profile.source_created_at = record.source_created_at
    profile.source_updated_at = record.source_updated_at
    profile.has_registration_password = bool(
        record.registration_password.get_secret_value()
    )
    profile.has_group_token = bool(record.group_token.get_secret_value())
    profile.has_authorization_token = bool(record.authorization_token.get_secret_value())
    profile.raw_record_ciphertext = encrypt_secret(
        encoded,
        f"{account_uuid}:ledger_record",
    )
    profile.raw_record_sha256 = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    profile.credential_key_version = 1


def ledger_profile_view(profile: AccountLedgerProfile) -> AccountLedgerProfileView:
    return AccountLedgerProfileView(
        source=profile.source,
        source_kind=profile.source_kind,
        source_raw=profile.source_raw,
        source_exported_at=profile.source_exported_at,
        source_count=profile.source_count,
        source_file_sha256=profile.source_file_sha256,
        source_record_id=profile.source_record_id,
        parent_account_id=profile.parent_account_id,
        parent_account=profile.parent_account,
        invite_status=profile.invite_status,
        invited_at=profile.invited_at,
        invite_error=profile.invite_error,
        invite_attempts=profile.invite_attempts,
        registration_status=profile.registration_status,
        registration_attempts=profile.registration_attempts,
        registered_at=profile.registered_at,
        registration_account_id=profile.registration_account_id,
        registration_error=profile.registration_error,
        credits_total=profile.credits_total,
        credits_subscription=profile.credits_subscription,
        credits_purchase=profile.credits_purchase,
        credits_rollover=profile.credits_rollover,
        credits_checked_at=profile.credits_checked_at,
        credits_error=profile.credits_error,
        credits_attempts=profile.credits_attempts,
        source_created_at=profile.source_created_at,
        source_updated_at=profile.source_updated_at,
        has_registration_password=profile.has_registration_password,
        has_group_token=profile.has_group_token,
        has_authorization_token=profile.has_authorization_token,
        raw_record_sha256=profile.raw_record_sha256,
    )
