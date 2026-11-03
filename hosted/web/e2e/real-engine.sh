#!/usr/bin/env bash
# The browser end-to-end against the real engine, in one command.
#
#   hosted/web/e2e/real-engine.sh [--no-build] [--keep]
#
# `npm run e2e` substitutes a stub engine so the browser suite needs no model.
# This script does not substitute anything: it resolves a Kokoro-format
# sherpa-onnx model (downloading and hash-checking it if this machine does not
# already have one), starts the fork's real backend on it, and drives the same
# suite at it. The run then proves from the engine's own answers that a model
# spoke — its identity from /health, the backend it reports active, its voice
# count, and the sha256 of the model bytes it was pointed at — and that the
# paragraph produced more audio than the one-liner, which a constant-size stub
# cannot pass. Both extra facts are written into e2e/.evidence/result.json.
#
# Model resolution order: QUALI_MODEL_DIR, hosted/web/<name>, the repository
# root, the repository's parent, /workspace/m1-models/<name>, then the download
# into $QUALI_E2E_WORK (default ${TMPDIR:-/tmp}/qb-real-engine-model). The
# download is the same release asset the hosted proof's script uses
# (hosted/scripts/hosted_demo.sh), and the same three sha256 values are pinned
# here: they are checked whatever directory the model came from, downloaded or
# already on the machine, and a mismatch stops the run rather than speaking with
# unpinned bytes. Nothing here falls back to
# the stub, because a run that quietly did would be the opposite of evidence.
#
# It also resolves a transcription model, because the suite now closes a loop the
# stub cannot: the sentence the speech engine says is uploaded back in and read by
# the engine's own speech-to-text route. That model is CTranslate2 Whisper small
# (Systran/faster-whisper-small), supplied by the operator the same way the speech
# model is — a directory — so the app's own licence gate has no catalogue entry to
# ask about and nothing is accepted on anybody's behalf. Resolution order:
# QUALI_ASR_MODEL_DIR, hosted/web/<name>, the repository root, the repository's
# parent, /workspace/m1-models/<name>, then the Hugging Face cache. If none has
# it, this script stops and says how to get it; it does not download half a
# gigabyte on its own, and it never falls back to a stub transcription.
#
# On CPU this takes several minutes: synthesis runs at roughly a seventh of real
# time, transcription is slower still, and the suite speaks a sentence, cancels a
# job, speaks a paragraph and transcribes the sentence. The speech download, when
# it happens, is about 132 MB.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../../.." && pwd)
WEB="$REPO/hosted/web"
MODEL_NAME=${QUALI_MODEL_NAME:-kokoro-int8-multi-lang-v1_0}
ASR_MODEL_NAME=${QUALI_ASR_MODEL_NAME:-faster-whisper-small}
#: The Whisper small CTranslate2 weights this machine already has, measured with
#: ``sha256sum`` on 2026-11-02. Pinned for the same reason the speech model is: a
#: run that used different bytes would be evidence about something else.
SHA_ASR_MODEL=3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671
TARBALL="$MODEL_NAME.tar.bz2"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/$TARBALL"
# The same three values hosted/scripts/hosted_demo.sh pins, from the M1 run.
SHA_MODEL=75635ea3b85a32d00094dcdd61a258aba87c160f7c264c304a7b3ae9d734195e
SHA_TOKENS=6ebb6bb288f20f3ae8d004d3c2ca27697da27c037d75e81a60e2a6a663f95425
SHA_VOICES=1c5a5b983d3d50d8586d437a51f3faa2da7919ce76a013c081e65671a3447c29
PY="$REPO/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "no venv at $PY. Build it once, then call this again:" >&2
  echo "    cd $REPO && uv sync --frozen --no-default-groups --group cpu --group dev --python python3.12" >&2
  exit 2
fi

#: The three files this script pins are checked whatever directory the model came
#: from. A model already on this machine is as capable of being the wrong bytes as
#: a downloaded one, and the pins are the whole reason the run is evidence about a
#: model rather than about "some Kokoro directory". The check used to live inside
#: the download branch, which quietly exempted every found model.
check_model_file() { # file expected
  actual=$(sha256sum "$1" | cut -d' ' -f1)
  if [ "$actual" != "$2" ]; then
    echo "HASH MISMATCH for $1" >&2
    echo "  expected $2" >&2
    echo "  actual   $actual" >&2
    echo "Stop: do not run a model whose bytes are not the ones this script pins." >&2
    exit 3
  fi
  echo "    ok $(basename "$1")"
}

MODEL=""
for candidate in "${QUALI_MODEL_DIR:-}" "$WEB/$MODEL_NAME" "$REPO/$MODEL_NAME" \
                 "$(dirname "$REPO")/$MODEL_NAME" "/workspace/m1-models/$MODEL_NAME"; do
  if [ -n "$candidate" ] && [ -d "$candidate" ] && [ -f "$candidate/tokens.txt" ] \
     && [ -f "$candidate/voices.bin" ]; then
    MODEL="$candidate"; break
  fi
done

MODEL_SOURCE=""
if [ -z "$MODEL" ]; then
  WORK=${QUALI_E2E_WORK:-${TMPDIR:-/tmp}/qb-real-engine-model}
  mkdir -p "$WORK"
  if [ ! -d "$WORK/$MODEL_NAME" ]; then
    echo "==> no $MODEL_NAME on this machine; downloading it (about 132 MB)"
    curl -fL --retry 3 -o "$WORK/$TARBALL" "$URL"
    echo "==> extracting"
    tar xjf "$WORK/$TARBALL" -C "$WORK" 2>/dev/null || \
      "$PY" -c "import tarfile,sys; tarfile.open(sys.argv[1],'r:bz2').extractall(sys.argv[2])" \
             "$WORK/$TARBALL" "$WORK"
  fi
  MODEL="$WORK/$MODEL_NAME"
  MODEL_SOURCE="downloaded by this script into $WORK"
fi

echo "==> verifying the model bytes against the pinned hashes"
check_model_file "$MODEL/model.int8.onnx" "$SHA_MODEL"
check_model_file "$MODEL/tokens.txt" "$SHA_TOKENS"
check_model_file "$MODEL/voices.bin" "$SHA_VOICES"

ASR=""
for candidate in "${QUALI_ASR_MODEL_DIR:-}" "$WEB/$ASR_MODEL_NAME" "$REPO/$ASR_MODEL_NAME" \
                 "$(dirname "$REPO")/$ASR_MODEL_NAME" "/workspace/m1-models/$ASR_MODEL_NAME"; do
  if [ -n "$candidate" ] && [ -f "$candidate/model.bin" ]; then
    ASR="$candidate"; break
  fi
done
if [ -z "$ASR" ]; then
  # The Hugging Face cache stores a snapshot as symlinks into blobs/, so the
  # snapshot directory is resolved rather than guessed — the path changes with
  # every revision downloaded.
  ASR=$("$PY" - <<'PYEOF' 2>/dev/null || true
import glob, os
for path in glob.glob(
    os.path.expanduser(
        "~/.cache/huggingface/hub/models--Systran--faster-whisper-small/snapshots/*"
    )
):
    if os.path.isfile(os.path.join(path, "model.bin")):
        print(path)
        break
PYEOF
)
fi
if [ -z "$ASR" ]; then
  echo "no Whisper small (CTranslate2) model found. One of these:" >&2
  echo "  set QUALI_ASR_MODEL_DIR=/path/to/faster-whisper-small, or" >&2
  echo "  put it at /workspace/m1-models/faster-whisper-small, or" >&2
  echo "  download it once with:" >&2
  echo "    \$PY -c \"from huggingface_hub import snapshot_download as d; \" \"print(d('Systran/faster-whisper-small'))\"" >&2
  echo "Stop: a run without a transcription model would not be the loop this proves." >&2
  exit 4
fi
ASR_ACTUAL=$(sha256sum "$ASR/model.bin" | cut -d' ' -f1)
if [ "$ASR_ACTUAL" != "$SHA_ASR_MODEL" ]; then
  echo "HASH MISMATCH for $ASR/model.bin" >&2
  echo "  expected $SHA_ASR_MODEL" >&2
  echo "  actual   $ASR_ACTUAL" >&2
  echo "Stop: do not run weights whose bytes are not the ones this script pins." >&2
  exit 3
fi
echo "==> engine speech-to-text: faster-whisper on $ASR (model.bin pinned)"

STATE=${QUALI_E2E_ENGINE_STATE:-${TMPDIR:-/tmp}/qb-real-engine-state}
mkdir -p "$STATE"

MODEL_SOURCE=${QUALI_MODEL_SOURCE:-${MODEL_SOURCE:-"found at $MODEL"}}
echo "==> engine: sherpa-onnx on $MODEL"
echo "==> CPU synthesis is slower than real time; this run takes minutes, not seconds."

cd "$WEB"
OMNIVOICE_TTS_BACKEND=sherpa-onnx \
OMNIVOICE_SHERPA_MODEL="$MODEL" \
OMNIVOICE_DATA_DIR="$STATE/data" \
OMNIVOICE_ENV_FILE="$STATE/user-env" \
OMNIVOICE_CACHE_DIR="$STATE/cache" \
OMNIVOICE_ASR_BACKEND=faster-whisper \
ASR_MODEL_FASTER="$ASR" \
HF_HUB_OFFLINE=1 \
QB_E2E_ENGINE_CMD="$PY -m uvicorn backend.main:app --host 127.0.0.1 --port {port}" \
QB_E2E_SPEAK_TIMEOUT_MS="${QB_E2E_SPEAK_TIMEOUT_MS:-300000}" \
QB_E2E_TRANSCRIBE_TIMEOUT_MS="${QB_E2E_TRANSCRIBE_TIMEOUT_MS:-900000}" \
QB_E2E_UPSTREAM_TIMEOUT_SECONDS="${QB_E2E_UPSTREAM_TIMEOUT_SECONDS:-900}" \
QB_E2E_SPEAK_TEXT="${QB_E2E_SPEAK_TEXT:-यह एक हिन्दी वाक्य है।}" \
QB_E2E_SPEAK_LANGUAGE="${QB_E2E_SPEAK_LANGUAGE:-hi}" \
QB_E2E_TRANSCRIBE_LANGUAGE="${QB_E2E_TRANSCRIBE_LANGUAGE:-hi}" \
QB_E2E_ENGINE_LABEL="sherpa-onnx on $MODEL_NAME (QB_E2E_ENGINE_CMD)" \
QB_E2E_MODEL_SOURCE="$MODEL_SOURCE" \
QB_E2E_ASR_MODEL="$ASR" \
QB_E2E_ASR_MODEL_SOURCE="${QUALI_ASR_SOURCE:-found at $ASR}" \
QB_E2E_ASR_MODEL_PINNED="$SHA_ASR_MODEL" \
  node e2e/dashboard.mjs "$@"