from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import BinaryIO
from urllib.parse import urlparse

from pydantic import ValidationError

from video_task_service.schemas import RenewalCookie, RenewalSessionPayload

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_ENTRY_BYTES = 1 * 1024 * 1024
MAX_ENTRIES = 500
MAX_COMPRESSION_RATIO = 100


class CookieArchiveError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CookieImportItemError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ParsedCookieImportItem:
    entry_name: str
    entry_sha256: str
    expected_login_name: str | None
    renewal_session: RenewalSessionPayload | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class ParsedCookieImportArchive:
    archive_sha256: str
    items: tuple[ParsedCookieImportItem, ...]


def _read_archive_bytes(stream: BinaryIO) -> bytes:
    archive_bytes = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise CookieArchiveError("ARCHIVE_TOO_LARGE", "ZIP exceeds the 20 MiB limit")
    return archive_bytes


def _validate_archive_entries(infos: list[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
    if not infos:
        raise CookieArchiveError("ARCHIVE_EMPTY", "ZIP contains no entries")
    if len(infos) > MAX_ENTRIES:
        raise CookieArchiveError("ARCHIVE_TOO_MANY_ENTRIES", "ZIP contains more than 500 entries")

    total_uncompressed = 0
    supported_infos: list[zipfile.ZipInfo] = []
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        path_parts = name.rstrip("/").split("/")
        if (
            "\\" in name
            or path.is_absolute()
            or not path_parts
            or any(part in {"", ".", ".."} for part in path_parts)
        ):
            raise CookieArchiveError("ARCHIVE_UNSAFE_PATH", "ZIP entry path is unsafe")
        mode = info.external_attr >> 16
        if stat.S_IFMT(mode) == stat.S_IFLNK:
            raise CookieArchiveError("ARCHIVE_SYMLINK_ENTRY", "ZIP symbolic links are rejected")
        if info.flag_bits & 1:
            raise CookieArchiveError(
                "ARCHIVE_ENCRYPTED_ENTRY",
                "Encrypted ZIP entries are rejected",
            )
        if info.file_size and (
            info.compress_size == 0
            or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
        ):
            raise CookieArchiveError(
                "ARCHIVE_COMPRESSION_RATIO",
                "ZIP entry compression ratio exceeds 100:1",
            )
        suffix = path.suffix.lower()
        if suffix == ".zip":
            raise CookieArchiveError("ARCHIVE_NESTED_ARCHIVE", "Nested archives are rejected")
        # Browser exports often include harmless manifests (for example
        # `emails.txt` and `leodev_links.txt`) or a root directory marker.
        # They are not import items, so ignore them after validating their
        # metadata instead of failing the complete upload.  Keep the safety
        # checks above for every entry so an ignored entry cannot bypass the
        # archive boundary or path/symlink/encryption checks.
        if info.is_dir() or suffix != ".json":
            total_uncompressed += info.file_size
            continue
        # Finder adds AppleDouble resource forks under __MACOSX/ with names
        # such as `._account.json`.  They are metadata, not Cookie exports,
        # even though their suffix happens to be `.json`.
        if path_parts[0] == "__MACOSX" or path.name.startswith("._"):
            total_uncompressed += info.file_size
            continue
        if len(path_parts) > 2:
            raise CookieArchiveError("ARCHIVE_UNSAFE_PATH", "ZIP entry path is unsafe")
        if info.file_size > MAX_ENTRY_BYTES:
            raise CookieArchiveError("ARCHIVE_ENTRY_TOO_LARGE", "JSON entry exceeds 1 MiB")
        total_uncompressed += info.file_size
        supported_infos.append(info)

    if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
        raise CookieArchiveError(
            "ARCHIVE_EXPANDED_TOO_LARGE",
            "ZIP expanded content exceeds 50 MiB",
        )
    if not supported_infos:
        raise CookieArchiveError(
            "ARCHIVE_NO_JSON_ENTRIES",
            "ZIP contains no supported JSON cookie entries",
        )
    return supported_infos


def _expected_login_name(entry_name: str) -> str | None:
    stem = PurePosixPath(entry_name).stem.strip().lower()
    return stem if EMAIL_PATTERN.fullmatch(stem) else None


def _normalized_cookie(raw_cookie: object, now_timestamp: float) -> RenewalCookie | None:
    if not isinstance(raw_cookie, dict):
        raise CookieImportItemError("COOKIE_ENTRY_INVALID", "Cookie entry must be an object")
    try:
        expiration_value = raw_cookie.get("expirationDate")
        expiration_date = float(expiration_value) if expiration_value is not None else None
    except (TypeError, ValueError) as exc:
        raise CookieImportItemError(
            "COOKIE_ENTRY_INVALID",
            "Cookie expirationDate must be numeric or null",
        ) from exc
    if expiration_date is not None and expiration_date <= now_timestamp:
        return None
    try:
        return RenewalCookie.model_validate(
            {
                "name": raw_cookie["name"],
                "value": raw_cookie["value"],
                "domain": raw_cookie["domain"],
                "path": raw_cookie.get("path") or "/",
                "expiration_date": expiration_date,
                "secure": bool(raw_cookie.get("secure", True)),
                "http_only": bool(raw_cookie.get("httpOnly", False)),
                "same_site": str(raw_cookie.get("sameSite") or "unspecified").lower(),
            }
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise CookieImportItemError(
            "COOKIE_ENTRY_INVALID",
            "Cookie entry has invalid fields",
        ) from exc


def _renewal_session_from_payload(payload: object, now: datetime) -> RenewalSessionPayload:
    if not isinstance(payload, dict):
        raise CookieImportItemError("ENTRY_JSON_INVALID", "JSON root must be an object")
    target_url = payload.get("url")
    parsed_url = urlparse(target_url) if isinstance(target_url, str) else None
    hostname = (parsed_url.hostname or "").lower() if parsed_url else ""
    if (
        parsed_url is None
        or parsed_url.scheme != "https"
        or (hostname != "leonardo.ai" and not hostname.endswith(".leonardo.ai"))
    ):
        raise CookieImportItemError(
            "ENTRY_URL_INVALID",
            "Entry URL must be an HTTPS leonardo.ai URL",
        )
    raw_cookies = payload.get("cookies")
    if not isinstance(raw_cookies, list):
        raise CookieImportItemError("ENTRY_COOKIES_INVALID", "Entry cookies must be a list")

    cookies = [
        cookie
        for raw_cookie in raw_cookies
        if (cookie := _normalized_cookie(raw_cookie, now.timestamp())) is not None
    ]
    if len(cookies) > 64:
        raise CookieImportItemError(
            "COOKIE_COUNT_EXCEEDED",
            "Entry contains more than 64 live target Cookies",
        )

    seen: set[tuple[str, str, str]] = set()
    names: set[str] = set()
    for cookie in cookies:
        key = (cookie.name, cookie.domain.lower(), cookie.path)
        if key in seen:
            raise CookieImportItemError(
                "COOKIE_DUPLICATE_KEY",
                "Entry contains a duplicate Cookie name/domain/path key",
            )
        seen.add(key)
        names.add(cookie.name.lower())
    if "__secure-better-auth.session_token" not in names:
        raise CookieImportItemError(
            "COOKIE_SESSION_TOKEN_MISSING",
            "Entry has no live Better Auth session token Cookie",
        )
    if not any(name.startswith("__secure-better-auth.session_data") for name in names):
        raise CookieImportItemError(
            "COOKIE_SESSION_DATA_MISSING",
            "Entry has no live Better Auth session data Cookie",
        )
    return RenewalSessionPayload(
        cookies=cookies,
        client_version="server-cookie-zip-v1",
        capability="better-auth-v1",
    )


def _parse_item(entry_name: str, entry_bytes: bytes, now: datetime) -> ParsedCookieImportItem:
    entry_sha256 = hashlib.sha256(entry_bytes).hexdigest()
    expected_login_name = _expected_login_name(entry_name)
    try:
        payload = json.loads(entry_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        error = CookieImportItemError("ENTRY_JSON_INVALID", "Entry is not valid UTF-8 JSON")
        return ParsedCookieImportItem(
            entry_name=entry_name,
            entry_sha256=entry_sha256,
            expected_login_name=expected_login_name,
            renewal_session=None,
            error_code=error.code,
            error_message=str(error),
        )
    try:
        renewal_session = _renewal_session_from_payload(payload, now)
    except CookieImportItemError as error:
        return ParsedCookieImportItem(
            entry_name=entry_name,
            entry_sha256=entry_sha256,
            expected_login_name=expected_login_name,
            renewal_session=None,
            error_code=error.code,
            error_message=str(error),
        )
    return ParsedCookieImportItem(
        entry_name=entry_name,
        entry_sha256=entry_sha256,
        expected_login_name=expected_login_name,
        renewal_session=renewal_session,
        error_code=None,
        error_message=None,
    )


def parse_cookie_import_archive(
    stream: BinaryIO,
    original_filename: str,
    now: datetime,
) -> ParsedCookieImportArchive:
    if not original_filename.lower().endswith(".zip"):
        raise CookieArchiveError("ARCHIVE_EXTENSION_INVALID", "Upload filename must end in .zip")
    archive_bytes = _read_archive_bytes(stream)
    items: list[ParsedCookieImportItem] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except (OSError, zipfile.BadZipFile) as exc:
        raise CookieArchiveError("ARCHIVE_INVALID", "Upload is not a valid ZIP archive") from exc
    with archive:
        infos = archive.infolist()
        supported_infos = _validate_archive_entries(infos)
        for info in supported_infos:
            try:
                entry_bytes = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise CookieArchiveError("ARCHIVE_READ_FAILED", "ZIP entry read failed") from exc
            items.append(_parse_item(info.filename, entry_bytes, now))
    return ParsedCookieImportArchive(
        archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        items=tuple(items),
    )
