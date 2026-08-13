from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, aes_key_wrap


class KeyProvider(Protocol):
    def wrap_dek(self, raw_dek: bytes) -> bytes: ...
    def unwrap_dek(self, wrapped_dek: bytes) -> bytes: ...


class FileSecretKeyProvider:
    def __init__(self, master_key_path: str) -> None:
        self._master_key = Path(master_key_path).read_bytes()

    def wrap_dek(self, raw_dek: bytes) -> bytes:
        return aes_key_wrap(self._master_key, raw_dek)

    def unwrap_dek(self, wrapped_dek: bytes) -> bytes:
        return aes_key_unwrap(self._master_key, wrapped_dek)
