from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.account_ledger import apply_ledger_profile, ledger_profile_view
from video_task_service.auth import require_admin_key
from video_task_service.config import get_settings
from video_task_service.crypto import decrypt_secret, encrypt_secret, master_key
from video_task_service.db import session_dependency
from video_task_service.models import (
    Account,
    AccountCreditLedger,
    AccountLedgerProfile,
    AccountLoginJob,
    Space,
    Task,
    TaskAttempt,
    TaskMediaAsset,
)
from video_task_service.protocol_renewal import (
    RenewalSessionStorageError,
    delete_stored_renewal_session,
    reset_stored_renewal_state,
    store_renewal_session,
)
from video_task_service.schemas import (
    AccountBalanceRefreshResult,
    AccountBulkDeleteItem,
    AccountBulkDeletePreview,
    AccountBulkDeleteRequest,
    AccountBulkDeleteResult,
    AccountBulkSelection,
    AccountCreate,
    AccountEmailAuditItemView,
    AccountEmailAuditRequest,
    AccountEmailAuditResponse,
    AccountLedgerImportItem,
    AccountLedgerImportRequest,
    AccountLedgerImportResult,
    AccountLedgerProfileView,
    AccountPatch,
    AccountSync,
    AccountSyncResult,
    AccountTokenUpdate,
    AccountView,
    SpaceCreate,
    SpaceView,
)
from video_task_service.upstream import LeonardoUpstream

router = APIRouter(
    prefix="/accounts",
    tags=["accounts"],
    dependencies=[Depends(require_admin_key)],
)
spaces_router = APIRouter(
    prefix="/spaces",
    tags=["spaces"],
    dependencies=[Depends(require_admin_key)],
)

ACCOUNT_EXPORT_RECEIPT_TTL_SECONDS = 10 * 60


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def account_selection_hash(account_uuids: list[UUID]) -> str:
    normalized = "\n".join(sorted(str(account_uuid) for account_uuid in account_uuids))
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def account_export_receipt(
    account_uuids: list[UUID],
    *,
    now: datetime | None = None,
) -> str:
    issued_at = int((now or datetime.now(UTC)).timestamp())
    payload = {
        "count": len(account_uuids),
        "expires_at": issued_at + ACCOUNT_EXPORT_RECEIPT_TTL_SECONDS,
        "selection_sha256": account_selection_hash(account_uuids),
        "version": 1,
    }
    encoded_payload = _urlsafe_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_key = hashlib.sha256(master_key() + b"\0account-export-receipt-v1").digest()
    signature = hmac.new(signing_key, encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded_payload}.{_urlsafe_encode(signature)}"


def verify_account_export_receipt(
    receipt: str,
    account_uuids: list[UUID],
    *,
    now: datetime | None = None,
) -> None:
    try:
        encoded_payload, encoded_signature = receipt.split(".", 1)
        signing_key = hashlib.sha256(master_key() + b"\0account-export-receipt-v1").digest()
        expected_signature = hmac.new(
            signing_key,
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_urlsafe_decode(encoded_signature), expected_signature):
            raise ValueError("signature mismatch")
        payload = json.loads(_urlsafe_decode(encoded_payload))
        current_time = int((now or datetime.now(UTC)).timestamp())
        valid = (
            payload.get("version") == 1
            and payload.get("count") == len(account_uuids)
            and payload.get("selection_sha256") == account_selection_hash(account_uuids)
            and isinstance(payload.get("expires_at"), int)
            and payload["expires_at"] >= current_time
        )
        if not valid:
            raise ValueError("receipt does not match selection")
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ACCOUNT_EXPORT_RECEIPT_INVALID",
                "message": "export receipt is invalid, expired, or belongs to another selection",
            },
        ) from exc


async def classify_account_deletions(
    session: AsyncSession,
    account_uuids: list[UUID],
    *,
    lock: bool,
) -> tuple[list[Account], AccountBulkDeletePreview]:
    requested_values = [str(account_uuid) for account_uuid in account_uuids]
    statement = (
        select(Account).where(Account.account_uuid.in_(requested_values)).order_by(Account.id)
    )
    if lock:
        statement = statement.with_for_update()
    accounts = list(await session.scalars(statement))
    accounts_by_uuid = {account.account_uuid: account for account in accounts}
    account_ids = [account.id for account in accounts]
    history_ids: set[int] = set()

    if account_ids:
        history_columns = (
            Task.account_id,
            TaskAttempt.account_id,
            TaskMediaAsset.account_id,
            AccountCreditLedger.account_id,
            AccountLoginJob.account_id,
            AccountLoginJob.active_account_id,
        )
        for column in history_columns:
            values = await session.scalars(
                select(column).where(column.in_(account_ids)).distinct()
            )
            history_ids.update(int(value) for value in values if value is not None)

    items: list[AccountBulkDeleteItem] = []
    for account_uuid in account_uuids:
        account = accounts_by_uuid.get(str(account_uuid))
        if account is None:
            items.append(
                AccountBulkDeleteItem(
                    account_uuid=account_uuid,
                    outcome="MISSING",
                    code="ACCOUNT_NOT_FOUND",
                    message="account was not found",
                )
            )
        elif account.active_tasks:
            items.append(
                AccountBulkDeleteItem(
                    account_uuid=account_uuid,
                    login_name=account.login_name,
                    outcome="PROTECTED",
                    code="ACCOUNT_HAS_ACTIVE_TASKS",
                    message="account has active tasks and cannot be deleted",
                )
            )
        elif account.reserved_credits:
            items.append(
                AccountBulkDeleteItem(
                    account_uuid=account_uuid,
                    login_name=account.login_name,
                    outcome="PROTECTED",
                    code="ACCOUNT_HAS_RESERVED_CREDITS",
                    message="account has reserved credits and cannot be deleted",
                )
            )
        elif account.id in history_ids:
            items.append(
                AccountBulkDeleteItem(
                    account_uuid=account_uuid,
                    login_name=account.login_name,
                    outcome="PROTECTED",
                    code="ACCOUNT_HAS_HISTORY",
                    message="account has task, media, credit, or login history",
                )
            )
        else:
            items.append(
                AccountBulkDeleteItem(
                    account_uuid=account_uuid,
                    login_name=account.login_name,
                    outcome="DELETABLE",
                )
            )

    return accounts, AccountBulkDeletePreview(
        requested=len(account_uuids),
        deletable=sum(item.outcome == "DELETABLE" for item in items),
        protected=sum(item.outcome == "PROTECTED" for item in items),
        missing=sum(item.outcome == "MISSING" for item in items),
        items=items,
    )


def fixed_account_concurrency(
    requested: int | None,
    configured: int,
    *,
    reject_mismatch: bool,
) -> int:
    if reject_mismatch and requested is not None and requested != configured:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ACCOUNT_CONCURRENCY_FIXED",
                "message": f"account max_concurrency is fixed at {configured}",
            },
        )
    return configured


def account_view(account: Account, space: Space) -> AccountView:
    return AccountView(
        account_uuid=UUID(account.account_uuid),
        space_uuid=UUID(space.space_uuid),
        login_name=account.login_name,
        # Rolling-deployment alias for older consoles. It intentionally carries
        # the complete login name so both client generations display it fully.
        login_name_masked=account.login_name,
        credential_source=getattr(account, "credential_source", "PASSWORD"),
        label=account.label,
        status=account.status,
        disabled_reason=account.disabled_reason,
        token_configured=account.video_token_ciphertext is not None,
        token_expires_at=account.token_expires_at,
        token_refreshed_at=account.token_refreshed_at,
        balance_credits=account.balance_credits,
        reserved_credits=account.reserved_credits,
        balance_synced_at=account.balance_synced_at,
        max_concurrency=account.max_concurrency,
        active_tasks=account.active_tasks,
        completed_tasks=account.completed_tasks,
        failed_tasks=account.failed_tasks,
        version=account.version,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def account_status_for_balance(
    current_status: str,
    token_expires_at: datetime | None,
    balance_credits: int,
    *,
    now: datetime,
    low_balance_threshold: int,
    token_guard_seconds: int,
) -> tuple[str, str | None]:
    if current_status == "MANUAL_DISABLED":
        return "MANUAL_DISABLED", "manual"
    if token_expires_at is None or token_expires_at <= now:
        return "TOKEN_EXPIRED", "token_expired"
    if balance_credits < low_balance_threshold:
        return "LOW_BALANCE_DISABLED", "balance_below_threshold"
    if token_expires_at <= now + timedelta(seconds=token_guard_seconds):
        return "TOKEN_EXPIRING", "token_inside_guard_window"
    return "ACTIVE", None


@router.post("/sync", response_model=AccountSyncResult)
async def sync_account(
    body: AccountSync,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> AccountSyncResult:
    """Create an unknown login or refresh the token of an existing login.

    Login name is the idempotent identity.  Existing credentials and scheduler
    placement are deliberately preserved; only the session token, optional
    balance snapshot, validation state, and optimistic version are refreshed.
    """

    no_store(response)
    settings = get_settings()
    account_max_concurrency = fixed_account_concurrency(
        body.max_concurrency,
        settings.account_max_concurrency,
        reject_mismatch=False,
    )
    login_name = body.login_name.strip().lower()
    space_name = body.space_name.strip()
    routing_key = body.routing_key.strip() if body.routing_key else None
    now = datetime.now(UTC).replace(tzinfo=None)
    action: Literal["CREATED", "TOKEN_UPDATED"] = "TOKEN_UPDATED"

    try:
        async with session.begin():
            account = await session.scalar(
                select(Account).where(Account.login_name == login_name).with_for_update()
            )
            if account is not None:
                space = await session.get(Space, account.space_id)
                assert space is not None
                account.video_token_ciphertext = encrypt_secret(
                    body.video_token.get_secret_value(),
                    f"{account.account_uuid}:video_token",
                )
                account.token_expires_at = body.token_expires_at
                account.token_refreshed_at = now
                if body.balance_credits is not None:
                    account.balance_credits = body.balance_credits
                    account.balance_synced_at = now
                account.max_concurrency = account_max_concurrency
                account.version += 1
                if account.status != "MANUAL_DISABLED":
                    account.status = "PENDING_VALIDATION"
                    account.disabled_reason = None
                account.last_error_code = None
                account.last_error_at = None
            else:
                if body.password is None:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "PASSWORD_REQUIRED_FOR_NEW_ACCOUNT",
                            "message": "password is required when creating an account",
                        },
                    )
                space = await session.scalar(
                    select(Space).where(Space.name == space_name).with_for_update()
                )
                if space is None:
                    space = Space(
                        space_uuid=str(uuid4()),
                        name=space_name,
                        routing_key=routing_key,
                        max_concurrency=max(10, account_max_concurrency),
                    )
                    session.add(space)
                    await session.flush()
                account_uuid = str(uuid4())
                account = Account(
                    account_uuid=account_uuid,
                    space_id=space.id,
                    login_name=login_name,
                    credential_source="PASSWORD",
                    password_ciphertext=encrypt_secret(
                        body.password.get_secret_value(),
                        f"{account_uuid}:password",
                    ),
                    video_token_ciphertext=encrypt_secret(
                        body.video_token.get_secret_value(),
                        f"{account_uuid}:video_token",
                    ),
                    token_expires_at=body.token_expires_at,
                    token_refreshed_at=now,
                    balance_credits=body.balance_credits or 0,
                    balance_synced_at=now if body.balance_credits is not None else None,
                    max_concurrency=account_max_concurrency,
                    status="PENDING_VALIDATION",
                )
                session.add(account)
                await session.flush()
                action = "CREATED"
            if body.renewal_session is not None:
                await store_renewal_session(session, account, body.renewal_session)
            else:
                await delete_stored_renewal_session(session, account.id)
    except RenewalSessionStorageError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RENEWAL_SESSION_TOO_LARGE",
                "message": "renewal session exceeds the encrypted storage limit",
            },
        ) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "ACCOUNT_SYNC_CONFLICT", "message": "account sync conflicted"},
        ) from exc

    return AccountSyncResult(action=action, account=account_view(account, space))


@spaces_router.post("", response_model=SpaceView, status_code=status.HTTP_201_CREATED)
async def create_space(
    body: SpaceCreate,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> Space:
    space = Space(
        space_uuid=str(uuid4()),
        name=body.name.strip(),
        routing_key=body.routing_key.strip() if body.routing_key else None,
        max_concurrency=body.max_concurrency,
    )
    try:
        async with session.begin():
            session.add(space)
            await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "SPACE_ALREADY_EXISTS", "message": "space name already exists"},
        ) from exc
    return space


@spaces_router.get("", response_model=list[SpaceView])
async def list_spaces(
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> list[Space]:
    result = await session.scalars(select(Space).order_by(Space.created_at.asc()))
    return list(result)


@router.post("", response_model=AccountView, status_code=status.HTTP_201_CREATED)
async def add_account(
    body: AccountCreate,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> AccountView:
    no_store(response)
    settings = get_settings()
    account_max_concurrency = fixed_account_concurrency(
        body.max_concurrency,
        settings.account_max_concurrency,
        reject_mismatch=False,
    )
    login_name = body.login_name.strip().lower()
    account_uuid = str(uuid4())
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        async with session.begin():
            space = await session.scalar(
                select(Space).where(Space.space_uuid == str(body.space_uuid))
            )
            if space is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "SPACE_NOT_FOUND", "message": "space was not found"},
                )
            existing = await session.scalar(
                select(Account.id).where(Account.login_name == login_name)
            )
            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "ACCOUNT_ALREADY_EXISTS",
                        "message": "login name already exists",
                    },
                )
            account = Account(
                account_uuid=account_uuid,
                space_id=space.id,
                login_name=login_name,
                credential_source="PASSWORD",
                label=body.label,
                password_ciphertext=encrypt_secret(
                    body.password.get_secret_value(), f"{account_uuid}:password"
                ),
                video_token_ciphertext=(
                    encrypt_secret(
                        body.video_token.get_secret_value(),
                        f"{account_uuid}:video_token",
                    )
                    if body.video_token is not None
                    else None
                ),
                token_expires_at=body.token_expires_at,
                token_refreshed_at=now if body.video_token is not None else None,
                balance_credits=body.balance_credits,
                max_concurrency=account_max_concurrency,
                status="PENDING_VALIDATION",
            )
            session.add(account)
            await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "ACCOUNT_ALREADY_EXISTS", "message": "login name already exists"},
        ) from exc
    return account_view(account, space)


@router.get("", response_model=list[AccountView])
async def list_accounts(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    account_status: Annotated[str | None, Query(alias="status")] = None,
) -> list[AccountView]:
    statement = select(Account, Space).join(Space, Space.id == Account.space_id)
    if account_status:
        statement = statement.where(Account.status == account_status.upper())
    rows = (await session.execute(statement.order_by(Account.created_at.asc()))).all()
    return [account_view(account, space) for account, space in rows]


# BEGIN EMAIL AUDIT API
@router.post("/blocked-check", response_model=AccountEmailAuditResponse)
async def audit_accounts_by_email(
    body: AccountEmailAuditRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> AccountEmailAuditResponse:
    """Compare a batch of emails with the online account pool.

    This is a read-only database snapshot.  It deliberately does not open a
    browser, submit a generation, or mutate account state.  The persisted
    ``MANUAL_DISABLED/manual`` pair is reported as a database block marker;
    Leonardo's upstream ``users.blocked`` flag is not stored in this schema and
    therefore remains ``null`` for all other rows.
    """

    no_store(response)
    accounts = list(
        await session.scalars(
            select(Account).where(Account.login_name.in_(body.emails))
        )
    )
    accounts_by_email = {account.login_name.strip().lower(): account for account in accounts}
    account_ids = [account.id for account in accounts]
    task_rows = []
    if account_ids:
        task_rows = list(
            (
                await session.execute(
                    select(Task.account_id, Task.status, Task.model).where(
                        Task.account_id.in_(account_ids),
                        Task.task_type == "IMAGE_GENERATION",
                    )
                )
            ).all()
        )

    tasks_by_account: dict[int, list[tuple[str, str]]] = {}
    for account_id, task_status, task_model in task_rows:
        tasks_by_account.setdefault(int(account_id), []).append(
            (str(task_status), str(task_model))
        )

    items: list[AccountEmailAuditItemView] = []
    for email in body.emails:
        account = accounts_by_email.get(email)
        rows = tasks_by_account.get(account.id, []) if account is not None else []
        image_success = sum(
            status_value in {"COMPLETED", "SUCCEEDED"} for status_value, _ in rows
        )
        image_failed = sum(
            status_value in {"FAILED", "SUBMIT_UNKNOWN"} for status_value, _ in rows
        )
        is_db_blocked = (
            account is not None
            and account.status == "MANUAL_DISABLED"
            and account.disabled_reason == "manual"
        )
        items.append(
            AccountEmailAuditItemView(
                email=email,
                in_account_pool=account is not None,
                account_uuid=UUID(account.account_uuid) if account is not None else None,
                account_status=account.status if account is not None else None,
                disabled_reason=account.disabled_reason if account is not None else None,
                balance_credits=account.balance_credits if account is not None else None,
                completed_tasks=(int(account.completed_tasks) if account is not None else None),
                failed_tasks=(int(account.failed_tasks) if account is not None else None),
                blocked=True if is_db_blocked else None,
                blocked_source="DB_MANUAL_STATUS" if is_db_blocked else "NOT_RECORDED",
                image_task_total=len(rows),
                image_task_success=image_success,
                image_task_failed=image_failed,
                image_models=sorted({model for _, model in rows}),
            )
        )

    return AccountEmailAuditResponse(
        checked_at=datetime.now(UTC),
        requested_count=len(items),
        matched_count=sum(item.in_account_pool for item in items),
        missing_count=sum(not item.in_account_pool for item in items),
        blocked_count=sum(item.blocked is True for item in items),
        image_success_account_count=sum(item.image_task_success > 0 for item in items),
        image_success_task_count=sum(item.image_task_success for item in items),
        items=items,
    )


# END EMAIL AUDIT API


@router.post("/bulk-delete/preview", response_model=AccountBulkDeletePreview)
async def preview_bulk_delete_accounts(
    body: AccountBulkSelection,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> AccountBulkDeletePreview:
    no_store(response)
    _, preview = await classify_account_deletions(
        session,
        body.account_uuids,
        lock=False,
    )
    return preview


@router.post("/export", response_class=Response)
async def export_account_credentials(
    body: AccountBulkSelection,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> Response:
    requested_values = [str(account_uuid) for account_uuid in body.account_uuids]
    accounts = list(
        await session.scalars(
            select(Account).where(Account.account_uuid.in_(requested_values))
        )
    )
    accounts_by_uuid = {account.account_uuid: account for account in accounts}
    missing = [
        str(account_uuid)
        for account_uuid in body.account_uuids
        if str(account_uuid) not in accounts_by_uuid
    ]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ACCOUNT_SELECTION_STALE",
                "message": "one or more selected accounts no longer exist",
                "account_uuids": missing,
            },
        )

    lines: list[str] = []
    for account_uuid in body.account_uuids:
        account = accounts_by_uuid[str(account_uuid)]
        try:
            password = (
                ""
                if getattr(account, "credential_source", "PASSWORD") == "COOKIE_SESSION"
                else decrypt_secret(
                    bytes(account.password_ciphertext),
                    f"{account.account_uuid}:password",
                )
            )
            token = (
                decrypt_secret(
                    bytes(account.video_token_ciphertext),
                    f"{account.account_uuid}:video_token",
                )
                if account.video_token_ciphertext is not None
                else ""
            )
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ACCOUNT_CREDENTIAL_DECRYPT_FAILED",
                    "message": "an account credential could not be decrypted",
                    "account_uuid": account.account_uuid,
                },
            ) from exc
        if any("\n" in value or "\r" in value for value in (account.login_name, password, token)):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ACCOUNT_CREDENTIAL_EXPORT_UNREPRESENTABLE",
                    "message": "an account credential contains a line break",
                    "account_uuid": account.account_uuid,
                },
            )
        lines.append(f"{account.login_name}|{password}|{token}")

    now = datetime.now(UTC)
    filename = f"accounts-credentials-{now.strftime('%Y%m%d-%H%M%S')}.txt"
    receipt = account_export_receipt(body.account_uuids, now=now)
    return Response(
        content="\n".join(lines).encode("utf-8"),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Account-Export-Receipt": receipt,
            "X-Exported-Count": str(len(lines)),
            "Access-Control-Expose-Headers": (
                "Content-Disposition, X-Account-Export-Receipt, X-Exported-Count"
            ),
        },
    )


@router.post("/bulk-delete", response_model=AccountBulkDeleteResult)
async def bulk_delete_accounts(
    body: AccountBulkDeleteRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> AccountBulkDeleteResult:
    no_store(response)
    verify_account_export_receipt(body.export_receipt, body.account_uuids)

    try:
        async with session.begin():
            accounts, preview = await classify_account_deletions(
                session,
                body.account_uuids,
                lock=True,
            )
            deletable_uuids = {
                str(item.account_uuid)
                for item in preview.items
                if item.outcome == "DELETABLE"
            }
            for account in accounts:
                if account.account_uuid in deletable_uuids:
                    await session.delete(account)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ACCOUNT_DELETE_CONFLICT",
                "message": "account references changed while the batch was being deleted",
            },
        ) from exc

    items = [
        item.model_copy(
            update={"outcome": "DELETED" if item.outcome == "DELETABLE" else "SKIPPED"}
        )
        for item in preview.items
    ]
    deleted = sum(item.outcome == "DELETED" for item in items)
    return AccountBulkDeleteResult(
        requested=len(body.account_uuids),
        deleted=deleted,
        skipped=len(items) - deleted,
        items=items,
    )


@router.post("/ledger-import", response_model=AccountLedgerImportResult)
async def import_account_ledger(
    body: AccountLedgerImportRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> AccountLedgerImportResult:
    """Idempotently import complete child-ledger rows by normalized login name."""

    no_store(response)
    settings = get_settings()
    account_max_concurrency = fixed_account_concurrency(
        None,
        settings.account_max_concurrency,
        reject_mismatch=False,
    )
    items: list[AccountLedgerImportItem] = []
    try:
        async with session.begin():
            space = await session.scalar(
                select(Space).where(Space.space_uuid == str(body.space_uuid))
            )
            if space is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "SPACE_NOT_FOUND", "message": "space was not found"},
                )

            for record in body.records:
                login_name = record.email.strip().lower()
                account = await session.scalar(
                    select(Account)
                    .where(Account.login_name == login_name)
                    .with_for_update()
                )
                if account is None:
                    account_uuid = str(uuid4())
                    account = Account(
                        account_uuid=account_uuid,
                        space_id=space.id,
                        login_name=login_name,
                        credential_source="PASSWORD",
                        password_ciphertext=encrypt_secret(
                            record.password.get_secret_value(),
                            f"{account_uuid}:password",
                        ),
                        balance_credits=record.credits_total or 0,
                        balance_synced_at=record.credits_checked_at,
                        max_concurrency=account_max_concurrency,
                        status="PENDING_VALIDATION",
                    )
                    session.add(account)
                    await session.flush()
                    action: Literal["CREATED", "UPDATED"] = "CREATED"
                else:
                    account.password_ciphertext = encrypt_secret(
                        record.password.get_secret_value(),
                        f"{account.account_uuid}:password",
                    )
                    account.credential_source = "PASSWORD"
                    if record.credits_total is not None and (
                        account.balance_synced_at is None
                        or record.credits_checked_at is None
                        or record.credits_checked_at >= account.balance_synced_at
                    ):
                        account.balance_credits = record.credits_total
                        account.balance_synced_at = record.credits_checked_at
                    account.version += 1
                    action = "UPDATED"

                profile = await session.get(AccountLedgerProfile, account.id)
                if profile is None:
                    profile = AccountLedgerProfile(account_id=account.id)
                    session.add(profile)
                apply_ledger_profile(
                    profile,
                    record,
                    source=body.source,
                    source_kind=body.source_kind,
                    source_raw=body.source_raw,
                    source_exported_at=body.source_exported_at,
                    source_count=body.source_count,
                    source_file_sha256=body.source_file_sha256,
                    account_uuid=account.account_uuid,
                )
                items.append(
                    AccountLedgerImportItem(
                        action=action,
                        account_uuid=account.account_uuid,
                        login_name=account.login_name,
                        source_record_id=record.id,
                        credits_total=record.credits_total,
                    )
                )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ACCOUNT_LEDGER_CONFLICT",
                "message": "ledger email or source record belongs to another account",
            },
        ) from exc

    return AccountLedgerImportResult(
        requested=len(items),
        created=sum(item.action == "CREATED" for item in items),
        updated=sum(item.action == "UPDATED" for item in items),
        items=items,
    )


@router.get("/{account_uuid}/ledger", response_model=AccountLedgerProfileView)
async def get_account_ledger_profile(
    account_uuid: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> AccountLedgerProfileView:
    no_store(response)
    profile = await session.scalar(
        select(AccountLedgerProfile)
        .join(Account, Account.id == AccountLedgerProfile.account_id)
        .where(Account.account_uuid == str(account_uuid))
    )
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "ACCOUNT_LEDGER_PROFILE_NOT_FOUND",
                "message": "account ledger profile was not found",
            },
        )
    return ledger_profile_view(profile)


@router.get("/{account_uuid}", response_model=AccountView)
async def get_account(
    account_uuid: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> AccountView:
    no_store(response)
    row = (
        await session.execute(
            select(Account, Space)
            .join(Space, Space.id == Account.space_id)
            .where(Account.account_uuid == str(account_uuid))
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ACCOUNT_NOT_FOUND", "message": "account was not found"},
        )
    return account_view(row[0], row[1])


@router.put("/{account_uuid}/token", response_model=AccountView)
async def update_account_token(
    account_uuid: UUID,
    body: AccountTokenUpdate,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> AccountView:
    no_store(response)
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session.begin():
        account = await session.scalar(
            select(Account).where(Account.account_uuid == str(account_uuid)).with_for_update()
        )
        if account is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "ACCOUNT_NOT_FOUND", "message": "account was not found"},
            )
        if account.version != body.expected_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ACCOUNT_VERSION_CONFLICT",
                    "message": "account was updated by another request",
                    "current_version": account.version,
                },
            )
        space = await session.get(Space, account.space_id)
        assert space is not None
        account.video_token_ciphertext = encrypt_secret(
            body.video_token.get_secret_value(), f"{account.account_uuid}:video_token"
        )
        account.token_expires_at = body.token_expires_at
        account.token_refreshed_at = now
        account.version += 1
        if account.status != "MANUAL_DISABLED":
            account.status = "PENDING_VALIDATION"
            account.disabled_reason = None
        account.last_error_code = None
        account.last_error_at = None
        await reset_stored_renewal_state(session, account.id)
    return account_view(account, space)


@router.post("/{account_uuid}/refresh-balance", response_model=AccountBalanceRefreshResult)
async def refresh_account_balance(
    account_uuid: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> AccountBalanceRefreshResult:
    """Fetch the current upstream token balance and update scheduler eligibility."""

    no_store(response)
    row = (
        await session.execute(
            select(Account, Space)
            .join(Space, Space.id == Account.space_id)
            .where(Account.account_uuid == str(account_uuid))
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ACCOUNT_NOT_FOUND", "message": "account was not found"},
        )
    snapshot, _ = row
    if snapshot.video_token_ciphertext is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "ACCOUNT_TOKEN_MISSING", "message": "account has no video token"},
        )
    snapshot_version = snapshot.version
    encrypted_token = bytes(snapshot.video_token_ciphertext)
    account_aad = f"{snapshot.account_uuid}:video_token"
    await session.rollback()

    try:
        token = decrypt_secret(encrypted_token, account_aad)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "ACCOUNT_TOKEN_DECRYPT_FAILED",
                "message": "stored account token could not be decrypted",
            },
        ) from exc

    upstream = LeonardoUpstream(get_settings())
    try:
        validation = await upstream.validate_account(token=token)
    finally:
        await upstream.close()

    now = datetime.now(UTC).replace(tzinfo=None)
    settings = get_settings()
    async with session.begin():
        account = await session.scalar(
            select(Account).where(Account.account_uuid == str(account_uuid)).with_for_update()
        )
        if account is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "ACCOUNT_NOT_FOUND", "message": "account was not found"},
            )
        if account.version != snapshot_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ACCOUNT_VERSION_CONFLICT",
                    "message": "account was updated while its balance was being fetched",
                    "current_version": account.version,
                },
            )
        space = await session.get(Space, account.space_id)
        assert space is not None
        previous_balance = account.balance_credits
        error_code = validation.error_code
        valid = validation.valid and validation.balance_credits is not None
        if valid:
            assert validation.balance_credits is not None
            account.balance_credits = validation.balance_credits
            account.status, account.disabled_reason = account_status_for_balance(
                account.status,
                account.token_expires_at,
                account.balance_credits,
                now=now,
                low_balance_threshold=settings.low_balance_threshold,
                token_guard_seconds=settings.token_guard_seconds,
            )
            account.last_error_code = None
            account.last_error_at = None
        else:
            error_code = error_code or "UPSTREAM_BALANCE_MISSING"
            account.last_error_code = error_code
            account.last_error_at = now
            if error_code in {"UPSTREAM_UNAUTHORIZED", "UPSTREAM_NO_USER_DETAILS"}:
                account.status = "TOKEN_EXPIRED"
                account.disabled_reason = error_code.lower()
        account.balance_synced_at = now
        account.version += 1
    return AccountBalanceRefreshResult(
        valid=valid,
        account=account_view(account, space),
        previous_balance_credits=previous_balance,
        balance_credits=account.balance_credits,
        credit_delta=account.balance_credits - previous_balance,
        refreshed_at=now,
        error_code=error_code,
    )


@router.patch("/{account_uuid}", response_model=AccountView)
async def patch_account(
    account_uuid: UUID,
    body: AccountPatch,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> AccountView:
    no_store(response)
    account_max_concurrency = fixed_account_concurrency(
        body.max_concurrency,
        get_settings().account_max_concurrency,
        reject_mismatch=True,
    )
    async with session.begin():
        account = await session.scalar(
            select(Account).where(Account.account_uuid == str(account_uuid)).with_for_update()
        )
        if account is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "ACCOUNT_NOT_FOUND", "message": "account was not found"},
            )
        if body.expected_version is not None and account.version != body.expected_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ACCOUNT_VERSION_CONFLICT",
                    "message": "account was updated by another request",
                    "current_version": account.version,
                },
            )
        if body.space_uuid is not None:
            space = await session.scalar(
                select(Space).where(Space.space_uuid == str(body.space_uuid))
            )
            if space is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "SPACE_NOT_FOUND", "message": "space was not found"},
                )
            if account.space_id != space.id and account.active_tasks:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "ACCOUNT_HAS_ACTIVE_TASKS",
                        "message": "account cannot move spaces while tasks are active",
                    },
                )
            account.space_id = space.id
        if body.password is not None:
            account.password_ciphertext = encrypt_secret(
                body.password.get_secret_value(), f"{account.account_uuid}:password"
            )
            account.credential_source = "PASSWORD"
        account.max_concurrency = account_max_concurrency
        if body.manual_status == "MANUAL_DISABLED":
            account.status = "MANUAL_DISABLED"
            account.disabled_reason = "manual"
        elif body.manual_status == "ACTIVE":
            account.status = "PENDING_VALIDATION"
            account.disabled_reason = None
        account.version += 1
        space = await session.get(Space, account.space_id)
        assert space is not None
    return account_view(account, space)


@router.delete(
    "/{account_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_account(
    account_uuid: UUID,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> Response:
    """Delete an unused account while preserving all historical task records."""

    async with session.begin():
        accounts, preview = await classify_account_deletions(
            session,
            [account_uuid],
            lock=True,
        )
        item = preview.items[0]
        if item.outcome == "MISSING":
            raise HTTPException(
                status_code=404,
                detail={"code": "ACCOUNT_NOT_FOUND", "message": "account was not found"},
            )
        if item.outcome == "PROTECTED":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": item.code,
                    "message": item.message,
                },
            )
        await session.delete(accounts[0])
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})
