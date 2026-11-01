"""What voices the engine offers, in the shape a dashboard needs.

The engine answers two different questions and neither one is the whole answer:

- ``GET /v1/audio/voices`` lists OpenAI aliases and saved voice profiles. For a
  Kokoro bundle it lists none of the 54 voices the model can actually speak.
- ``GET /engines/tts`` names the active engine and, for a model whose voice
  table lives in the file (``id2speaker``), the ids that model declares.

This module composes the two into one list, engine voices first, and says which
kind each voice is, so a picker offers what the service can really speak and
labels the aliases as aliases. It does not invent anything: a voice appears here
because the engine reported it, and the default marker is the engine's own
default, never a guess.
"""
from __future__ import annotations

from typing import Any


def compose(
    catalogue: dict[str, Any] | None, engines: dict[str, Any] | None
) -> dict[str, Any]:
    """One voice list from the engine's catalogue and its engine inventory."""
    voices: list[dict[str, Any]] = []
    seen: set[str] = set()

    active = ""
    display_name = ""
    default_voice = ""
    reachable = catalogue is not None and engines is not None

    if engines:
        active = str(engines.get("active") or "")
        for entry in engines.get("backends") or []:
            if not isinstance(entry, dict) or entry.get("id") != active:
                continue
            display_name = str(entry.get("display_name") or active)
            default_voice = str(entry.get("default_voice") or "")
            for name in entry.get("preset_voices") or []:
                if not isinstance(name, str) or not name or name in seen:
                    continue
                seen.add(name)
                voices.append({
                    "id": name,
                    "name": name,
                    "kind": "engine",
                    "language": None,
                    "default": name == default_voice,
                })

    for entry in (catalogue or {}).get("voices") or []:
        if not isinstance(entry, dict):
            continue
        identifier = str(entry.get("voice_id") or entry.get("id") or "")
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        kind = "profile" if entry.get("type") == "profile" else "alias"
        voices.append({
            "id": identifier,
            "name": str(entry.get("name") or identifier),
            "kind": kind,
            "language": entry.get("language") or None,
            "default": False,
        })

    counts = {"engine": 0, "alias": 0, "profile": 0}
    for voice in voices:
        counts[voice["kind"]] += 1

    return {
        "engine": {
            "id": active or None,
            "display_name": display_name or None,
            "default_voice": default_voice or None,
            "reachable": reachable,
        },
        "voices": voices,
        "count": len(voices),
        "kinds": counts,
    }