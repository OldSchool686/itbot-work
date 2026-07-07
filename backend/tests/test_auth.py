import pytest

from backend.utils.auth_jwt import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_and_verify():
    """Password hashing round-trip."""
    pwd = "test_password_123"
    hashed = hash_password(pwd)
    assert verify_password(pwd, hashed)
    assert not verify_password("wrong_password", hashed)


def test_hash_is_different_each_time():
    """Each hash should be unique (different salt)."""
    pwd = "same_password"
    h1 = hash_password(pwd)
    h2 = hash_password(pwd)
    assert h1 != h2
    assert verify_password(pwd, h1)
    assert verify_password(pwd, h2)


def test_jwt_create_and_decode():
    """JWT token creation and decoding round-trip."""
    username = "test_admin"
    token = create_access_token(username)
    decoded_username = decode_access_token(token)
    assert decoded_username == username


def test_jwt_invalid_token():
    """Invalid JWT returns None."""
    result = decode_access_token("invalid.token.here")
    assert result is None
