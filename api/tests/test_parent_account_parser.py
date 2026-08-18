from __future__ import annotations

from video_task_service.parent_accounts import (
    MAX_IMPORT_LINES,
    parse_parent_account_import,
)


def test_parse_parent_account_import_normalizes_three_fields() -> None:
    result = parse_parent_account_import(
        "\ufeff USER@Example.COM Secret-1 https://example.test/invite/token\n\n"
        "second@example.com\tSecret-2\thttp://example.test/join"
    )

    assert [
        (item.line_number, item.email, item.password, item.invite_url)
        for item in result.records
    ] == [
        (1, "user@example.com", "Secret-1", "https://example.test/invite/token"),
        (3, "second@example.com", "Secret-2", "http://example.test/join"),
    ]
    assert result.blank_lines == 1
    assert result.issues == []


def test_parse_parent_account_import_classifies_bad_and_duplicate_rows() -> None:
    result = parse_parent_account_import(
        "broken@example.com only-two\n"
        "bad-email Secret https://example.test/join\n"
        "first@example.com Secret ftp://example.test/join\n"
        "valid@example.com Secret https://example.test/join\n"
        "VALID@example.com Other https://example.test/other\n"
        "existing@example.com Secret https://example.test/existing",
        existing_emails=["Existing@Example.com"],
    )

    assert [issue.code for issue in result.issues] == [
        "FORMAT",
        "INVALID_EMAIL",
        "INVALID_URL",
        "DUPLICATE_IN_BATCH",
        "DUPLICATE_EXISTING",
    ]
    assert [record.email for record in result.records] == ["valid@example.com"]


def test_parse_parent_account_import_rejects_fields_over_their_limits() -> None:
    result = parse_parent_account_import(
        f"user@example.com {'x' * 4_097} https://example.test/join"
    )

    assert result.records == []
    assert [issue.code for issue in result.issues] == ["FIELD_TOO_LONG"]


def test_parse_parent_account_import_caps_nonblank_rows() -> None:
    content = "\n".join(
        f"user{index}@example.com Secret https://example.test/{index}"
        for index in range(MAX_IMPORT_LINES + 1)
    )

    result = parse_parent_account_import(content)

    assert len(result.records) == MAX_IMPORT_LINES
    assert [issue.code for issue in result.issues] == ["TOO_MANY_ROWS"]
