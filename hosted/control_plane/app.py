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
    POST   /v1/projects             session or key — a manuscript, split and queued
    GET    /v1/projects             session or key — your projects, newest first
    GET    /v1/projects/{id}        session or key — one project and its chapters
    DELETE /v1/projects/{id}        session or key — cancel its queued chapters
    GET    /v1/projects/{id}/audio  session or key — the assembled audiobook, when
                                     every chapter has been spoken
    POST   /v1/dubs                 API key — a recording in, the engine's words
                                     out, and those words spoken back in a voice
    GET    /v1/dubs                 session or key — your dubs, newest first
    GET    /v1/dubs/{id}            session or key — one dub
    GET    /v1/dubs/{id}/audio      session or key — the dubbed WAV

Two deliberate omissions. There is no public signup-then-speak shortcut: speaking
always needs a key, so every byte of audio is attributable to a key. (A job may be
queued from the dashboard instead, in which case it is attributed to the workspace
and the meter says so.) And there is no route that takes a workspace id, so a
customer cannot even ask for somebody else's numbers, let alone read them.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated, Iterator, NoReturn

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

from . import (
    dubbing,
    jobs,
    limits,
    metering,
    projects,
    security,
    storage,
    voices as voice_catalogue,
)
from .auth import Principal, principal
from .config import Settings
from .db import (
    ApiKey,
    Database,
    Dub,
    Job,
    Project,
    ProjectChapter,
    SessionToken,
    Workspace,
    utcnow,
)
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
DUB_ROUTE = "/v1/dubs"
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


class LimitsRequest(BaseModel):
    """Slice G: a workspace's own limits, which are its own spend guard.

    Absent means "leave alone"; an explicit ``null`` means "no limit". That
    distinction is the whole reason this body is not two ``int = 0`` fields: a
    dashboard that sends a partial body must not silently clear the other limit.
    ``0`` is accepted and means the same as ``null``, because a client that has
    the number in hand should not have to guess which spelling we prefer.
    """

    character_ceiling: int | None = Field(default=None, ge=0, le=1_000_000_000)
    requests_per_minute: int | None = Field(default=None, ge=0, le=1_000_000)


class ProjectRequest(BaseModel):
    """A manuscript. Long by design, so the ceiling is checked rather than declared.

    ``max_length`` is deliberately absent: a pydantic length refusal is a 422 with
    a schema-shaped body, and the sentence a customer should get names the ceiling
    in characters and says what to do about it.
    """

    title: str = Field(default="", max_length=projects.TITLE_MAX_CHARS)
    input: str = Field(default="")
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


def _own_project_row(session: Session, project_id: int, workspace_id: int) -> Project:
    """One of this workspace's projects, or a 404 — the same shape as a job row.

    A project id that belongs to somebody else answers exactly like one that does
    not exist, so the route cannot be used to learn that an id is real.
    """
    row = session.execute(
        select(Project).where(
            Project.id == project_id, Project.workspace_id == workspace_id
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such project."
        )
    return row


def _own_dub_row(session: Session, dub_id: int, workspace_id: int) -> Dub:
    """One of this workspace's dubs, or a 404 — the same shape as a job row.

    A dub id that belongs to somebody else answers exactly like one that does not
    exist, so the route cannot be used to learn that a given id is real.
    """
    row = session.execute(
        select(Dub).where(Dub.id == dub_id, Dub.workspace_id == workspace_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such dub."
        )
    return row


def _chapter_rows(session: Session, project_id: int) -> list[tuple[ProjectChapter, Job]]:
    """A project's chapters in reading order, each with the job that speaks it.

    An inner join on purpose: a chapter whose job has gone is not a chapter with
    unknown audio, it is a row that cannot be assembled, and hiding it would make
    the audiobook silently shorter than the manuscript.
    """
    return list(
        session.execute(
            select(ProjectChapter, Job)
            .join(Job, Job.id == ProjectChapter.job_id)
            .where(ProjectChapter.project_id == project_id)
            .order_by(ProjectChapter.chapter_index)
        ).all()
    )


def project_status(statuses: list[str]) -> str:
    """One word for a whole project, decided in the order a reader cares about.

    A project is only ``ready`` when every chapter has been spoken; one failed
    chapter is a ``failed`` project even if the rest are still queued, because
    that is the fact the reader has to act on.
    """
    if not statuses:
        return "empty"
    if all(item == jobs.SUCCEEDED for item in statuses):
        return "ready"
    for word in (jobs.RUNNING, jobs.QUEUED, jobs.FAILED, jobs.CANCELLED):
        if word in statuses:
            return word
    return "unknown"


def project_dict(
    project: Project, rows: list[tuple[ProjectChapter, Job]]
) -> dict:
    """What a customer sees of a project, including per-chapter progress."""
    chapters = [
        {
            "index": chapter.chapter_index,
            "position": chapter.chapter_index + 1,
            "characters": chapter.characters,
            "status": job.status,
            "job_id": job.id,
            "error": job.error or "",
            "audio_seconds": job.audio_seconds,
            "audio_bytes": job.audio_bytes,
            "audio_url": (
                f"/v1/jobs/{job.id}/audio" if job.status == jobs.SUCCEEDED else None
            ),
            "preview": (job.input_text or "")[:jobs.INPUT_PREVIEW_CHARS],
        }
        for chapter, job in rows
    ]
    counts: dict[str, int] = {}
    for chapter in chapters:
        counts[chapter["status"]] = counts.get(chapter["status"], 0) + 1
    spoken_seconds = sum(
        chapter["audio_seconds"] for chapter in chapters if chapter["audio_seconds"]
    )
    return {
        "id": project.id,
        "title": project.title,
        "voice": project.voice,
        "response_format": project.response_format,
        "status": project_status([chapter["status"] for chapter in chapters]),
        "chapter_count": project.chapter_count,
        "source_characters": project.source_characters,
        "chapter_max_chars": project.chapter_max_chars,
        "chapter_characters": [chapter["characters"] for chapter in chapters],
        "statuses": counts,
        "spoken_chapters": counts.get(jobs.SUCCEEDED, 0),
        "spoken_seconds": round(spoken_seconds, 3),
        "created_at": project.created_at.isoformat(),
        "chapters": chapters,
        "audio_url": f"/v1/projects/{project.id}/audio",
    }


def _key_id(who: Principal) -> int | None:
    """The key row a metered request belongs to, or ``None`` for a session caller.

    A dashboard request is attributed to the workspace and says so — the meter's
    per-key section counts only what a key did — which is the same rule the jobs
    route already follows.
    """
    return who.api_key.id if who.api_key is not None else None


def _refuse_limits(
    session: Session,
    *,
    who: Principal,
    api_key: ApiKey | None,
    route: str,
    refusal: limits.Refusal,
) -> NoReturn:
    """Record the refusal the way the meter records everything else, then stop.

    Two decisions worth naming. The event carries **zero** characters: the text
    never reached the engine, and charging the ceiling for a request the ceiling
    itself refused would make the limit inflate its own spend. And the refusal is
    an ordinary row in the ledger with its own status code, so "how often was this
    workspace told no, and why" is a question the meter can already answer —
    ``by_status`` counts 402 and 429 apart from real failures.
    """
    metering.record(
        session,
        workspace_id=who.workspace_id,
        api_key_id=None if api_key is None else api_key.id,
        route=route,
        status_code=refusal.status_code,
        characters=0,
        error=refusal.error,
    )
    session.commit()
    # The body is an object, not a sentence: a client branches on ``error`` and a
    # human reads ``detail``. A prose-only refusal forces every integration to
    # pattern-match English, and the plane's own transcription route already
    # answers with an object detail for the same reason.
    raise HTTPException(
        status_code=refusal.status_code,
        detail=refusal.as_dict(),
        headers=refusal.http_headers(),
    )


def _limits_for(session: Session, who: Principal) -> tuple[Workspace, ApiKey | None]:
    """The workspace row and the key row a metered request is judged against."""
    workspace = session.get(Workspace, who.workspace_id)
    if workspace is None:  # pragma: no cover - the principal came from a live key
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That credential's workspace no longer exists.",
        )
    return workspace, who.api_key


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


def _json_body(result: UpstreamResult) -> dict | None:
    """The engine's JSON object, when that is what it delivered, else ``None``.

    A non-JSON body (a subtitle file, a WAV) is not an error here: it simply is
    not a transcript, and the caller treats ``None`` the way it treats silence.
    """
    if not result.delivered:
        return None
    content_type = (result.media_type or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return None
    try:
        payload = json.loads(result.body or b"")
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _engine_refusal(result: UpstreamResult) -> dict:
    """The engine's own refusal, ready to hand back as an HTTP detail."""
    body = _json_body(result)
    if body:
        return body
    return {
        "detail": "The engine refused this request and did not say why in JSON.",
        "engine_status": result.status,
    }


def dub_dict(row: Dub) -> dict:
    """One dub as a caller reads it. Both engine calls' seconds stay separate."""
    return {
        "id": row.id,
        "voice": row.voice,
        "response_format": row.response_format,
        "source_bytes": row.source_bytes,
        "source_sha256": row.source_sha256,
        "transcript": row.transcript,
        "transcript_characters": row.transcript_characters,
        "spoken_characters": row.spoken_characters,
        "audio_seconds": row.audio_seconds,
        "audio_bytes": row.audio_bytes,
        "transcribe_seconds": row.transcribe_seconds,
        "speech_seconds": row.speech_seconds,
        "object_sha256": row.object_sha256,
        "audio_url": f"{DUB_ROUTE}/{row.id}/audio" if row.object_key else None,
        "created_at": row.created_at.isoformat(),
    }


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
            # The long-form limits, published for the same reason: the dashboard
            # refuses an over-long manuscript with the plane's own number instead
            # of a copy of it that drifts (``hosted/web/lib/projects.js``).
            "project_max_chars": projects.PROJECT_MAX_CHARS,
            "chapter_target_chars": projects.CHAPTER_TARGET_CHARS,
            "chapter_max_chars": projects.CHAPTER_MAX_CHARS,
            "chapter_max_count": projects.CHAPTER_MAX_COUNT,
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
            # Slice G: the operator's house defaults, which are "none" unless the
            # operator set them. A workspace can always change its own afterwards.
            character_ceiling=state.settings.default_character_ceiling,
            requests_per_minute=state.settings.default_requests_per_minute,
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

    # ── limits ────────────────────────────────────────────────────────────────
    @app.get("/v1/limits")
    def get_limits(request: Request, session: DbSession) -> dict:
        """This workspace's limits, and what is left of them.

        Readable with a key as well as a session on purpose: the thing a
        customer wires an alarm to is a key's remaining allowance, and a monitor
        should not have to hold a human's session token to see it.
        """
        who = principal(session, request, allow="any")
        workspace, _ = _limits_for(session, who)
        return {"limits": limits.state(session, workspace), "read_via": who.via}

    @app.put("/v1/limits")
    def set_limits(body: LimitsRequest, request: Request, session: DbSession) -> dict:
        """Set this workspace's ceiling and rate. Session only, deliberately.

        A key can read the limits and cannot change them: lowering a client's own
        ceiling is the move a runaway script would make, and the point of the
        ceiling is to survive the client's mistakes, not to be negotiated with one.
        """
        who = principal(session, request, allow="session")
        workspace, _ = _limits_for(session, who)
        sent = set(body.model_fields_set)
        if not sent:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Send at least one of character_ceiling or "
                    "requests_per_minute. null (or 0) clears a limit."
                ),
            )
        if "character_ceiling" in sent:
            workspace.character_ceiling = int(body.character_ceiling or 0)
        if "requests_per_minute" in sent:
            workspace.requests_per_minute = int(body.requests_per_minute or 0)
        session.commit()
        return {
            "limits": limits.state(session, workspace),
            "changed": sorted(sent),
            "note": (
                "A lower ceiling takes effect on the next request; characters "
                "already charged this period are not unspent by lowering it."
            ),
        }

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
        # Slice G: the characters are known before this route queues anything, so
        # the ceiling is decided here — a job that would break the month's limit
        # is refused while it is still one HTTP request, not later from the queue.
        workspace, _ = _limits_for(session, who)
        refusal = limits.guard(
            session, workspace=workspace, api_key=who.api_key, characters=len(text)
        )
        if refusal is not None:
            _refuse_limits(
                session, who=who, api_key=who.api_key, route=jobs.JOBS_ROUTE, refusal=refusal
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

    # ── projects: a manuscript, split and queued ─────────────────────────────
    @app.post("/v1/projects", status_code=status.HTTP_202_ACCEPTED)
    def create_project(
        body: ProjectRequest, request: Request, session: DbSession
    ) -> dict:
        """Split a manuscript and queue one job per chapter.

        The split happens here, in the request, and the chapters are ordinary jobs:
        there is no second queue, no second worker and no second meter. What this
        route owns is the decision of where the chapters end (``projects``), and
        the record of which job speaks which chapter.
        """
        who = principal(session, request, allow="any")
        text = (body.input or "").strip()
        if not text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'input' must be a non-empty string.",
            )
        if len(text) > projects.PROJECT_MAX_CHARS:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    f"'input' is {len(text)} characters and a project takes at most "
                    f"{projects.PROJECT_MAX_CHARS}. Send it as more than one project, "
                    "or shorten it."
                ),
            )
        try:
            chapters = projects.split_chapters(text)
        except projects.SplitError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        # Slice G: one reservation for the whole manuscript, taken after the split
        # because the split is what decides how many characters are really spoken
        # — the chapter texts, not the manuscript they came from. Reserving the
        # manuscript instead would strand the separator characters (two per
        # boundary) in the counter for the rest of the month, and a test in this
        # slice measured exactly that leak. Each chapter's job then settles its own
        # share as it finishes, so a half-finished project does not hold the
        # ceiling hostage for chapters that never ran.
        workspace, _ = _limits_for(session, who)
        speakable = sum(len(chapter) for chapter in chapters)
        refusal = limits.guard(
            session, workspace=workspace, api_key=who.api_key, characters=speakable
        )
        if refusal is not None:
            _refuse_limits(
                session, who=who, api_key=who.api_key, route="/v1/projects",
                refusal=refusal,
            )

        voice = (body.voice or "").strip()
        response_format = (body.response_format or "wav").strip().lower() or "wav"
        project = Project(
            workspace_id=who.workspace_id,
            title=(body.title or "").strip(),
            voice=voice,
            response_format=response_format,
            source_characters=len(text),
            chapter_count=len(chapters),
            chapter_max_chars=projects.CHAPTER_MAX_CHARS,
        )
        session.add(project)
        session.flush()
        rows: list[tuple[ProjectChapter, Job]] = []
        for index, chapter_text in enumerate(chapters):
            job = jobs.enqueue(
                session,
                workspace_id=who.workspace_id,
                api_key_id=_key_id(who),
                text=chapter_text,
                voice=voice,
                response_format=response_format,
                max_attempts=state.settings.job_max_attempts,
            )
            chapter = ProjectChapter(
                project_id=project.id,
                job_id=job.id,
                chapter_index=index,
                characters=len(chapter_text),
            )
            session.add(chapter)
            session.flush()
            rows.append((chapter, job))
        session.commit()
        return {
            "project": project_dict(project, rows),
            "queued_via": who.via,
            "note": (
                f"{len(chapters)} chapter job(s) queued; nothing has been spoken yet. "
                "A worker process speaks them in order, and "
                "GET /v1/projects/{id}/audio answers with one assembled file once "
                "every chapter is succeeded."
            ),
        }

    @app.get("/v1/projects")
    def list_projects(
        request: Request,
        session: DbSession,
        limit: int = Query(default=20, ge=1, le=50),
    ) -> dict:
        who = principal(session, request, allow="any")
        found = session.execute(
            select(Project)
            .where(Project.workspace_id == who.workspace_id)
            .order_by(Project.id.desc())
            .limit(limit)
        ).scalars().all()
        total = session.execute(
            select(func.count(Project.id)).where(
                Project.workspace_id == who.workspace_id
            )
        ).scalar_one()
        return {
            "projects": [
                project_dict(row, _chapter_rows(session, row.id)) for row in found
            ],
            "returned": len(found),
            "limit": limit,
            "workspace_total": total,
        }

    @app.get("/v1/projects/{project_id}")
    def get_project(project_id: int, request: Request, session: DbSession) -> dict:
        who = principal(session, request, allow="any")
        project = _own_project_row(session, project_id, who.workspace_id)
        return {"project": project_dict(project, _chapter_rows(session, project.id))}

    @app.delete("/v1/projects/{project_id}")
    def cancel_project(project_id: int, request: Request, session: DbSession) -> dict:
        """Cancel the chapters that have not been spoken yet, and nothing else."""
        who = principal(session, request, allow="any")
        project = _own_project_row(session, project_id, who.workspace_id)
        rows = _chapter_rows(session, project.id)
        cancelled = [job.id for _chapter, job in rows if jobs.cancel(session, job)]
        session.commit()
        return {
            "project": project_dict(project, rows),
            "cancelled_jobs": cancelled,
            "note": (
                "Only chapters still queued were cancelled. A chapter already being "
                "spoken is not interrupted, and one already stored is kept — the "
                "meter saw one request per chapter that was spoken, not per project."
            ),
        }

    @app.get("/v1/projects/{project_id}/audio")
    def project_audio(project_id: int, request: Request, session: DbSession) -> Response:
        """The whole audiobook, joined from the chapters the plane actually stored."""
        who = principal(session, request, allow="any")
        project = _own_project_row(session, project_id, who.workspace_id)
        rows = _chapter_rows(session, project.id)
        statuses = [job.status for _chapter, job in rows]
        waiting = [
            {
                "chapter": chapter.chapter_index + 1,
                "status": job.status,
                "error": job.error or "",
            }
            for chapter, job in rows
            if job.status != jobs.SUCCEEDED
        ]
        if not rows or waiting:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "project_status": project_status(statuses),
                    "detail": "This project has no assembled audio yet.",
                    "waiting": waiting,
                    "spoken_chapters": statuses.count(jobs.SUCCEEDED),
                    "chapter_count": len(rows),
                },
            )
        parts: list[tuple[str, bytes]] = []
        for chapter, job in rows:
            position = chapter.chapter_index + 1
            if not job.object_key:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "project_status": project_status(statuses),
                        "detail": (
                            f"Chapter {position} is recorded as spoken but has no "
                            "stored object, so the audiobook cannot be assembled."
                        ),
                        "waiting": [{"chapter": position, "status": job.status, "error": job.error or ""}],
                        "spoken_chapters": statuses.count(jobs.SUCCEEDED),
                        "chapter_count": len(rows),
                    },
                )
            try:
                # Read at request time, from the store: the file is the record of
                # what the chapter job delivered, and nothing is cached in memory.
                parts.append((f"chapter {position}", state.store.get(job.object_key)))
            except storage.StorageError as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Chapter {position}'s stored audio could not be read: {exc}",
                ) from exc
        try:
            assembled = projects.join_wavs(parts)
        except projects.CannotAssemble as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "project_status": project_status(statuses),
                    "detail": f"The chapters cannot be joined into one file: {exc}",
                    "spoken_chapters": len(parts),
                    "chapter_count": len(rows),
                },
            ) from exc
        joined = projects.wav_format(assembled)
        return Response(
            content=assembled,
            media_type="audio/wav",
            headers={
                "X-Qualiabits-Project": str(project.id),
                "X-Qualiabits-Project-Chapters": str(len(parts)),
                "X-Qualiabits-Audio-Seconds": f"{joined.seconds:.3f}",
                "X-Qualiabits-Object-Sha256": hashlib.sha256(assembled).hexdigest(),
                "Content-Disposition": f'attachment; filename="project-{project.id}.wav"',
            },
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

        workspace, _ = _limits_for(session, who)
        refusal = limits.guard(
            session, workspace=workspace, api_key=api_key, characters=len(text)
        )
        if refusal is not None:
            _refuse_limits(
                session, who=who, api_key=api_key, route=SPEECH_ROUTE, refusal=refusal
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
        # The reservation becomes real spend, in the same transaction as the
        # event that charges it, so the ceiling cannot double-count a request.
        limits.settle(session, workspace_id=who.workspace_id, characters=len(text))
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

        # Slice G, and the shape of the rule: a transcription asks the engine to
        # speak nothing, so it is bounded by the request rate and not by the
        # character ceiling. The check happens before the upload is parsed.
        workspace, _ = _limits_for(session, who)
        refusal = limits.guard(
            session, workspace=workspace, api_key=who.api_key, characters=0
        )
        if refusal is not None:
            _refuse_limits(
                session, who=who, api_key=who.api_key,
                route=TRANSCRIBE_ROUTE, refusal=refusal,
            )

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

    # ── dubbing: what was heard, said back in another voice ───────────────────
    @app.post(DUB_ROUTE, status_code=status.HTTP_201_CREATED)
    async def create_dub(request: Request, session: DbSession) -> dict:
        """A recording in, the engine's words out, and those words spoken back.

        Speaking needs a key here for the same reason it does on
        ``/v1/audio/speech``: this route produces audio, and every byte produced
        must be attributable. The order of the two engine calls is the feature.
        The voice is checked against the catalogue *before* the recording is read,
        so a voice the plane would never offer costs no transcription; the speech
        call happens only once there is a transcript to speak, so an empty or
        unreadable answer is refused rather than spoken. Each call is metered on
        its own route, so "speech in" and "speech out" stay separate counters, and
        the row keeps each call's own engine seconds apart.
        """
        who = principal(session, request, allow="api_key")
        api_key = who.require_api_key()
        ceiling = state.settings.transcribe_max_bytes
        allowance = f"An upload is at most {_window_of(ceiling)}."

        # Slice G: a dub is refused by the rate, not by the ceiling, for the same
        # reason a transcription is — until the recording has been heard nobody
        # knows how many characters will be spoken, and reserving a guess would
        # be inventing a number.
        workspace, _ = _limits_for(session, who)
        refusal = limits.guard(
            session, workspace=workspace, api_key=api_key, characters=0
        )
        if refusal is not None:
            _refuse_limits(
                session, who=who, api_key=api_key, route=DUB_ROUTE, refusal=refusal
            )

        declared = request.headers.get("content-length", "").strip()
        if declared.isdigit() and int(declared) > ceiling:
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=api_key.id,
                route=DUB_ROUTE, status_code=413, error="declared upload too large",
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
                session, workspace_id=who.workspace_id, api_key_id=api_key.id,
                route=DUB_ROUTE, status_code=400, error="unreadable multipart body",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Send the recording as multipart/form-data, with the audio in a "
                       "'file' part and the voice as a 'voice' field.",
            ) from None

        part = form.get("file")
        voice_raw = form.get("voice")
        voice = voice_raw.strip() if isinstance(voice_raw, str) else ""
        asked_format = form.get("response_format")
        asked_format = asked_format.strip().lower() if isinstance(asked_format, str) else ""

        if not isinstance(part, UploadFile):
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=api_key.id,
                route=DUB_ROUTE, status_code=400, error="no file part",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="There is no 'file' part in this request. The recording goes in "
                       "a 'file' part.",
            )
        if not voice:
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=api_key.id,
                route=DUB_ROUTE, status_code=400, error="missing voice",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'voice' must name one of the voices the plane offers. "
                       "GET /v1/voices lists them.",
            )
        if asked_format and asked_format != "wav":
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=api_key.id,
                route=DUB_ROUTE, status_code=400, error="unsupported format",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A dub is spoken as 'wav', not {asked_format!r}.",
            )

        # The voice first, before the recording is read: a recording is the
        # expensive half to hear, and a voice we would never offer is knowable
        # without it. A catalogue we cannot read does not refuse (see
        # ``dubbing.voice_is_known``); the engine answers for itself then.
        _, catalogue = await state.upstream.json_get("/v1/audio/voices")
        _, engines = await state.upstream.json_get("/engines/tts")
        offered = voice_catalogue.compose(catalogue, engines)
        if not dubbing.voice_is_known(voice, offered):
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=api_key.id,
                route=DUB_ROUTE, status_code=400, error="unknown voice",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"The engine does not offer a voice called {voice!r}. "
                       "GET /v1/voices lists the ones it does.",
            )

        content = await part.read()
        if len(content) > ceiling:
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=api_key.id,
                route=DUB_ROUTE, status_code=413, error="upload over the ceiling",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"That upload is {len(content)} bytes and the limit is "
                       f"{_window_of(ceiling)}. {allowance}",
            )
        if not content:
            metering.record(
                session, workspace_id=who.workspace_id, api_key_id=api_key.id,
                route=DUB_ROUTE, status_code=400, error="empty upload",
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The 'file' part was empty, so there is nothing to hear.",
            )

        fields: dict[str, str] = {}
        for name in TRANSCRIBE_FIELDS:
            value = form.get(name)
            if isinstance(value, str) and value.strip():
                fields[name] = value.strip()

        # 1. Hear it. Metered on the transcription route, because that is the
        #    work: what a customer handed us, counted in transcript characters.
        heard = await state.upstream.transcribe(
            filename=part.filename or "recording",
            content=content,
            content_type=part.content_type or "application/octet-stream",
            fields=fields,
        )
        metering.touch_key(session, api_key)
        heard_text = dubbing.transcript_text(_json_body(heard))
        metering.record(
            session,
            workspace_id=who.workspace_id,
            api_key_id=api_key.id,
            route=TRANSCRIBE_ROUTE,
            status_code=heard.status,
            transcript_characters=len(heard_text),
            upload=content,
            engine_seconds=heard.elapsed_seconds,
            error=heard.error,
        )
        session.commit()

        if not heard.delivered:
            if heard.status in (502, 504):
                raise HTTPException(
                    status_code=heard.status,
                    detail={"detail": "The transcription engine did not answer.",
                            "reason": heard.error},
                )
            raise HTTPException(
                status_code=heard.status, detail=_engine_refusal(heard)
            )

        try:
            spoken = dubbing.speakable_transcript(heard_text)
        except dubbing.NothingHeard as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        except dubbing.TooLongToSpeak as exc:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
            ) from exc

        # 2. Say it, in the chosen voice, through the same route a direct speech
        #    request uses, and metered on that route.
        said = await state.upstream.speech(
            {"input": spoken, "voice": voice, "response_format": "wav"}
        )
        metering.record(
            session,
            workspace_id=who.workspace_id,
            api_key_id=api_key.id,
            route=SPEECH_ROUTE,
            status_code=said.status,
            characters=len(spoken),
            body=said.body if said.delivered else None,
            engine_seconds=said.elapsed_seconds,
            error=said.error,
        )
        session.commit()

        if not said.delivered:
            if said.status in (502, 504):
                raise HTTPException(
                    status_code=said.status,
                    detail={"detail": "The speech engine did not answer.",
                            "reason": said.error},
                )
            raise HTTPException(status_code=said.status, detail=_engine_refusal(said))

        # 3. Keep it. The row is written only after the speech call delivered
        #    bytes, so a failed dub never leaves a row that reads like a success.
        body = said.body or b""
        stored = state.store.put(body, content_type=said.media_type or "audio/wav")
        row = Dub(
            workspace_id=who.workspace_id,
            api_key_id=api_key.id,
            voice=voice,
            response_format="wav",
            source_bytes=len(content),
            source_sha256=hashlib.sha256(content).hexdigest(),
            transcript=heard_text,
            transcript_characters=len(heard_text),
            spoken_characters=len(spoken),
            object_key=stored.key,
            object_sha256=stored.sha256,
            audio_seconds=metering.wav_seconds(body),
            audio_bytes=len(body),
            transcribe_seconds=heard.elapsed_seconds,
            speech_seconds=said.elapsed_seconds,
        )
        session.add(row)
        session.commit()
        return {
            "dub": dub_dict(row),
            "spoke_via": who.via,
            "note": (
                "The ear and the mouth are metered separately: this dub wrote one "
                f"transcription request of {len(heard_text)} characters and one speech "
                f"request of {len(spoken)}. The transcript is the engine's own, "
                "unedited — the same words, heard again, in another voice."
            ),
        }

    @app.get(DUB_ROUTE)
    def list_dubs(
        request: Request,
        session: DbSession,
        limit: int = Query(default=20, ge=1, le=50),
    ) -> dict:
        who = principal(session, request, allow="any")
        rows = session.execute(
            select(Dub)
            .where(Dub.workspace_id == who.workspace_id)
            .order_by(Dub.id.desc())
            .limit(limit)
        ).scalars().all()
        total = session.execute(
            select(func.count(Dub.id)).where(Dub.workspace_id == who.workspace_id)
        ).scalar_one()
        return {
            "dubs": [dub_dict(row) for row in rows],
            "returned": len(rows),
            "limit": limit,
            "workspace_total": total,
        }

    @app.get(DUB_ROUTE + "/{dub_id}")
    def get_dub(dub_id: int, request: Request, session: DbSession) -> dict:
        who = principal(session, request, allow="any")
        row = _own_dub_row(session, dub_id, who.workspace_id)
        return {"dub": dub_dict(row)}

    @app.get(DUB_ROUTE + "/{dub_id}/audio")
    def dub_audio(dub_id: int, request: Request, session: DbSession) -> Response:
        who = principal(session, request, allow="any")
        row = _own_dub_row(session, dub_id, who.workspace_id)
        if not row.object_key:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This dub has no stored audio.",
            )
        try:
            # Read from the object store at request time: the file is the record.
            body = state.store.get(row.object_key)
        except storage.StorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"The stored audio could not be read: {exc}",
            ) from exc
        headers = {
            "X-Qualiabits-Dub": str(row.id),
            "X-Qualiabits-Object-Sha256": row.object_sha256 or "",
        }
        if row.audio_seconds is not None:
            headers["X-Qualiabits-Audio-Seconds"] = str(row.audio_seconds)
        return Response(
            content=body,
            media_type=storage.content_type_for_key(row.object_key),
            headers=headers,
        )

    return app


app = create_app()