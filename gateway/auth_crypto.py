"""Local administrator password and session token primitives."""

import hashlib
import secrets


def password_hash(password: str, salt: str | None = None) -> dict[str, str]:
    """PBKDF2 is stdlib-only so local setup has no extra secret dependency."""
    salt_bytes = bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 600_000)
    return {"algorithm": "pbkdf2_sha256", "iterations": "600000", "salt": salt_bytes.hex(), "hash": digest.hex()}


def admin_session_version(admin: dict[str, str]) -> str:
    return hashlib.sha256(f"{admin['salt']}:{admin['hash']}".encode("utf-8")).hexdigest()


def verify_password(password: str, admin: dict[str, str]) -> bool:
    candidate = password_hash(password, str(admin["salt"]))
    return secrets.compare_digest(candidate["hash"], str(admin["hash"]))


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
