"""The control plane's HTTP surface.

Run it locally:

    QUALI_UPSTREAM_BASE_URL=http://127.0.0.1:3900 \\
    QUALI_DATABASE_URL=sqlite:///$PWD/.hosted/control-plane.db \\
    .venv/bin/python -m uvicorn hosted.control_plane.app:app --port 8080

Routes and who may call them:

    GET    /healthz                 anyone — is this plane up, on which schema
    GET    /healthz/upstream        anyone — is the engine behind it up
    POST   /v1/workspaces           anyone — signup, one workspace per customer
    POST   /v1/auth/token           anyone — email + password -> session token
    POST   /v1/api-keys             session — mint a key (returned once)
    GET    /v1/api-keys             session — list keys, no key material
    DELETE /v1/api-keys/{id}        session — revoke one of your own keys
    GET    /v1/usage                session or key — this workspace's meter
    POST   /v1/audio/speech         API key — metered passthrough to the engine
    POST   /v1/jobs                 session or key — queue a longer piece of audio
    GET    /v1/jobs                 session or key — your jobs, newest first
    GET    /v1/jobs/{id}            session or key — one of your jobs
    DELETE /v1/jobs/{id}            session or key — cancel a queued job
    GET    /v1/jobs/{id}/audio      session or key — the stored audio of a done job

Two deliberate omissions. There is no public signup-then-speak shortcut: speaking
always needs a key, so every byte of audio is attributable to a key. (A job may be
queued from the dashboard instead, in which case it is attributed to the workspace
and the meter says so.) And there is no route that takes a workspace id, so a
customer cannot even ask for somebody else's numbers, let alone read them.
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import jobs, metering, security, storage
from .auth import Principal, principal
from .config import Settings
from .db import ApiKey, Database, Job, SessionToken, Workspace, utcnow
from .upstream import UpstreamClient

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
PASSWORD_MIN = 12
PASSWORD_MAX = 200
INPUT_MAX_CHARS = 5000
#: The backstop on a queued job's body. The route's own limit
#: (``jobs.MAX_JOB_CHARS``) is smaller and answers 413 first; this exists so a
#: body far past any real request is refused before it is parsed into memory.
JOB_BODY_MAX_CHARS = 100_000
SPEECH_ROUTE = "/v1/audio/speech"
JOB_STATUSES = frozenset(
    (jobs.QUEUED, jobs.RUNNING, *jobs.TERMINAL_STATUSES)
)


class HostedState:
    """Everything the app owns: settings, database, engine client, object store."""

    def __init__(
        self,
        settings: Settings,
        upstream: UpstreamClient | None = None,
        store: storage.ObjectStore | None = None,
    ) -> None:
        self.settings = settings
        self.db = Database(settings.database_url)
        self.upstream = upstream or UpstreamClient(
            settings.upstream_base_url, settings.upstream_timeout_seconds
        )
        # Built here, not lazily: a plane configured for S3 with no bucket must
        # fail at startup rather than discover it when the first customer's audio
        # has nowhere to go.
        self.store = store if store is not None else storage.build_store(settings)
        self.schema_state = "unknown"


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


class JobRequest(BaseModel):
    """A queued piece of speech. Same body as the engine's speech route."""

    input: str = Field(default="", max_length=JOB_BODY_MAX_CHARS)
    voice: str = Field(default="", max_length=120)
    response_format: str = Field(default="wav", max_length=16)


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


def _own_job_row(session: Session, job_id: int, workspace_id: int) -> Job:
    """One of this workspace's jobs, or a 404.

    A job id that belongs to somebody else answers exactly like one that does not
    exist, so the route cannot be used to learn that a given id is real.
    """
    job = session.execute(
        select(Job).where(Job.id == job_id, Job.workspace_id == workspace_id)
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such job."
        )
    return job


def create_app(
    settings: Settings | None = None,
    upstream: UpstreamClient | None = None,
    store: storage.ObjectStore | None = None,
) -> FastAPI:
    state = HostedState(settings or Settings.from_env(), upstream, store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Create or migrate the schema on startup, and remember which of the two
        # it was, so /healthz can say it instead of leaving it to a log line.
        state.schema_state = state.db.init_schema(state.settings.schema_version)
        try:
            yield
        finally:
            await state.upstream.aclose()
            state.db.dispose()

    app = FastAPI(
        title="QualiaBits Voice control plane",
        version="0.2.0",
        summary="Workspaces, keys, jobs and metering in front of the VoiceStudio engine.",
        lifespan=lifespan,
    )
    app.state.hosted = state

    # ── health ────────────────────────────────────────────────────────────────
    @app.get("/healthz")
    def healthz() -> dict:
        # Sync on purpose: the job counts are a database read, and FastAPI runs a
        # sync route in a worker thread rather than blocking the event loop.
        with state.db.session() as session:
            counts = {
                name: count
                for name, count in session.execute(
                    select(Job.status, func.count(Job.id)).group_by(Job.status)
                ).all()
            }
        return {
            "status": "ok",
            "service": "qualiabits-voice-control-plane",
            "schema_version": state.settings.schema_version,
            "schema_state": state.schema_state,
            "upstream_base_url": state.settings.upstream_base_url,
            "storage": state.store.describe(),
            "jobs": counts,
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

    # ── jobs ──────────────────────────────────────────────────────────────────
    @app.post("/v1/jobs", status_code=status.HTTP_202_ACCEPTED)
    def create_job(body: JobRequest, request: Request, session: DbSession) -> dict:
        who = principal(session, request, allow="any")
        # Whitespace is stripped before queueing, so the characters the meter
        # counts are the characters the engine will be asked to speak.
        text = (body.input or "").strip()
        if not text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'input' must be a non-empty string.",
            )
        if len(text) > jobs.MAX_JOB_CHARS:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    f"'input' is longer than {jobs.MAX_JOB_CHARS} characters. "
                    "Split it into more than one job."
                ),
            )
        job = jobs.enqueue(
            session,
            workspace_id=who.workspace_id,
            api_key_id=who.api_key.id if who.api_key is not None else None,
            text=text,
            voice=body.voice.strip(),
            response_format=(body.response_format or "wav").strip().lower() or "wav",
            max_attempts=state.settings.job_max_attempts,
        )
        session.commit()
        return {
            "job": jobs.job_dict(job, full_input=True),
            "queued_via": who.via,
            "note": (
                "Nothing has been spoken yet. Poll GET /v1/jobs/{id} and download "
                "the audio from /v1/jobs/{id}/audio when the status is succeeded. "
                "A worker process does the work; the plane restarts safely."
            ),
        }

    @app.get("/v1/jobs")
    def list_jobs(
        request: Request,
        session: DbSession,
        job_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict:
        who = principal(session, request, allow="any")
        statement = select(Job).where(Job.workspace_id == who.workspace_id)
        if job_status:
            wanted = job_status.strip().lower()
            if wanted not in JOB_STATUSES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"status must be one of {', '.join(sorted(JOB_STATUSES))}.",
                )
            statement = statement.where(Job.status == wanted)
        rows = session.execute(
            statement.order_by(Job.id.desc()).limit(limit)
        ).scalars().all()
        total = session.execute(
            select(func.count(Job.id)).where(Job.workspace_id == who.workspace_id)
        ).scalar_one()
        return {
            "jobs": [jobs.job_dict(row) for row in rows],
            "returned": len(rows),
            "limit": limit,
            "workspace_total": total,
        }

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: int, request: Request, session: DbSession) -> dict:
        who = principal(session, request, allow="any")
        job = _own_job_row(session, job_id, who.workspace_id)
        return {"job": jobs.job_dict(job, full_input=True)}

    @app.delete("/v1/jobs/{job_id}")
    def cancel_job(job_id: int, request: Request, session: DbSession) -> dict:
        who = principal(session, request, allow="any")
        job = _own_job_row(session, job_id, who.workspace_id)
        if not jobs.cancel(session, job):
            # Only a queued job can be cancelled: a running one is already being
            # spoken, and a finished one is a receipt, not a draft.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"This job is {job.status} and cannot be cancelled. "
                    "Only a queued job can be."
                ),
            )
        session.commit()
        return {"job": jobs.job_dict(job, full_input=True)}

    @app.get("/v1/jobs/{job_id}/audio")
    def job_audio(job_id: int, request: Request, session: DbSession) -> Response:
        who = principal(session, request, allow="any")
        job = _own_job_row(session, job_id, who.workspace_id)
        if job.status != jobs.SUCCEEDED or not job.object_key:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "job_status": job.status,
                    "detail": "This job has no audio yet.",
                    "error": job.error or "",
                },
            )
        try:
            # Read from the object store at request time, not from anything the
            # worker left in memory: the file is the record.
            body = state.store.get(job.object_key)
        except storage.StorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"The stored audio could not be read: {exc}",
            ) from exc
        headers = {
            "X-Qualiabits-Job": str(job.id),
            "X-Qualiabits-Object-Sha256": job.object_sha256 or "",
        }
        if job.audio_seconds is not None:
            headers["X-Qualiabits-Audio-Seconds"] = str(job.audio_seconds)
        return Response(
            content=body,
            media_type=storage.content_type_for_key(job.object_key),
            headers=headers,
        )

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