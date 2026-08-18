from __future__ import annotations

import io
import json
import random
import stat
import struct
import zipfile
from datetime import UTC, datetime

import pytest

from video_task_service.cookie_import_archive import (
    CookieArchiveError,
    parse_cookie_import_archive,
)


def _cookie(
    name: str,
    value: str,
    *,
    expiration_date: float | None = 1_950_000_000,
    domain: str = "app.leonardo.ai",
) -> dict[str, object]:
    return {
        "domain": domain,
        "expirationDate": expiration_date,
        "hostOnly": True,
        "httpOnly": True,
        "name": name,
        "path": "/",
        "sameSite": "lax",
        "secure": True,
        "session": expiration_date is None,
        "storeId": "0",
        "value": value,
    }


def _chrome_export(prefix: str) -> dict[str, object]:
    return {
        "url": "https://app.leonardo.ai",
        "cookies": [
            _cookie("__Secure-better-auth.session_token", f"{prefix}-token"),
            _cookie("__Secure-better-auth.session_data.0", f"{prefix}-data"),
            _cookie("expired-cookie", f"{prefix}-expired", expiration_date=1_800_000_000),
            _cookie("session-cookie", f"{prefix}-session", expiration_date=None),
        ],
    }


def _cookie_zip(entries: list[tuple[str, dict[str, object]]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries:
            archive.writestr(name, json.dumps(payload))
    return output.getvalue()


def _raw_zip(
    entries: list[tuple[str, bytes]],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
    symlink: bool = False,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression) as archive:
        for name, payload in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = compression
            if symlink:
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, payload)
    return output.getvalue()


def _mark_first_entry_encrypted(archive: bytes) -> bytes:
    marked = bytearray(archive)
    local = marked.index(b"PK\x03\x04")
    central = marked.index(b"PK\x01\x02")
    local_flags = struct.unpack_from("<H", marked, local + 6)[0]
    central_flags = struct.unpack_from("<H", marked, central + 8)[0]
    struct.pack_into("<H", marked, local + 6, local_flags | 1)
    struct.pack_into("<H", marked, central + 8, central_flags | 1)
    return bytes(marked)


def _parse(archive: bytes, original_filename: str = "cookies.zip"):
    return parse_cookie_import_archive(
        io.BytesIO(archive),
        original_filename,
        datetime(2030, 1, 1, tzinfo=UTC),
    )


def test_parses_sample_shape_and_filters_expired_cookies() -> None:
    archive = _cookie_zip(
        [
            ("first@example.test.json", _chrome_export("first")),
            ("second@example.test.json", _chrome_export("second")),
        ]
    )

    parsed = parse_cookie_import_archive(
        io.BytesIO(archive),
        "cookies.zip",
        datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert len(parsed.items) == 2
    assert parsed.items[0].expected_login_name == "first@example.test"
    assert parsed.items[0].renewal_session is not None
    assert parsed.items[0].renewal_session.client_version == "server-cookie-zip-v1"
    assert parsed.items[0].renewal_session.capability == "better-auth-v1"
    assert [cookie.name for cookie in parsed.items[0].renewal_session.cookies] == [
        "__Secure-better-auth.session_token",
        "__Secure-better-auth.session_data.0",
        "session-cookie",
    ]
    assert all(
        cookie.expiration_date is None or cookie.expiration_date > 1_893_456_000
        for cookie in parsed.items[0].renewal_session.cookies
    )


@pytest.mark.parametrize(
    ("entry_name", "code"),
    [
        ("/absolute.json", "ARCHIVE_UNSAFE_PATH"),
        ("../traversal.json", "ARCHIVE_UNSAFE_PATH"),
        ("folder\\windows.json", "ARCHIVE_UNSAFE_PATH"),
        ("folder/sub/nested.json", "ARCHIVE_UNSAFE_PATH"),
        ("nested.zip", "ARCHIVE_NESTED_ARCHIVE"),
    ],
)
def test_rejects_unsafe_archive_entries(entry_name: str, code: str) -> None:
    archive = _raw_zip([(entry_name, b"{}")])

    with pytest.raises(CookieArchiveError) as caught:
        _parse(archive)

    assert caught.value.code == code


def test_filters_root_directories_and_non_json_manifests() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cookie-export/", b"")
        archive.writestr("emails.txt", b"first@example.test\n")
        archive.writestr("leodev_links.txt", b"https://leodev.app/?email=first%40example.test\n")
        archive.writestr(
            "cookie-export/first@example.test.json",
            json.dumps(_chrome_export("first")),
        )
        archive.writestr("__MACOSX/cookie-export/._first@example.test.json", b"AppleDouble")

    parsed = _parse(output.getvalue())

    assert len(parsed.items) == 1
    assert parsed.items[0].entry_name == "cookie-export/first@example.test.json"


def test_rejects_archive_without_supported_json_entries() -> None:
    archive = _raw_zip([("emails.txt", b"first@example.test\n"), ("export/", b"")])

    with pytest.raises(CookieArchiveError) as caught:
        _parse(archive)

    assert caught.value.code == "ARCHIVE_NO_JSON_ENTRIES"


def test_rejects_symlink_entry() -> None:
    archive = _raw_zip([("linked.json", b"target")], symlink=True)

    with pytest.raises(CookieArchiveError) as caught:
        _parse(archive)

    assert caught.value.code == "ARCHIVE_SYMLINK_ENTRY"


def test_rejects_encrypted_entry_before_reading_it() -> None:
    archive = _mark_first_entry_encrypted(_raw_zip([("encrypted.json", b"{}")]))

    with pytest.raises(CookieArchiveError) as caught:
        _parse(archive)

    assert caught.value.code == "ARCHIVE_ENCRYPTED_ENTRY"


def test_rejects_more_than_500_entries() -> None:
    archive = _raw_zip([(f"{index}.json", b"{}") for index in range(501)])

    with pytest.raises(CookieArchiveError) as caught:
        _parse(archive)

    assert caught.value.code == "ARCHIVE_TOO_MANY_ENTRIES"


def test_rejects_entry_larger_than_one_mib() -> None:
    archive = _raw_zip([("large.json", b"x" * (1024 * 1024 + 1))], compression=zipfile.ZIP_STORED)

    with pytest.raises(CookieArchiveError) as caught:
        _parse(archive)

    assert caught.value.code == "ARCHIVE_ENTRY_TOO_LARGE"


def test_rejects_total_uncompressed_size_above_50_mib() -> None:
    one_mib = random.Random(2030).randbytes(16 * 1024) * 64
    archive = _raw_zip(
        [(f"{index}.json", one_mib) for index in range(51)],
    )

    with pytest.raises(CookieArchiveError) as caught:
        _parse(archive)

    assert caught.value.code == "ARCHIVE_EXPANDED_TOO_LARGE"


def test_rejects_compression_ratio_above_100() -> None:
    archive = _raw_zip([("bomb.json", b"0" * 100_000)])

    with pytest.raises(CookieArchiveError) as caught:
        _parse(archive)

    assert caught.value.code == "ARCHIVE_COMPRESSION_RATIO"


def test_rejects_archive_larger_than_20_mib_before_opening_zip() -> None:
    with pytest.raises(CookieArchiveError) as caught:
        _parse(b"not-a-zip" + b"x" * (20 * 1024 * 1024))

    assert caught.value.code == "ARCHIVE_TOO_LARGE"


def test_rejects_non_zip_filename() -> None:
    archive = _raw_zip([("entry.json", b"{}")])

    with pytest.raises(CookieArchiveError) as caught:
        _parse(archive, "cookies.tar")

    assert caught.value.code == "ARCHIVE_EXTENSION_INVALID"


def test_keeps_valid_entries_when_one_json_is_malformed() -> None:
    archive = _raw_zip(
        [
            ("broken@example.test.json", b"{not-json"),
            ("valid@example.test.json", json.dumps(_chrome_export("valid")).encode()),
        ]
    )

    parsed = _parse(archive)

    assert len(parsed.items) == 2
    assert parsed.items[0].error_code == "ENTRY_JSON_INVALID"
    assert parsed.items[0].renewal_session is None
    assert parsed.items[1].error_code is None
    assert parsed.items[1].renewal_session is not None


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"url": "https://example.test", "cookies": []}, "ENTRY_URL_INVALID"),
        ({"url": "https://app.leonardo.ai", "cookies": "not-a-list"}, "ENTRY_COOKIES_INVALID"),
        (
            {
                "url": "https://app.leonardo.ai",
                "cookies": [
                    _cookie(
                        "__Secure-better-auth.session_token",
                        "secret-token-that-must-not-leak",
                        domain="example.test",
                    )
                ],
            },
            "COOKIE_ENTRY_INVALID",
        ),
        (
            {
                "url": "https://app.leonardo.ai",
                "cookies": [_cookie("__Secure-better-auth.session_data.0", "data")],
            },
            "COOKIE_SESSION_TOKEN_MISSING",
        ),
        (
            {
                "url": "https://app.leonardo.ai",
                "cookies": [_cookie("__Secure-better-auth.session_token", "token")],
            },
            "COOKIE_SESSION_DATA_MISSING",
        ),
    ],
)
def test_records_cookie_semantic_failures_without_secret_values(
    payload: dict[str, object],
    code: str,
) -> None:
    parsed = _parse(_cookie_zip([("entry@example.test.json", payload)]))

    assert parsed.items[0].error_code == code
    assert parsed.items[0].renewal_session is None
    assert "secret-token" not in (parsed.items[0].error_message or "")


def test_rejects_more_than_64_live_target_cookies_per_item() -> None:
    payload = _chrome_export("many")
    payload["cookies"] = [
        _cookie("__Secure-better-auth.session_token", "token"),
        _cookie("__Secure-better-auth.session_data.0", "data"),
        *[_cookie(f"extra-{index}", str(index)) for index in range(63)],
    ]

    parsed = _parse(_cookie_zip([("many@example.test.json", payload)]))

    assert parsed.items[0].error_code == "COOKIE_COUNT_EXCEEDED"
    assert parsed.items[0].renewal_session is None


def test_rejects_duplicate_cookie_name_domain_path_key() -> None:
    token = _cookie("__Secure-better-auth.session_token", "token")
    payload = {
        "url": "https://app.leonardo.ai",
        "cookies": [token, dict(token), _cookie("__Secure-better-auth.session_data.0", "data")],
    }

    parsed = _parse(_cookie_zip([("duplicate@example.test.json", payload)]))

    assert parsed.items[0].error_code == "COOKIE_DUPLICATE_KEY"


def test_normalizes_email_filename_and_preserves_session_cookie() -> None:
    parsed = _parse(
        _cookie_zip([("  First.User@Example.TEST  .json", _chrome_export("normalized"))])
    )

    item = parsed.items[0]
    assert item.expected_login_name == "first.user@example.test"
    assert item.renewal_session is not None
    session_cookie = next(
        cookie for cookie in item.renewal_session.cookies if cookie.name == "session-cookie"
    )
    assert session_cookie.expiration_date is None
