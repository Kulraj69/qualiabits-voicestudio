"""Control-plane settings, read from ``QUALI_*`` environment variables.

Everything has a local default so the plane can be started with no configuration
at all. Defaults are for a single-box local run, not for production: the default
database is a SQLite file beside the state directory, and the default upstream is
the engine on loopback.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Bumped whenever the control plane's schema changes in a way that needs a
#: migration. Recorded in the database itself so a running plane can say which
#: schema it created.
SCHEMA_VERSION = "m2a1"

DEFAULT_UPSTREAM = "http://127.0.0.1:3900"
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    """Where the plane keeps its data and which engine it wraps."""

    database_url: str
    upstream_base_url: str
    upstream_timeout_seconds: float = 300.0
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_env(cls) -> "Settings":
        default_db = Path(
            os.environ.get("QUALI_STATE_DIR", "") or Path.home() / ".qualiabits-voice"
        ) / "control-plane.db"
        return cls(
            database_url=os.environ.get("QUALI_DATABASE_URL", "").strip()
            or f"sqlite:///{default_db}",
            upstream_base_url=(
                os.environ.get("QUALI_UPSTREAM_BASE_URL", "").strip() or DEFAULT_UPSTREAM
            ).rstrip("/"),
            upstream_timeout_seconds=_env_float("QUALI_UPSTREAM_TIMEOUT_SECONDS", 300.0),
            session_ttl_seconds=_env_int(
                "QUALI_SESSION_TTL_SECONDS", DEFAULT_SESSION_TTL_SECONDS
            ),
        )