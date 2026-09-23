"""API-key auth and Fernet encryption for stored 授权码."""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import Header, HTTPException, status

from .config import BASE_DIR, get_settings

_KEY_FILE = BASE_DIR / ".fernet.key"


def _load_or_create_key() -> bytes:
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    return key


@lru_cache
def get_fernet() -> Fernet:
    key = get_settings().encryption_key.encode() if get_settings().encryption_key else _load_or_create_key()
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return get_fernet().decrypt(ciphertext.encode()).decode()


def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    if x_api_key is None or not secrets.compare_digest(x_api_key, get_settings().api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-API-Key",
        )
    return x_api_key
