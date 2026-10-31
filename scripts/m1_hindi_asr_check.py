"""Round-trip the probe's audio through an independent ASR.

The M1 probe can show that non-silent audio came back for Devanagari text, but
not that the audio contains the words it was asked to say. This transcribes the
returned WAV with faster-whisper (Whisper weights, MIT — the ASR is a different
model family from the TTS, so it is an independent check) and scores the token
overlap against the requested text.

What it is and is not: a machine check that the synthesized speech is
intelligible enough for an unrelated ASR to recover the words. It is NOT a human
listening check, not a pronunciation or accent judgement, and not a claim about
audio quality. Whisper's own errors count against the score, so the number is a
lower bound on intelligibility, not a measurement of it.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from pathlib import Path

WORD = re.compile(r"[\w\u0900-\u097F]+", re.UNICODE)


def tokens(text: str) -> list[str]:
    """Word tokens, case-folded; Devanagari survives the punctuation strip."""
    return [word.casefold() for word in WORD.findall(text)]


def score(expected: str, heard: str) -> dict:
    want, got = tokens(expected), tokens(heard)
    matcher = difflib.SequenceMatcher(a=want, b=got)
    recovered = sum(block.size for block in matcher.get_matching_blocks())
    return {
        "expected_tokens": len(want),
        "heard_tokens": len(got),
        "matched_tokens": recovered,
        "token_recall": round(recovered / len(want), 3) if want else None,
        "sequence_ratio": round(matcher.ratio(), 3),
        "heard": heard.strip(),
    }


def transcribe(wav: Path, model_size: str, language: str | None) -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(wav), language=language, beam_size=5)
    return " ".join(segment.text for segment in segments)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--language", default=None, help="ISO code hint, e.g. hi")
    parser.add_argument("--model", default="small")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    started = time.monotonic()
    heard = transcribe(args.wav, args.model, args.language)
    result = {
        "wav": args.wav.name,
        "asr": f"faster-whisper {args.model} (int8, CPU)",
        "language_hint": args.language,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        **score(args.expected, heard),
    }
    if args.json_out:
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())