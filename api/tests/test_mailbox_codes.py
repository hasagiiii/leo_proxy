from video_task_service.mailbox_codes import (
    extract_verification_code,
    parse_mailbox_import,
)


def test_import_parser_normalizes_valid_rows_and_ignores_blank_lines() -> None:
    result = parse_mailbox_import(
        "\ufeff USER@Example.COM ---- password ---- client-id ---- refresh-token\n\n"
        "second@example.com----p2----c2----r2"
    )

    assert result.blank_lines == 1
    assert [(item.line_number, item.email) for item in result.records] == [
        (1, "user@example.com"),
        (3, "second@example.com"),
    ]
    assert result.records[0].password == "password"
    assert result.records[0].client_id == "client-id"
    assert result.records[0].refresh_token == "refresh-token"
    assert result.issues == []


def test_import_parser_reports_shape_and_duplicate_issues_without_secrets() -> None:
    result = parse_mailbox_import(
        "broken@example.com----password----client-only\n"
        "first@example.com----secret-one----client-one----refresh-one\n"
        "FIRST@example.com----secret-two----client-two----refresh-two\n"
        "existing@example.com----secret-three----client-three----refresh-three",
        existing_emails=["Existing@Example.com"],
    )

    assert [record.email for record in result.records] == ["first@example.com"]
    assert [(issue.line_number, issue.email, issue.code) for issue in result.issues] == [
        (1, "broken@example.com", "FORMAT"),
        (3, "first@example.com", "DUPLICATE_IN_BATCH"),
        (4, "existing@example.com", "DUPLICATE_EXISTING"),
    ]
    assert all("secret" not in issue.reason for issue in result.issues)
    assert all(not hasattr(issue, "source") for issue in result.issues)


def test_import_parser_rejects_invalid_email_and_blank_fields() -> None:
    result = parse_mailbox_import(
        "not-an-email----password----client----refresh\n"
        "blank@example.com--------client----refresh"
    )

    assert result.records == []
    assert [issue.code for issue in result.issues] == ["INVALID_EMAIL", "EMPTY_FIELD"]


def test_extracts_code_near_chinese_or_english_keyword() -> None:
    chinese = extract_verification_code("登录验证", "您的验证码：483921", "")
    english = extract_verification_code(
        "Sign in",
        "Your verification code is AB12CD. It expires soon.",
        "",
    )

    assert chinese is not None
    assert (chinese.code, chinese.matched_by) == ("483921", "KEYWORD_NEARBY")
    assert english is not None
    assert (english.code, english.matched_by) == ("AB12CD", "KEYWORD_NEARBY")


def test_extracts_html_emphasis_and_numeric_fallback() -> None:
    emphasized = extract_verification_code("", "", "<p>Use <strong>Q7M4P2</strong></p>")
    fallback = extract_verification_code("", "temporary number 725194", "")

    assert emphasized is not None
    assert (emphasized.code, emphasized.matched_by) == ("Q7M4P2", "HTML_EMPHASIS")
    assert fallback is not None
    assert (fallback.code, fallback.matched_by) == ("725194", "NUMERIC_FALLBACK")


def test_extractor_filters_year_time_url_and_long_numbers() -> None:
    result = extract_verification_code(
        "Account notice 2026",
        "Sent at 12:30. Visit https://example.test/483921 or call 13800138000.",
        "",
    )

    assert result is None
