"""Password hashing, session tokens and API keys.

Rules this module keeps, because getting them wrong is how hosted products leak:

- Passwords are never stored, only a scrypt digest with its own salt. The scrypt
  parameters travel with the digest, so they can be raised later without
  invalidating existing accounts.
- API keys are shown once, at creation. What is stored is a SHA-256 digest of the
  whole key plus the key's public id, which is what a lookup uses. A leaked
  database therefore does not hand over working keys. Keys are 32 random bytes,
  so a digest without a work factor is the right trade here — the input is not
  guessable, and hashing on every API call needs to stay cheap.
- Every comparison of secret material goes through ``hmac.compare_digest``.
- Key shape: ``qbv_<key_id>_<secret>``. The ``qbv_`` prefix is what tells the
  plane that a bearer token is an API key rather than a session token, and it
  makes a leaked key recognisable in a log scan.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

API_KEY_PREFIX = "qbv"
_KEY_ID_BYTES = 8
_SECRET_BYTES = 32
_SESSION_BYTES = 32

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_PARAMS = f"scrypt-n{SCRYPT_N}-r{SCRYPT_R}-p{SCRYPT_P}"


def _scrypt(password: str, salt: bytes, params: str) -> bytes:
    if params != SCRYPT_PARAMS:
        raise ValueError(f"Unsupported password digest parameters: {params!r}")
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        # 128 * n * r = 16 MiB; OpenSSL's default 32 MiB cap would refuse n=2**15
        # later, so the limit is stated here rather than discovered then.
        maxmem=64 * 1024 * 1024,
    )


def hash_password(password: str) -> tuple[str, str, str]:
    """Return ``(salt_hex, digest_hex, params)``. The plaintext is not returned."""
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_PARAMS)
    return salt.hex(), digest.hex(), SCRYPT_PARAMS


def verify_password(password: str, salt_hex: str, digest_hex: str, params: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    try:
        candidate = _scrypt(password, salt, params)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, expected)


def dummy_verify(password: str) -> None:
    """Burn the same work as a real verify, for logins against unknown emails.

    Without this, ``POST /v1/auth/token`` answers "unknown email" in microseconds
    and "wrong password" in ~40 ms, which is a free account-enumeration oracle.
    """
    _scrypt(password, b"\x00" * 16, SCRYPT_PARAMS)


def new_api_key() -> tuple[str, str, str]:
    """Return ``(full_key, key_id, digest)``. Only ``full_key`` is shown once."""
    key_id = secrets.token_hex(_KEY_ID_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    full = f"{API_KEY_PREFIX}_{key_id}_{secret}"
    return full, key_id, digest_secret(full)


def digest_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_digest(value: str, digest_hex: str) -> bool:
    """Constant-time check of a presented secret against a stored digest."""
    if not isinstance(digest_hex, str) or not digest_hex:
        return False
    return hmac.compare_digest(digest_secret(value), digest_hex)


def split_api_key(presented: str) -> tuple[str, str] | None:
    """Return ``(key_id, full_key)`` for a well-formed key, else ``None``.

    Shape only. Whether the key exists or is live is the database's answer, and
    the caller must still compare digests in constant time.

    The split is limited to three parts on purpose: the secret comes from
    ``token_urlsafe``, whose alphabet includes ``_`` and ``-``, so splitting on
    every underscore turns a perfectly good key into "not in the expected
    format". That was a real bug here, found by a test that minted keys until one
    contained an underscore.
    """
    if not isinstance(presented, str):
        return None
    parts = presented.split("_", 2)
    if len(parts) != 3:
        return None
    prefix, key_id, secret = parts
    if prefix != API_KEY_PREFIX or not secret:
        return None
    if len(key_id) != _KEY_ID_BYTES * 2:
        return None
    try:
        bytes.fromhex(key_id)
    except ValueError:
        return None
    return key_id, presented


def looks_like_api_key(presented: str) -> bool:
    return isinstance(presented, str) and presented.startswith(f"{API_KEY_PREFIX}_")


def new_session_token() -> tuple[str, str]:
    """Return ``(token, digest)``. Only the digest is stored."""
    token = secrets.token_urlsafe(_SESSION_BYTES)
    return token, digest_secret(token)