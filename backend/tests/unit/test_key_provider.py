import os

import pytest
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap

from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


@pytest.fixture
def master_key_path(tmp_path):
    path = tmp_path / "master.key"
    path.write_bytes(os.urandom(32))
    return str(path)


def test_wrap_and_unwrap_round_trip(master_key_path):
    provider = FileSecretKeyProvider(master_key_path)
    raw_dek = os.urandom(32)

    wrapped = provider.wrap_dek(raw_dek)
    unwrapped = provider.unwrap_dek(wrapped)

    assert unwrapped == raw_dek
    assert wrapped != raw_dek


@pytest.mark.parametrize("length", [0, 8, 15, 31, 33, 64])
def test_invalid_master_key_length_fails_at_construction(tmp_path, length):
    path = tmp_path / "bad.key"
    path.write_bytes(os.urandom(length))

    with pytest.raises(ValueError, match="AES-KW requires"):
        FileSecretKeyProvider(str(path))


@pytest.mark.parametrize("length", [16, 24, 32])
def test_valid_master_key_lengths_are_accepted(tmp_path, length):
    path = tmp_path / "good.key"
    path.write_bytes(os.urandom(length))

    provider = FileSecretKeyProvider(str(path))
    raw_dek = os.urandom(32)
    assert provider.unwrap_dek(provider.wrap_dek(raw_dek)) == raw_dek


def test_unwrap_fails_under_a_different_master_key(tmp_path):
    key_a = tmp_path / "a.key"
    key_a.write_bytes(os.urandom(32))
    key_b = tmp_path / "b.key"
    key_b.write_bytes(os.urandom(32))

    provider_a = FileSecretKeyProvider(str(key_a))
    provider_b = FileSecretKeyProvider(str(key_b))

    raw_dek = os.urandom(32)
    wrapped = provider_a.wrap_dek(raw_dek)

    with pytest.raises(InvalidUnwrap):
        provider_b.unwrap_dek(wrapped)
