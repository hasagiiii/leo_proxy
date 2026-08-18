import pytest
from cryptography.exceptions import InvalidTag

from video_task_service.crypto import decrypt_secret, encrypt_secret, token_fingerprint


def test_secret_round_trip_and_associated_data() -> None:
    key = b"k" * 32
    encrypted = encrypt_secret("secret-token", "account:video_token", key=key)
    assert b"secret-token" not in encrypted
    assert decrypt_secret(encrypted, "account:video_token", key=key) == "secret-token"
    with pytest.raises(InvalidTag):
        decrypt_secret(encrypted, "other-account:video_token", key=key)


def test_token_fingerprint_is_short_and_stable() -> None:
    assert token_fingerprint("abc") == token_fingerprint("abc")
    assert len(token_fingerprint("abc")) == 16
