from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from video_task_service.config import get_settings


def master_key() -> bytes:
    encoded = get_settings().credential_master_key.get_secret_value()
    try:
        key = base64.urlsafe_b64decode(encoded)
    except Exception as exc:  # pragma: no cover - configuration failure
        raise RuntimeError("credential master key is not valid URL-safe base64") from exc
    if len(key) != 32:
        raise RuntimeError("credential master key must decode to exactly 32 bytes")
    return key


def encrypt_secret(value: str, associated_data: str, *, key: bytes | None = None) -> bytes:
    encryption_key = key or master_key()
    nonce = os.urandom(12)
    ciphertext = AESGCM(encryption_key).encrypt(
        nonce,
        value.encode("utf-8"),
        associated_data.encode("utf-8"),
    )
    return nonce + ciphertext


def decrypt_secret(value: bytes, associated_data: str, *, key: bytes | None = None) -> str:
    encryption_key = key or master_key()
    if len(value) < 29:
        raise ValueError("credential ciphertext is too short")
    plaintext = AESGCM(encryption_key).decrypt(
        value[:12],
        value[12:],
        associated_data.encode("utf-8"),
    )
    return plaintext.decode("utf-8")


def token_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
