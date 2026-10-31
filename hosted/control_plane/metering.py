"""Per-workspace usage metering.

Two counters that mean different things, kept apart on purpose:

- ``characters`` — the text this workspace asked the engine to speak. Counted
  once a request passes input validation, whether or not the engine then answered.
- ``audio_seconds`` — audio the customer actually received, computed from the
  returned WAV's own header. A request that failed adds a failure, not seconds.

Anything billed later can pick either, and the summary says which is which so a
customer reading it is not misled by a single blended number.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import ApiKey, UsageEvent, utcnow


def wav_seconds(body: bytes) -> float | None:
    """Duration of a RIFF/WAVE payload, from its own header.

    ``None`` when the bytes are not a readable WAV — an MP3 is not a WAV, and
    guessing a bitrate to invent a duration would be a made-up number. The
    caller stores ``None`` and the summary reports the request with no audio
    seconds instead.
    """
    if len(body) < 44 or body[0:4] != b"RIFF" or body[8:12] != b"WAVE":
        return None
    offset = 12
    byte_rate = None
    data_size = None
    while offset + 8 <= len(body):
        chunk_id = body[offset:offset + 4]
        (chunk_size,) = struct.unpack_from("<I", body, offset + 4)
        payload = offset + 8
        if chunk_id == b"fmt " and chunk_size >= 16 and payload + 16 <= len(body):
            (byte_rate,) = struct.unpack_from("<I", body, payload + 8)
        elif chunk_id == b"data":
            data_size = min(chunk_size, max(0, len(body) - payload))
            break
        offset = payload + chunk_size + (chunk_size % 2)
    if not byte_rate or data_size is None:
        return None
    return round(data_size / byte_rate, 6)


@dataclass
class UsageSummary:
    workspace_id: int
    requests: int = 0
    characters: int = 0
    audio_seconds: float = 0.0
    audio_bytes: int = 0
    failed_requests: int = 0
    requests_without_audio_seconds: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_key: list[dict] = field(default_factory=list)
    first_request_at: datetime | None = None
    last_request_at: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "requests": self.requests,
            "characters": self.characters,
            "audio_seconds": round(self.audio_seconds, 3),
            "audio_bytes": self.audio_bytes,
            "failed_requests": self.failed_requests,
            "requests_without_audio_seconds": self.requests_without_audio_seconds,
            "by_status": dict(sorted(self.by_status.items())),
            "by_key": self.by_key,
            "first_request_at": _iso(self.first_request_at),
            "last_request_at": _iso(self.last_request_at),
            "characters_note": "text sent to the engine, counted whether or not it answered",
            "audio_seconds_note": "audio received, measured from the returned WAV header",
            "by_key_note": (
                "spend per key, so a customer can tell which integration is talking. "
                "Input the engine never saw appears here too, with zero characters."
            ),
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def record(
    session: Session,
    *,
    workspace_id: int,
    api_key_id: int | None,
    route: str,
    status_code: int,
    characters: int = 0,
    body: bytes | None = None,
    engine_seconds: float | None = None,
    error: str = "",
) -> UsageEvent:
    """Write one metered request. Caller commits."""
    seconds = wav_seconds(body) if body else None
    event = UsageEvent(
        workspace_id=workspace_id,
        api_key_id=api_key_id,
        route=route,
        status_code=status_code,
        characters=characters,
        audio_seconds=seconds,
        audio_bytes=len(body) if body else None,
        engine_seconds=engine_seconds,
        error=error[:200],
        created_at=utcnow(),
    )
    session.add(event)
    session.flush()
    return event


def summary(session: Session, workspace_id: int) -> UsageSummary:
    """Aggregate one workspace's ledger. Never crosses workspaces.

    Two views of the same rows: the workspace total, and a per-key breakdown, so
    a customer with a laptop and a server can see which one is spending.
    """
    rows = session.execute(
        select(UsageEvent).where(UsageEvent.workspace_id == workspace_id)
    ).scalars().all()
    out = UsageSummary(workspace_id=workspace_id)
    keys: dict[int, dict] = {}
    for row in rows:
        out.requests += 1
        out.characters += row.characters or 0
        out.audio_bytes += row.audio_bytes or 0
        if row.audio_seconds is not None:
            out.audio_seconds += row.audio_seconds
        else:
            out.requests_without_audio_seconds += 1
        if row.status_code >= 400:
            out.failed_requests += 1
        key = str(row.status_code)
        out.by_status[key] = out.by_status.get(key, 0) + 1
        if out.first_request_at is None or row.created_at < out.first_request_at:
            out.first_request_at = row.created_at
        if out.last_request_at is None or row.created_at > out.last_request_at:
            out.last_request_at = row.created_at
        if row.api_key_id is not None:
            bucket = keys.setdefault(row.api_key_id, {
                "api_key_id": row.api_key_id, "key_id": None, "label": "",
                "requests": 0, "characters": 0, "audio_seconds": 0.0,
                "failed_requests": 0, "last_request_at": None,
            })
            bucket["requests"] += 1
            bucket["characters"] += row.characters or 0
            bucket["audio_seconds"] += row.audio_seconds or 0.0
            if row.status_code >= 400:
                bucket["failed_requests"] += 1
            if bucket["last_request_at"] is None or row.created_at > bucket["last_request_at"]:
                bucket["last_request_at"] = row.created_at
    for api_key_id, bucket in keys.items():
        record = session.get(ApiKey, api_key_id)
        if record is not None and record.workspace_id == workspace_id:
            # The public key id only. The digest and the key itself never leave
            # the database.
            bucket["key_id"] = record.key_id
            bucket["label"] = record.label
        bucket["audio_seconds"] = round(bucket["audio_seconds"], 3)
        bucket["last_request_at"] = _iso(bucket["last_request_at"])
    out.by_key = sorted(keys.values(), key=lambda item: item["api_key_id"])
    return out


def touch_key(session: Session, api_key: ApiKey) -> None:
    api_key.last_used_at = utcnow()
    session.flush()