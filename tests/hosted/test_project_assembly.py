"""Joining chapter WAVs into one audiobook.

The assembler is small, and the interesting half of it is what it refuses. A join
that concatenates frames without looking at the headers produces a file every player
opens and every listener hears wrong — the second half plays at the wrong speed — so
the mismatch cases below are the point of this file, not an afterthought.

Every file here is written by the stdlib ``wave`` module and read back with it: no
hand-rolled RIFF bytes, so the durations the code reads out of the headers are
durations a real encoder wrote.
"""
from __future__ import annotations

import io
import struct
import wave

import pytest

from hosted.control_plane.projects import CannotAssemble, join_wavs, wav_format


def tone(seconds: float, *, sample_rate: int = 24000, channels: int = 1, width: int = 2, value: int = 1200) -> bytes:
    """A real WAV of exactly ``seconds``, in the format asked for."""
    frames = int(round(seconds * sample_rate))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(sample_rate)
        handle.writeframes(struct.pack("<h", value) * frames * channels)
    return buffer.getvalue()


def test_a_single_chapter_is_returned_as_a_readable_wav():
    one = tone(1.5)
    joined = join_wavs([("chapter 1", one)])
    assert wav_format(joined).frames == wav_format(one).frames
    assert wav_format(joined).seconds == pytest.approx(1.5, abs=1e-6)


def test_the_joined_file_is_as_long_as_the_chapters_added_up():
    parts = [("chapter 1", tone(1.0)), ("chapter 2", tone(2.5)), ("chapter 3", tone(0.5))]
    joined = join_wavs(parts)
    fmt = wav_format(joined)
    assert fmt.seconds == pytest.approx(4.0, abs=1e-6)
    assert fmt.frames == sum(wav_format(data).frames for _label, data in parts)
    assert fmt.channels == 1 and fmt.sample_width == 2 and fmt.frame_rate == 24000


def test_the_joined_file_can_be_read_back_by_the_standard_library():
    """Not just our own parser: ``wave`` decodes it and sees every frame."""
    first, second = tone(1.0), tone(1.0, value=3000)
    joined = join_wavs([("chapter 1", first), ("chapter 2", second)])
    with wave.open(io.BytesIO(joined), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    assert len(frames) == len(struct.pack("<h", 1) * 24000 * 2)
    # The audio is the two chapters in order, not one of them twice.
    assert frames[:2] == struct.pack("<h", 1200)
    assert frames[24000 * 2:24000 * 2 + 2] == struct.pack("<h", 3000)


def test_the_same_chapters_always_join_to_the_same_bytes():
    parts = [("chapter 1", tone(0.25)), ("chapter 2", tone(0.25))]
    assert join_wavs(parts) == join_wavs(parts)


def test_chapters_at_different_sample_rates_are_refused_with_both_rates_named():
    parts = [("chapter 1", tone(0.5, sample_rate=24000)), ("chapter 2", tone(0.5, sample_rate=48000))]
    with pytest.raises(CannotAssemble) as error:
        join_wavs(parts)
    message = str(error.value)
    assert "chapter 2" in message and "chapter 1" in message
    assert "48000 Hz" in message and "24000 Hz" in message
    assert "resampling" in message


def test_chapters_with_different_channel_counts_are_refused():
    parts = [("chapter 1", tone(0.5, channels=1)), ("chapter 2", tone(0.5, channels=2))]
    with pytest.raises(CannotAssemble) as error:
        join_wavs(parts)
    assert "channel" in str(error.value)


def test_chapters_with_different_sample_widths_are_refused():
    parts = [("chapter 1", tone(0.5, width=2)), ("chapter 2", tone(0.5, width=1))]
    with pytest.raises(CannotAssemble) as error:
        join_wavs(parts)
    assert "16-bit" in str(error.value) and "8-bit" in str(error.value)


def test_bytes_that_are_not_a_wav_are_refused_and_say_what_they_start_with():
    with pytest.raises(CannotAssemble) as error:
        join_wavs([("chapter 1", b"MP3\x00not really")])
    assert "MP3" in str(error.value)


def test_a_truncated_header_is_refused_rather_than_guessed_at():
    with pytest.raises(CannotAssemble) as error:
        join_wavs([("chapter 1", tone(0.5)[:20])])
    assert "header" in str(error.value)


def test_empty_bytes_are_refused_with_their_length():
    with pytest.raises(CannotAssemble) as error:
        join_wavs([("chapter 1", b"")])
    assert "0 bytes" in str(error.value)


def test_nothing_to_join_is_refused():
    with pytest.raises(CannotAssemble) as error:
        join_wavs([])
    assert "no chapter files" in str(error.value)


def test_wav_format_reports_what_the_header_says():
    fmt = wav_format(tone(2.0, sample_rate=8000, channels=2, width=2))
    assert fmt.channels == 2 and fmt.sample_width == 2 and fmt.frame_rate == 8000
    assert fmt.frames == 16000 and fmt.seconds == pytest.approx(2.0)
    assert fmt.as_dict()["seconds"] == 2.0