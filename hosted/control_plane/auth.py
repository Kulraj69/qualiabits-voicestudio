"""Who is asking: a session token (the dashboard) or an API key (the product API).

Both arrive as ``Authorization: Bearer <credential>``; an API key additionally
carries the ``qbv_`` prefix, which is what decides which row to look in. A
session token is not accepted on the metered routes and an API key is not
accepted where a session is required, so a leaked dashboard token cannot be used
to rack up metered requests.

Every refusal is a plain 401 with one sentence. The plane never says whether the
credential existed, only that it does not work.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import security
from .db import ApiKey, SessionToken, Workspace, utcnow


@dataclass
class Principal:
    """Who this request belongs to."""

    workspace: Workspace
    api_key: ApiKey | None = None
    session_token: SessionToken | None = None

    @property
    def workspace_id(self) -> int:
        return self.workspace.id

    @property
    def via(self) -> str:
        return "api_key" if self.api_key is not None else "session"

    def require_api_key(self) -> ApiKey:
        """The key row, for routes that only accept key auth.

        ``principal(..., allow="api_key")`` guarantees this, and the metered route
        needs a non-optional row to attribute usage to. Failing loudly here beats
        ``None`` propagating into the ledger.
        """
        if self.api_key is None:
            raise RuntimeError("no API key on a principal resolved for api_key auth")
        return self.api_key


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def presented_credential(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header:
        scheme, _, value = header.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
        return None
    # Some clients cannot set Authorization (browser audio tags, quick curl
    # tests). Accepting X-Api-Key is not a second auth scheme, just a second
    # place to put the same one.
    return request.headers.get("x-api-key", "").strip() or None


def _api_key_principal(db: Session, presented: str) -> Principal:
    split = security.split_api_key(presented)
    if split is None:
        raise _unauthorized("That API key is not in the expected format.")
    key_id, full = split
    record = db.execute(
        select(ApiKey).where(ApiKey.key_id == key_id)
    ).scalar_one_or_none()
    if record is None or not security.verify_digest(full, record.key_digest):
        raise _unauthorized("That API key is not recognised.")
    if record.revoked_at is not None:
        raise _unauthorized("That API key has been revoked.")
    workspace = db.get(Workspace, record.workspace_id)
    if workspace is None:
        raise _unauthorized("That API key's workspace no longer exists.")
    return Principal(workspace=workspace, api_key=record)


def _session_principal(db: Session, presented: str) -> Principal:
    digest = security.digest_secret(presented)
    record = db.execute(
        select(SessionToken).where(SessionToken.token_digest == digest)
    ).scalar_one_or_none()
    if record is None:
        raise _unauthorized("That session is not recognised.")
    if record.revoked_at is not None:
        raise _unauthorized("That session has been signed out.")
    if _naive_utc(record.expires_at) <= utcnow():
        raise _unauthorized("That session has expired.")
    workspace = db.get(Workspace, record.workspace_id)
    if workspace is None:
        raise _unauthorized("That session's workspace no longer exists.")
    return Principal(workspace=workspace, session_token=record)


def _naive_utc(value):
    """SQLite hands back naive datetimes; compare in UTC either way."""
    if value.tzinfo is None:
        return value.replace(tzinfo=utcnow().tzinfo)
    return value


def principal(db: Session, request: Request, *, allow: str) -> Principal:
    """Resolve the caller. ``allow`` is "session", "api_key" or "any"."""
    presented = presented_credential(request)
    if not presented:
        raise _unauthorized("Send an Authorization: Bearer credential.")
    is_key = security.looks_like_api_key(presented)
    if is_key and allow in ("api_key", "any"):
        return _api_key_principal(db, presented)
    if not is_key and allow in ("session", "any"):
        return _session_principal(db, presented)
    if is_key:
        raise _unauthorized(
            "That is an API key; this route needs a dashboard session."
        )
    raise _unauthorized("That is a session token; this route needs an API key.")