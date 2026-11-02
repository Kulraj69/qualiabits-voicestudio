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
    GET    /v1/usage                 session or key — this workspace's meter
    GET    /v1/voices                session or key — what the engine can speak
    POST   /v1/audio/speech          API key — metered passthrough to the engine
    POST   /v1/audio/transcriptions  session or key — a recording in, text out,
                                     metered in characters and in its own length
    POST   /v1/jobs                  session or key — queue a longer piece of audio
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

import json
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated, Iterator

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from . import jobs, metering, security, storage, voices as voice_catalogue
from .auth import Principal, principal
from .config import Settings
from .db import ApiKey, Database, Job, SessionToken, Workspace, utcnow
from .upstream import UpstreamClient, UpstreamResult

#: ``starlette.datastructures.UploadFile``, not ``fastapi.UploadFile``: the latter
#: is a *subclass* that lives in FastAPI's route-signature machinery, and a part
#: that Starlette's form parser hands back is not an instance of it. Checking
#: against the wrong one silently refuses every real upload — which is exactly
#: what the first run of this route's tests did.

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
PASSWORD_MIN = 12
PASSWORD_MAX = 200
INPUT_MAX_CHARS = 5000
#: The backstop on a queued job's body. The route's own limit
#: (``jobs.MAX_JOB_CHARS``) is smaller and answers 413 first; this exists so a
#: body far past any real request is refused before it is parsed into memory.
JOB_BODY_MAX_CHARS = 100_000
SPEECH_ROUTE = "/v1/audio/speech"
TRANSCRIBE_ROUTE = "/v1/audio/transcriptions"
#: The engine's own option names on its OpenAI-shaped STT route. A client that
#: sends something else has that field dropped rather than forwarded: the plane
#: proxies this shape, it does not invent parameters for it.
TRANSCRIBE_FIELDS = ("model", "language", "prompt", "response_format", "temperature")
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


def _key_id(who: Principal) -> int | None:
    """The key row a metered request belongs to, or ``None`` for a session caller.

    A dashboard request is attributed to the workspace and says so — the meter's
    per-key section counts only what a key did — which is the same rule the jobs
    route already follows.
    """
    return who.api_key.id if who.api_key is not None else None


def _window_of(byte_count: int) -> str:
    """A human-readable byte ceiling, for the sentence a refusal carries."""
    mebibyte = 1024 * 1024
    if byte_count >= mebibyte:
        return f"{int(byte_count // mebibyte)} MiB"
    return f"{int(byte_count)} bytes"


def transcript_characters(result: UpstreamResult) -> int:
    """How much text the engine handed back, or 0 when that is not knowable.

    Only a JSON answer has a transcript field to count. A caller who asked for
    ``srt`` or ``text`` gets those bytes, and counting the characters of a
    subtitle file would count cue numbers and timecodes as if they were speech —
    so this returns 0 there rather than a number nobody can defend. The meter's
    ``audio_seconds`` still records the upload, which is measured either way.
    """
    if not result.delivered:
        return 0
    content_type = (result.media_type or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return 0
    try:
        payload = json.loads(result.body or b"")
    except (ValueError, UnicodeDecodeError):
        return 0
    text = payload.get("text") if isinstance(payload, dict) else None
    return len(text) if isinstance(text, str) else 0


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
            # The upload ceiling, published so a client can refuse an oversized
            # upload itself instead of streaming it here and losing the polite
            # refusal to a closed connection. Same number the route enforces.
            "transcribe_max_bytes": state.settings.transcribe_max_bytes,
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

    # ── voices ────────────────────────────────────────────────────────────────
    @app.get("/v1/voices")
    async def voices(request: Request, session: DbSession) -> dict:
        """What the engine can speak, for a picker.

        Authenticated like every other customer route: a voice list is small,
        but it is still the engine's internals and it is not public. The engine
        is read twice because neither answer is complete — its voice catalogue
        misses the ids a model declares in its own file, and its engine
        inventory knows those ids but not the aliases and profiles.
        """
        principal(session, request, allow="any")
        _, catalogue = await state.upstream.json_get("/v1/audio/voices")
        _, engines = await state.upstream.json_get("/engines/tts")
        return voice_catalogue.compose(catalogue, engines)

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

    # ── speech in: the metered transcription proxy ────────────────────────────
    @app.post(TRANSCRIBE_ROUTE)
    async def transcribe(request: Request, session: DbSession) -> Response:
        """A customer's recording in, the engine's transcript out, metered.

        Auth is ``any`` — a session from the dashboard or a key from a server
        integration — because the reason speech needs a key (every byte of audio
        produced must be attributable) does not apply to audio being handed to
        us. What does still apply is that the upload is bounded before it is
        parsed, and that nothing reaches the engine when it is refused.
        """
        who = principal(session, request, allow="any")
        ceiling = state.settings.transcribe_max_bytes
        allowance = f"An upload is at most {_window_of(ceiling)}."

        # A declared length over the ceiling is refused before a byte is read.
        declared = request.headers.get("content-length", "").strip()
        if declared.isdigit() and int(declared) > ceiling:
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=_key_id(who),
                route=TRANSCRIBE_ROUTE, status_code=413, error="declared upload too large",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"That upload is larger than {_window_of(ceiling)}. {allowance}",
            )

        try:
            form = await request.form()
        except Exception:  # noqa: BLE001 — a malformed body is the caller's, not ours
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=_key_id(who),
                route=TRANSCRIBE_ROUTE, status_code=400, error="unreadable multipart body",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Send the recording as multipart/form-data, with the audio in a "
                       "'file' part and the options as plain fields.",
            ) from None

        part = form.get("file")
        if not isinstance(part, UploadFile):
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=_key_id(who),
                route=TRANSCRIBE_ROUTE, status_code=400, error="no file part",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="There is no 'file' part in this request. The recording goes in a "
                       "'file' part.",
            )
        content = await part.read()
        # Checked again after reading: a chunked upload declares no length, so the
        # ceiling cannot be enforced from a header alone. Starlette spools a large
        # part to a temporary file, so this is a refusal, not a memory event.
        if len(content) > ceiling:
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=_key_id(who),
                route=TRANSCRIBE_ROUTE, status_code=413, error="upload over the ceiling",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"That upload is {len(content)} bytes and the limit is "
                       f"{_window_of(ceiling)}. {allowance}",
            )
        if not content:
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=_key_id(who),
                route=TRANSCRIBE_ROUTE, status_code=400, error="empty upload",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The 'file' part was empty, so there is nothing to transcribe.",
            )

        # Only the engine's own option names travel on; anything else the caller
        # sent is dropped here rather than forwarded to be interpreted twice.
        fields: dict[str, str] = {}
        for name in TRANSCRIBE_FIELDS:
            value = form.get(name)
            if isinstance(value, str) and value.strip():
                fields[name] = value.strip()

        result = await state.upstream.transcribe(
            filename=part.filename or "recording",
            content=content,
            content_type=part.content_type or "application/octet-stream",
            fields=fields,
        )
        if who.api_key is not None:
            metering.touch_key(session, who.api_key)
        counted = transcript_characters(result)
        metering.record(
            session,
            workspace_id=who.workspace_id,
            api_key_id=_key_id(who),
            route=TRANSCRIBE_ROUTE,
            status_code=result.status,
            transcript_characters=counted,
            upload=content,
            engine_seconds=result.elapsed_seconds,
            error=result.error,
        )
        session.commit()

        # The length of the upload is known whether or not the engine answered —
        # we measured the bytes we were sent — so these headers ride on refusals
        # too, and the sentence about them says so.
        headers = {
            "X-Qualiabits-Workspace": str(who.workspace_id),
            "X-Qualiabits-Engine-Seconds": str(result.elapsed_seconds),
            "X-Qualiabits-Transcript-Characters": str(counted),
            "X-Qualiabits-Upload-Bytes": str(len(content)),
            **result.engine_headers,
        }
        seconds = metering.wav_seconds(content)
        if seconds is not None:
            headers["X-Qualiabits-Audio-Seconds"] = str(seconds)

        if result.delivered:
            return Response(
                content=result.body,
                media_type=result.media_type or "application/json",
                headers=headers,
            )
        if result.status in (502, 504):
            return JSONResponse(
                status_code=result.status,
                content={"detail": "The transcription engine did not answer.",
                         "reason": result.error},
                headers=headers,
            )
        # The engine's own refusal, in its own words.
        return Response(
            content=result.body or b"",
            status_code=result.status,
            media_type=result.media_type or "application/json",
            headers=headers,
        )

    return app


app = create_app()