"""Phone numbers: normalised, encrypted at rest, hashed for exact lookups."""

from __future__ import annotations

import hashlib
import re

from app.crypto import DecryptError, decrypt, encrypt

_DIGITS = re.compile(r"[^0-9+]")


def normalize_phone(raw: str | None) -> str:
    """``0912 345 678`` / ``+84 912-345-678`` → ``0912345678`` (Vietnamese local form)."""
    s = _DIGITS.sub("", (raw or "").strip())
    if s.startswith("+84"):
        s = "0" + s[3:]
    elif s.startswith("84") and len(s) >= 11:
        s = "0" + s[2:]
    s = s.replace("+", "")
    return s


def phone_hash(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()


def encrypt_phone(raw: str | None) -> tuple[str | None, str | None, str | None]:
    """``(encrypted, hash, last4)`` for storage; all None when the number is empty."""
    phone = normalize_phone(raw)
    if not phone:
        return None, None, None
    return encrypt(phone), phone_hash(phone), phone[-4:]


def decrypt_phone(token: str | None) -> str:
    if not token:
        return ""
    try:
        return decrypt(token)
    except DecryptError:
        return ""
