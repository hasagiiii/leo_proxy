from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_IMPORT_LINES = 5_000
FIELD_LIMITS = (255, 4_096, 8_192)

ParentAccountImportIssueCode = Literal[
    "FORMAT",
    "INVALID_EMAIL",
    "INVALID_URL",
    "FIELD_TOO_LONG",
    "DUPLICATE_IN_BATCH",
    "DUPLICATE_EXISTING",
    "TOO_MANY_ROWS",
]


@dataclass(frozen=True)
class ParentAccountImportRecord:
    line_number: int
    email: str
    password: str
    invite_url: str


@dataclass(frozen=True)
class ParentAccountImportIssue:
    line_number: int
    email: str
    code: ParentAccountImportIssueCode
    reason: str


@dataclass(frozen=True)
class ParentAccountImportParseResult:
    records: list[ParentAccountImportRecord]
    issues: list[ParentAccountImportIssue]
    blank_lines: int


def _valid_invite_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def parse_parent_account_import(
    text: str,
    existing_emails: Iterable[str] = (),
) -> ParentAccountImportParseResult:
    existing = {email.strip().lower() for email in existing_emails if email.strip()}
    seen: set[str] = set()
    records: list[ParentAccountImportRecord] = []
    issues: list[ParentAccountImportIssue] = []
    blank_lines = 0
    nonblank_lines = 0

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.replace("\ufeff", "").strip()
        if not line:
            blank_lines += 1
            continue
        nonblank_lines += 1
        if nonblank_lines > MAX_IMPORT_LINES:
            issues.append(
                ParentAccountImportIssue(
                    line_number=line_number,
                    email="",
                    code="TOO_MANY_ROWS",
                    reason=f"单次最多导入 {MAX_IMPORT_LINES} 行",
                )
            )
            continue

        fields = line.split()
        email = fields[0].lower() if fields else ""
        if len(fields) != 3:
            issues.append(
                ParentAccountImportIssue(
                    line_number=line_number,
                    email=email,
                    code="FORMAT",
                    reason="每行必须包含邮箱、密码和邀请链接三个字段",
                )
            )
            continue
        if any(len(value) > limit for value, limit in zip(fields, FIELD_LIMITS, strict=True)):
            issues.append(
                ParentAccountImportIssue(
                    line_number=line_number,
                    email=email,
                    code="FIELD_TOO_LONG",
                    reason="字段长度超过限制",
                )
            )
            continue
        if EMAIL_PATTERN.fullmatch(email) is None:
            issues.append(
                ParentAccountImportIssue(
                    line_number=line_number,
                    email=email,
                    code="INVALID_EMAIL",
                    reason="邮箱格式无效",
                )
            )
            continue
        if not _valid_invite_url(fields[2]):
            issues.append(
                ParentAccountImportIssue(
                    line_number=line_number,
                    email=email,
                    code="INVALID_URL",
                    reason="邀请链接必须是完整的 HTTP 或 HTTPS URL",
                )
            )
            continue
        if email in seen:
            issues.append(
                ParentAccountImportIssue(
                    line_number=line_number,
                    email=email,
                    code="DUPLICATE_IN_BATCH",
                    reason="同一批次内邮箱重复",
                )
            )
            continue
        seen.add(email)
        if email in existing:
            issues.append(
                ParentAccountImportIssue(
                    line_number=line_number,
                    email=email,
                    code="DUPLICATE_EXISTING",
                    reason="母号池中已存在",
                )
            )
            continue
        records.append(
            ParentAccountImportRecord(
                line_number=line_number,
                email=email,
                password=fields[1],
                invite_url=fields[2],
            )
        )

    return ParentAccountImportParseResult(
        records=records,
        issues=issues,
        blank_lines=blank_lines,
    )
