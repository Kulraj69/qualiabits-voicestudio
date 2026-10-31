"""The control plane's HTTP surface.

Run it locally:

    QUALI_UPSTREAM_BASE_URL=http://127.0.0.1:3900 \\
    QUALI_DATABASE_URL=sqlite:///$PWD/.hosted/control-plane.db \\
    .venv/bin/python -m uvicorn hosted.control_plane.app:app --port 8080

Routes and who may call them:

    GET    /healthz                 anyone — is this plane up
    GET    /healthz/upstream        anyone — is the engine behind it up
    POST   /v1/workspaces           anyone — signup, one workspace per customer
    POST   /v1/auth/token           anyone — email + password -> session token
    POST   /v1/api-keys             session — mint a key (returned once)
    GET    /v1/api-keys             session — list keys, no key material
    DELETE /v1/api-keys/{id}        session — revoke one of your own keys
    GET    /v1/usage                session or key — this workspace's meter
    POST   /v1/audio/speech         API key — metered passthrough to the engine

Two deliberate omissions. There is no public signup-then-speak shortcut: speaking
always needs a key, so every byte of audio is attributable to a key. And there is
no route that takes a workspace id, so a customer cannot even ask for somebody
else's numbers, let alone read them.
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated, Iterator

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import metering, security
from .auth import Principal, principal
from .config import Settings
from .db import ApiKey, Database, SessionToken, Workspace, utcnow
from .upstream import UpstreamClient

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
PASSWORD_MIN = 12
PASSWORD_MAX = 200
INPUT_MAX_CHARS = 5000
SPEECH_ROUTE = "/v1/audio/speech"


class HostedState:
    """Everything the app owns: settings, database, engine client."""

    def __init__(self, settings: Settings, upstream: UpstreamClient | None = None) -> None:
        self.settings = settings
        self.db = Database(settings.database_url)
        self.upstream = upstream or UpstreamClient(
            settings.upstream_base_url, settings.upstream_timeout_seconds
        )


def get_state(request: Request) -> HostedState:
    return request.app.state.hosted


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.hosted.db.session() as session:
        yield session


# Mutating routes commit inside the request, before they return. The dependency
# teardown above also commits, but teardown runs after the response is handed
# back, so a caller that writes and immediately reads can lose the race and see
# the old row. The end-to-end run lost exactly that race over a key revocation:
# the DELETE answered 200 and the very next request was still accepted. Sessions
# and workspaces had the same shape of bug and were committed explicitly too.


DbSession = Annotated[Session, Depends(get_db)]
State = Annotated[HostedState, Depends(get_state)]


class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
    display_name: str = Field(default="", max_length=120)


class TokenRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


class ApiKeyRequest(BaseModel):
    label: str = Field(default="", max_length=120)


def normalise_email(raw: str) -> str:
    email = raw.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That does not look like an email address.",
        )
    return email


def workspace_dict(workspace: Workspace) -> dict:
    return {
        "id": workspace.id,
        "email": workspace.email,
        "display_name": workspace.display_name,
        "created_at": workspace.created_at.isoformat(),
    }


def key_dict(row: ApiKey, *, include_secret: str | None = None) -> dict:
    out = {
        "id": row.id,
        "key_id": row.key_id,
        "label": row.label,
        "created_at": row.created_at.isoformat(),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }
    if include_secret is not None:
        out["api_key"] = include_secret
        out["shown_once"] = (
            "This is the only time the key is returned. Only a digest is stored."
        )
    return out


def create_app(settings: Settings | None = None, upstream: UpstreamClient | None = None) -> FastAPI:
    state = HostedState(settings or Settings.from_env(), upstream)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state.db.init_schema(state.settings.schema_version)
        try:
            yield
        finally:
            await state.upstream.aclose()
            state.db.dispose()

    app = FastAPI(
        title="QualiaBits Voice control plane",
        version="0.1.0",
        summary="Workspaces, keys and metering in front of the VoiceStudio engine.",
        lifespan=lifespan,
    )
    app.state.hosted = state

    # ── health ────────────────────────────────────────────────────────────────
    @app.get("/healthz")
    async def healthz() -> dict:
        return {
            "status": "ok",
            "service": "qualiabits-voice-control-plane",
            "schema_version": state.settings.schema_version,
            "upstream_base_url": state.settings.upstream_base_url,
        }

    @app.get("/healthz/upstream")
    async def healthz_upstream() -> dict:
        return {"upstream_base_url": state.settings.upstream_base_url,
                **(await state.upstream.healthy())}

    # ── workspaces and login ──────────────────────────────────────────────────
    @app.post("/v1/workspaces", status_code=status.HTTP_201_CREATED)
    def create_workspace(body: SignupRequest, session: DbSession) -> dict:
        email = normalise_email(body.email)
        exists = session.execute(
            select(Workspace).where(Workspace.email == email)
        ).scalar_one_or_none()
        if exists is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A workspace already exists for that email.",
            )
        salt, digest, params = security.hash_password(body.password)
        workspace = Workspace(
            email=email,
            display_name=body.display_name.strip(),
            password_salt=salt,
            password_hash=digest,
            password_params=params,
        )
        session.add(workspace)
        try:
            session.flush()
        except IntegrityError:
            # Two signups for the same address at once. The unique index decides,
            # and the loser gets the same answer as the sequential case.
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A workspace already exists for that email.",
            )
        session.commit()
        return {"workspace": workspace_dict(workspace)}

    @app.post("/v1/auth/token")
    def create_token(body: TokenRequest, session: DbSession) -> dict:
        email = body.email.strip().lower()
        workspace = session.execute(
            select(Workspace).where(Workspace.email == email)
        ).scalar_one_or_none()
        if workspace is None:
            # Same work, same answer, whether or not the address is registered.
            security.dummy_verify(body.password)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email or password is wrong.",
            )
        if not security.verify_password(
            body.password,
            workspace.password_salt,
            workspace.password_hash,
            workspace.password_params,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email or password is wrong.",
            )
        token, digest = security.new_session_token()
        expires_at = utcnow() + timedelta(seconds=state.settings.session_ttl_seconds)
        session.add(
            SessionToken(
                workspace_id=workspace.id, token_digest=digest, expires_at=expires_at
            )
        )
        session.commit()
        return {
            "token": token,
            "token_type": "bearer",
            "expires_at": expires_at.isoformat(),
            "workspace": workspace_dict(workspace),
        }

    # ── API keys ──────────────────────────────────────────────────────────────
    @app.post("/v1/api-keys", status_code=status.HTTP_201_CREATED)
    def create_api_key(
        body: ApiKeyRequest, request: Request, session: DbSession
    ) -> dict:
        who = principal(session, request, allow="session")
        full, key_id, digest = security.new_api_key()
        row = ApiKey(
            workspace_id=who.workspace_id,
            key_id=key_id,
            key_digest=digest,
            label=body.label.strip(),
        )
        session.add(row)
        session.commit()
        return {"api_key_record": key_dict(row, include_secret=full)}

    @app.get("/v1/api-keys")
    def list_api_keys(request: Request, session: DbSession) -> dict:
        who = principal(session, request, allow="session")
        rows = session.execute(
            select(ApiKey)
            .where(ApiKey.workspace_id == who.workspace_id)
            .order_by(ApiKey.id)
        ).scalars().all()
        return {"api_keys": [key_dict(row) for row in rows]}

    @app.delete("/v1/api-keys/{key_row_id}")
    def revoke_api_key(key_row_id: int, request: Request, session: DbSession) -> dict:
        who = principal(session, request, allow="session")
        row = session.execute(
            select(ApiKey).where(
                ApiKey.id == key_row_id, ApiKey.workspace_id == who.workspace_id
            )
        ).scalar_one_or_none()
        if row is None:
            # Not "forbidden": a customer does not get to learn that somebody
            # else's key id exists.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No such key."
            )
        if row.revoked_at is None:
            row.revoked_at = utcnow()
        session.commit()
        return {"revoked": True, "api_key_record": key_dict(row)}

    # ── usage ─────────────────────────────────────────────────────────────────
    @app.get("/v1/usage")
    def usage(request: Request, session: DbSession) -> dict:
        who: Principal = principal(session, request, allow="any")
        return {"usage": metering.summary(session, who.workspace_id).as_dict(),
                "read_via": who.via}

    # ── the metered proxy ─────────────────────────────────────────────────────
    @app.post(SPEECH_ROUTE)
    async def speech(request: Request, session: DbSession) -> Response:
        who = principal(session, request, allow="api_key")
        api_key = who.require_api_key()
        try:
            payload = await request.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=api_key.id,
                route=SPEECH_ROUTE, status_code=400, characters=0,
                error="request body is not a JSON object",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Send a JSON object, the same body the engine's "
                       "/v1/audio/speech takes.",
            )
        text = payload.get("input")
        if not isinstance(text, str) or not text.strip():
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=api_key.id,
                route=SPEECH_ROUTE, status_code=400, characters=0,
                error="missing input",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'input' must be a non-empty string.",
            )
        if len(text) > INPUT_MAX_CHARS:
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=api_key.id,
                route=SPEECH_ROUTE, status_code=413, characters=0,
                error="input too long",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"'input' is longer than {INPUT_MAX_CHARS} characters.",
            )

        result = await state.upstream.speech(payload)
        metering.touch_key(session, api_key)
        metering.record(
            session,
            workspace_id=who.workspace_id,
            api_key_id=api_key.id,
            route=SPEECH_ROUTE,
            status_code=result.status,
            characters=len(text),
            body=result.body if result.delivered else None,
            engine_seconds=result.elapsed_seconds,
            error=result.error,
        )
        session.commit()

        if result.delivered:
            seconds = metering.wav_seconds(result.body or b"")
            headers = {
                "X-Qualiabits-Workspace": str(who.workspace_id),
                "X-Qualiabits-Engine-Seconds": str(result.elapsed_seconds),
                **result.engine_headers,
            }
            if seconds is not None:
                headers["X-Qualiabits-Audio-Seconds"] = str(seconds)
            return Response(
                content=result.body,
                media_type=result.media_type or "application/octet-stream",
                headers=headers,
            )
        if result.status in (502, 504):
            return JSONResponse(
                status_code=result.status,
                content={"detail": "The speech engine did not answer.",
                         "reason": result.error},
            )
        # The engine's own refusal, passed through as it wrote it.
        return Response(
            content=result.body or b"",
            status_code=result.status,
            media_type=result.media_type or "application/json",
        )

    return app


app = create_app()