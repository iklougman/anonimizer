from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, aes_key_wrap


class KeyProvider(Protocol):
    def wrap_dek(self, raw_dek: bytes) -> bytes: ...
    def unwrap_dek(self, wrapped_dek: bytes) -> bytes: ...


_VALID_MASTER_KEY_LENGTHS = (16, 24, 32)


class FileSecretKeyProvider:
    def __init__(self, master_key_path: str) -> None:
        self._master_key = Path(master_key_path).read_bytes()
        # Validated at construction so a misconfigured or truncated key file fails fast
        # and legibly at startup, rather than deep inside aes_key_wrap on the first
        # tenant creation with an opaque error.
        if len(self._master_key) not in _VALID_MASTER_KEY_LENGTHS:
            raise ValueError(
                f"master key at {master_key_path} is {len(self._master_key)} bytes; "
                f"AES-KW requires one of {_VALID_MASTER_KEY_LENGTHS}"
            )

    def wrap_dek(self, raw_dek: bytes) -> bytes:
        return aes_key_wrap(self._master_key, raw_dek)

    def unwrap_dek(self, wrapped_dek: bytes) -> bytes:
        return aes_key_unwrap(self._master_key, wrapped_dek)
