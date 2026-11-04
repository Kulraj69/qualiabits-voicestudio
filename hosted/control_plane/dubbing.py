"""Dubbing: what a recording said, said back in another voice.

The plane already does both halves of this on their own — transcription
(slice B) and metered speech (slice A) — so slice F adds no engine capability.
What it adds is the composition, and the order decisions that keep it honest:

- the voice is checked **before** the recording is transcribed, so a voice the
  plane would never offer costs no transcription;
- a recording the engine heard no words in is refused rather than spoken, so an
  empty dub is impossible;
- the transcript is never edited. What the dubbed file says is what the engine
  heard, mistakes included, which is why the page shows the transcript beside
  the player instead of hiding it behind one.

A dub is **not** a translation. There is no translation model in this repository
and none is called; "dub" here means the same words, heard again, in a different
voice. A page that implied otherwise would be the lie this module exists to make
hard to tell.
"""
from __future__ import annotations

from typing import Any

#: The longest transcript a dub will speak, in characters. Equal, on purpose, to
#: the ceiling the speech route enforces on its own ``input``
#: (``app.INPUT_MAX_CHARS``): a dub must never ask the engine to say more than a
#: direct speech request could. ``tests/hosted/test_hosted_dubbing.py`` reads both
#: numbers and fails if they drift apart, so this is a copy that cannot go stale
#: in silence.
SPEAK_MAX_CHARS = 5000


class DubError(Exception):
    """A refusal this module decided on, whose message is the caller's sentence."""


class NothingHeard(DubError):
    """The engine returned no words, so there is nothing to speak."""


class TooLongToSpeak(DubError):
    """The transcript is longer than ``SPEAK_MAX_CHARS``."""


def transcript_text(payload: Any) -> str:
    """The transcript out of the engine's STT payload, or ``""`` if there is none.

    Only the engine's own ``text`` field is read. A caller who asked for subtitles
    gets a file, not this shape, and an answer that is not JSON has no transcript
    to count — returning an empty string there is honest, and the route turns it
    into the same refusal as a silent recording rather than speaking a guess.
    """
    if isinstance(payload, dict):
        text = payload.get("text")
        if isinstance(text, str):
            return text
    return ""


def speakable_transcript(text: str) -> str:
    """The transcript exactly as it will be spoken: stripped, and nothing else.

    The only change is whitespace trimming, which cannot alter a word. Raises
    :class:`NothingHeard` when nothing was heard and :class:`TooLongToSpeak`
    when the words are past the ceiling, so the caller refuses instead of asking
    the engine to speak an empty string or a manuscript.
    """
    spoken = (text or "").strip()
    if not spoken:
        raise NothingHeard(
            "The engine heard no words in this recording, so there is nothing to "
            "say back. Try a longer clip, or a clearer one."
        )
    if len(spoken) > SPEAK_MAX_CHARS:
        raise TooLongToSpeak(
            f"The engine heard {len(spoken)} characters and a dub speaks at most "
            f"{SPEAK_MAX_CHARS}. Dub a shorter recording."
        )
    return spoken


def voice_is_known(voice: str, catalogue: dict[str, Any] | None) -> bool:
    """Is this voice one the plane would offer?

    The check exists only to save a transcription when the voice is clearly
    wrong. It must never be the thing that refuses a real voice, so a catalogue
    that could not be read, or lists nothing, answers ``True`` and lets the
    engine's own refusal be what the caller sees — the same refusal a direct
    speech request would get. A catalogue that does list voices is authoritative:
    a name outside it is refused here, before the recording is read.
    """
    if not isinstance(catalogue, dict):
        return True
    voices = catalogue.get("voices")
    if not isinstance(voices, list) or not voices:
        return True
    name = (voice or "").strip()
    return any(
        isinstance(entry, dict) and entry.get("id") == name for entry in voices
    )