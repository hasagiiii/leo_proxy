from __future__ import annotations

import html
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
KEYWORD_PATTERN = re.compile(
    r"(?:验证码|校验码|动态码|一次性密码|verification\s+code|security\s+code|"
    r"one[-\s]?time(?:\s+password)?|passcode|otp|code)",
    re.IGNORECASE,
)
ALPHANUMERIC_CODE_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{4,8})(?![A-Za-z0-9])")
NUMERIC_CODE_PATTERN = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")
EMPHASIS_PATTERN = re.compile(
    r"<(?:strong|b|code)\b[^>]*>\s*([A-Za-z0-9]{4,8})\s*</(?:strong|b|code)>",
    re.IGNORECASE,
)
TAG_PATTERN = re.compile(r"<[^>]+>")
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)

MAX_IMPORT_LINES = 5_000
FIELD_LIMITS = (255, 4_096, 255, 16_384)


@dataclass(frozen=True)
class MailboxImportRecord:
    line_number: int
    email: str
    password: str
    client_id: str
    refresh_token: str


@dataclass(frozen=True)
class MailboxImportIssue:
    line_number: int
    email: str
    code: Literal[
        "FORMAT",
        "EMPTY_FIELD",
        "INVALID_EMAIL",
        "FIELD_TOO_LONG",
        "DUPLICATE_IN_BATCH",
        "DUPLICATE_EXISTING",
        "TOO_MANY_ROWS",
    ]
    reason: str


@dataclass(frozen=True)
class MailboxImportParseResult:
    records: list[MailboxImportRecord]
    issues: list[MailboxImportIssue]
    blank_lines: int


@dataclass(frozen=True)
class VerificationCodeMatch:
    code: str
    matched_by: Literal["KEYWORD_NEARBY", "HTML_EMPHASIS", "NUMERIC_FALLBACK"]


def parse_mailbox_import(
    text: str,
    existing_emails: Iterable[str] = (),
) -> MailboxImportParseResult:
    existing = {email.strip().lower() for email in existing_emails if email.strip()}
    seen: set[str] = set()
    records: list[MailboxImportRecord] = []
    issues: list[MailboxImportIssue] = []
    blank_lines = 0
    nonempty_lines = 0

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.replace("\ufeff", "").strip()
        if not line:
            blank_lines += 1
            continue
        nonempty_lines += 1
        if nonempty_lines > MAX_IMPORT_LINES:
            issues.append(
                MailboxImportIssue(
                    line_number=line_number,
                    email="",
                    code="TOO_MANY_ROWS",
                    reason=f"单次最多导入 {MAX_IMPORT_LINES} 行",
                )
            )
            continue

        fields = [value.strip() for value in line.split("----")]
        email = fields[0].lower() if fields else ""
        if len(fields) != 4:
            issues.append(
                MailboxImportIssue(
                    line_number=line_number,
                    email=email,
                    code="FORMAT",
                    reason="每行必须包含邮箱、密码、client_id 和 refresh_token 四个字段",
                )
            )
            continue
        if any(not value for value in fields):
            issues.append(
                MailboxImportIssue(
                    line_number=line_number,
                    email=email,
                    code="EMPTY_FIELD",
                    reason="四个字段均不能为空",
                )
            )
            continue
        if any(len(value) > limit for value, limit in zip(fields, FIELD_LIMITS, strict=True)):
            issues.append(
                MailboxImportIssue(
                    line_number=line_number,
                    email=email,
                    code="FIELD_TOO_LONG",
                    reason="字段长度超过限制",
                )
            )
            continue
        if EMAIL_PATTERN.fullmatch(email) is None:
            issues.append(
                MailboxImportIssue(
                    line_number=line_number,
                    email=email,
                    code="INVALID_EMAIL",
                    reason="邮箱格式无效",
                )
            )
            continue
        if email in seen:
            issues.append(
                MailboxImportIssue(
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
                MailboxImportIssue(
                    line_number=line_number,
                    email=email,
                    code="DUPLICATE_EXISTING",
                    reason="邮箱池中已存在",
                )
            )
            continue
        records.append(
            MailboxImportRecord(
                line_number=line_number,
                email=email,
                password=fields[1],
                client_id=fields[2],
                refresh_token=fields[3],
            )
        )

    return MailboxImportParseResult(records=records, issues=issues, blank_lines=blank_lines)


def _valid_candidate(candidate: str) -> bool:
    if not any(character.isdigit() for character in candidate):
        return False
    if candidate.isdigit() and len(candidate) == 4 and 1900 <= int(candidate) <= 2099:
        return False
    return True


def _plain_text(value: str) -> str:
    return html.unescape(TAG_PATTERN.sub(" ", value)).replace("\xa0", " ")


def extract_verification_code(
    subject: str,
    body_preview: str,
    body_content: str,
) -> VerificationCodeMatch | None:
    plain = "\n".join((_plain_text(subject), _plain_text(body_preview), _plain_text(body_content)))
    for keyword in KEYWORD_PATTERN.finditer(plain):
        window = plain[keyword.end() : keyword.end() + 100]
        for candidate_match in ALPHANUMERIC_CODE_PATTERN.finditer(window):
            candidate = candidate_match.group(1).upper()
            if _valid_candidate(candidate):
                return VerificationCodeMatch(candidate, "KEYWORD_NEARBY")

    for emphasis in EMPHASIS_PATTERN.finditer(body_content):
        candidate = emphasis.group(1).upper()
        if _valid_candidate(candidate):
            return VerificationCodeMatch(candidate, "HTML_EMPHASIS")

    fallback_text = URL_PATTERN.sub(" ", plain)
    for candidate_match in NUMERIC_CODE_PATTERN.finditer(fallback_text):
        candidate = candidate_match.group(1)
        if _valid_candidate(candidate):
            return VerificationCodeMatch(candidate, "NUMERIC_FALLBACK")
    return None
