"""Long-form work: a manuscript in, chapters out, one audiobook assembled.

The queue (``jobs``) speaks one paragraph per job and stores one WAV per job. A book
is not one paragraph, so this module is the two pieces that turn the queue into
long-form: a splitter that decides where one chapter ends, and an assembler that
joins the chapters the engine actually produced back into one file.

Both pieces are pure. No database, no HTTP, no engine, no clock: given the same
manuscript they produce the same chapters, and given the same chapter bytes they
produce the same file. That is what makes them testable without a browser or a
model, and it is why the interesting behaviour — where the split lands, and what
the assembler refuses — is decided here rather than inside a route handler.

Two rules this module keeps, because both are places a product like this usually
starts lying:

- **The split never happens mid-word.** A chapter boundary is a paragraph, then a
  sentence, then a whitespace gap, in that order of preference. If a single word is
  longer than a chapter's ceiling, the manuscript is refused with a sentence that
  says how long the word is, rather than a chapter that speaks half of it.
- **The assembler refuses formats it cannot join.** Concatenating 24 kHz mono with
  48 kHz stereo produces a file that every player will open and every listener will
  hear wrong: the second half plays at the wrong speed. There is no resampling in
  this repository yet, so the honest answer is a refusal naming both chapters.
"""
from __future__ import annotations

import io
import re
import wave
from bisect import bisect_right
from dataclasses import dataclass

#: What a chapter aims for before it is closed. Short enough that a chapter is a
#: couple of minutes of speech on CPU, so a reader sees progress instead of one
#: hour-long job.
CHAPTER_TARGET_CHARS = 1_500

#: The hard ceiling for one chapter. It must stay at or under ``jobs.MAX_JOB_CHARS``
#: or the splitter would produce chapters the queue then refuses; the guard in
#: ``tests/hosted/test_project_chapters.py`` reads both numbers and fails if that
#: stops being true.
CHAPTER_MAX_CHARS = 5_000

#: How many chapters one project may have. Two hours of speech is already a long
#: CPU run, and a project that spawns unbounded jobs is a queue nobody can drain.
CHAPTER_MAX_COUNT = 120

#: The manuscript ceiling, stated rather than discovered: the chapter target times
#: the chapter count. A manuscript of ordinary paragraphs at this length therefore
#: fits inside both limits. It is not a promise for every shape of text — the same
#: number of characters inside unusually long paragraphs can split into more
#: chapters than a project takes, and that is refused with the count rather than
#: queued.
PROJECT_MAX_CHARS = CHAPTER_TARGET_CHARS * CHAPTER_MAX_COUNT

TITLE_MAX_CHARS = 120

#: Paragraph boundaries: a blank line, which is also how the page's own textarea
#: placeholder suggests writing. The cut is placed at the end of the run, so the
#: next chapter starts on its first character rather than on whitespace.
_PARAGRAPH_CUT = re.compile(r"\n\s*\n+")

#: Sentence boundaries. The first alternative needs whitespace after an ASCII full
#: stop, which keeps a decimal like 1.5 in one piece. The danda (।) and the double
#: danda (॥) are Devanagari full stops and are cut after whether or not a space
#: follows, because Hindi manuscripts are written both ways.
_SENTENCE_CUT = re.compile(r"(?<=[.!?])\s+|(?<=[।॥])\s*")

#: The fallback tier: any whitespace run. A chapter is never cut inside a word, so
#: a sentence with no terminator in reach is cut at the last space before the
#: ceiling instead.
_WORD_CUT = re.compile(r"\s+")

#: Preferred first, then less preferred. A chapter is closed at the best boundary
#: the target allows.
_CUT_TIERS = (_PARAGRAPH_CUT, _SENTENCE_CUT, _WORD_CUT)


class SplitError(ValueError):
    """The manuscript cannot be split into chapters the queue would accept."""


class CannotAssemble(RuntimeError):
    """The chapter files cannot be joined, and the reason is in the sentence."""


@dataclass(frozen=True)
class WavFormat:
    """What a WAV file says about itself. Read from its own header, never assumed."""

    channels: int
    sample_width: int
    frame_rate: int
    frames: int

    @property
    def seconds(self) -> float:
        return self.frames / self.frame_rate if self.frame_rate else 0.0

    def as_dict(self) -> dict:
        return {
            "channels": self.channels,
            "sample_width": self.sample_width,
            "frame_rate": self.frame_rate,
            "frames": self.frames,
            "seconds": round(self.seconds, 6),
        }


def wav_format(data: bytes) -> WavFormat:
    """The format a WAV's own header declares.

    Raises ``CannotAssemble`` when the bytes are not a readable WAV at all, so a
    caller never has to guess whether a zero-length answer means silence or a
    failure.
    """
    if not data:
        raise CannotAssemble("the audio is empty (0 bytes)")
    if data[:4] != b"RIFF":
        raise CannotAssemble(
            f"the audio is not RIFF/WAVE; it starts with {data[:4]!r}"
        )
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            return WavFormat(
                channels=handle.getnchannels(),
                sample_width=handle.getsampwidth(),
                frame_rate=handle.getframerate(),
                frames=handle.getnframes(),
            )
    except (wave.Error, EOFError) as exc:
        raise CannotAssemble(f"the audio's header could not be read: {exc}") from exc


def cut_tiers(text: str) -> tuple[list[int], list[int], list[int]]:
    """Every place a chapter may end, one sorted list per kind of boundary.

    Paragraph ends first, then sentence ends, then the end of any whitespace run —
    the order a splitter should prefer them in. The manuscript's last position is
    always a cut, so the final chapter always has somewhere to stop.
    """
    tiers = [sorted({match.end() for match in pattern.finditer(text)}) for pattern in _CUT_TIERS]
    tiers[-1] = sorted(set(tiers[-1]) | {len(text)})
    return (tiers[0], tiers[1], tiers[2])


def _last_cut_at_most(positions: list[int], limit: int) -> int | None:
    """The last boundary at or before ``limit``, or ``None`` when there is none."""
    index = bisect_right(positions, limit) - 1
    return positions[index] if index >= 0 else None


def _next_cut(
    tiers: tuple[list[int], list[int], list[int]],
    body: str,
    start: int,
    *,
    target_chars: int,
    max_chars: int,
) -> int | None:
    """Where the chapter that starts at ``start`` should end.

    The whole remainder is one chapter when it fits inside the target: a chapter is
    closed because the text ran out, not because a boundary happened to be nearby.
    Otherwise the target decides first, and the ceiling applies only when no
    boundary at all falls inside the target — which is how a single over-long
    sentence is cut at a space rather than at the ceiling.
    """
    if len(body) - start <= target_chars:
        return len(body)
    for limit in (min(start + target_chars, len(body)), min(start + max_chars, len(body))):
        for tier in tiers:
            candidate = _last_cut_at_most(tier, limit)
            if candidate is not None and candidate > start:
                return candidate
    return None


def _word_length_at(text: str, start: int) -> int:
    """How long the word at ``start`` is, for a refusal sentence that names it."""
    match = re.match(r"\S+", text[start:])
    return len(match.group(0)) if match else 0


def split_chapters(
    text: str,
    *,
    target_chars: int = CHAPTER_TARGET_CHARS,
    max_chars: int = CHAPTER_MAX_CHARS,
    max_chapters: int = CHAPTER_MAX_COUNT,
) -> list[str]:
    """The manuscript, split into chapters.

    Each chapter ends at the best boundary the target allows: a paragraph end if
    one falls inside the target, otherwise a sentence end, otherwise the last space
    before it. No chapter is longer than ``max_chars``; a chapter is closed as soon
    as the next boundary would take it past ``target_chars``. The same manuscript
    always splits the same way, and a boundary is never placed inside a word — a
    word longer than the ceiling is a refusal, not a cut.
    """
    if target_chars <= 0 or max_chars <= 0 or max_chapters <= 0:
        raise SplitError("chapter limits must be positive")
    if target_chars > max_chars:
        raise SplitError(
            f"a chapter target of {target_chars} characters is above its ceiling "
            f"of {max_chars}, so no chapter could ever satisfy both"
        )
    if not text or not text.strip():
        raise SplitError("the manuscript is empty; there is nothing to split")

    body = text.strip()
    tiers = cut_tiers(body)
    chapters: list[str] = []
    start = 0
    while start < len(body):
        cut = _next_cut(
            tiers,
            body,
            start,
            target_chars=target_chars,
            max_chars=max_chars,
        )
        if cut is None:
            length = _word_length_at(body, start)
            raise SplitError(
                f"a single word is {length} characters long and this project's "
                f"chapter ceiling is {max_chars}; nothing can be spoken before "
                "that word, so the manuscript is refused instead of being cut "
                "inside it"
            )
        piece = body[start:cut].strip()
        if piece:
            chapters.append(piece)
        start = cut

    if len(chapters) > max_chapters:
        raise SplitError(
            f"this manuscript splits into {len(chapters)} chapters and a project "
            f"takes at most {max_chapters}; send it as more than one project"
        )
    return chapters


def join_wavs(parts: list[tuple[str, bytes]]) -> bytes:
    """Join chapter WAVs into one WAV, or refuse with the reason.

    Every chapter must agree on channels, sample width, frame rate and
    compression. The refusal names the chapter by its label and both formats, so a
    reader knows which chapter to re-synthesise instead of guessing.
    """
    if not parts:
        raise CannotAssemble("there are no chapter files to join")
    formats = [(label, wav_format(data)) for label, data in parts]
    first_label, first = formats[0]
    for label, fmt in formats[1:]:
        differences = []
        if fmt.channels != first.channels:
            differences.append(f"{fmt.channels} channel(s) against {first.channels}")
        if fmt.sample_width != first.sample_width:
            differences.append(
                f"{fmt.sample_width * 8}-bit samples against {first.sample_width * 8}-bit"
            )
        if fmt.frame_rate != first.frame_rate:
            differences.append(
                f"{fmt.frame_rate} Hz against {first.frame_rate} Hz"
            )
        if differences:
            raise CannotAssemble(
                f"{label} was recorded at {' and '.join(differences)} in "
                f"{first_label}; joining them would produce a file that plays the "
                "rest at the wrong speed, so this assembler refuses instead of "
                "resampling"
            )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(first.channels)
        out.setsampwidth(first.sample_width)
        out.setframerate(first.frame_rate)
        for _label, data in parts:
            with wave.open(io.BytesIO(data), "rb") as handle:
                out.writeframes(handle.readframes(handle.getnframes()))
    return buffer.getvalue()