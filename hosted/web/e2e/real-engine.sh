#!/usr/bin/env bash
# The browser end-to-end against the real engine, in one command.
#
#   hosted/web/e2e/real-engine.sh [--no-build] [--keep]
#
# `npm run e2e` substitutes a stub engine so the browser suite needs no model.
# This script does not substitute anything: it finds a Kokoro-format sherpa-onnx
# model on this machine, starts the fork's real backend on it, and drives the
# same suite at it. The run then proves from the engine's own answers that a
# model spoke — its identity from /health, the backend it reports active, its
# voice count, and the sha256 of the model bytes it was pointed at — and that the
# paragraph produced more audio than the one-liner, which a constant-size stub
# cannot pass. Both extra facts are written into e2e/.evidence/result.json.
#
# Model resolution order: QUALI_MODEL_DIR, hosted/web/<name>, the repository
# root, the repository's parent, /workspace/m1-models/<name>. Nothing is
# downloaded here; if no model is present the script says so and stops, because
# a run that quietly fell back to the stub would be the opposite of evidence.
#
# On CPU this takes several minutes: synthesis runs at roughly a seventh of real
# time and the suite speaks a sentence, cancels a job and speaks a paragraph.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../../.." && pwd)
WEB="$REPO/hosted/web"
MODEL_NAME=${QUALI_MODEL_NAME:-kokoro-int8-multi-lang-v1_0}
PY="$REPO/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "no venv at $PY. Build it once, then call this again:" >&2
  echo "    cd $REPO && uv sync --frozen --no-default-groups --group cpu --group dev --python python3.12" >&2
  exit 2
fi

MODEL=""
for candidate in "${QUALI_MODEL_DIR:-}" "$WEB/$MODEL_NAME" "$REPO/$MODEL_NAME" \
                 "$(dirname "$REPO")/$MODEL_NAME" "/workspace/m1-models/$MODEL_NAME"; do
  if [ -n "$candidate" ] && [ -d "$candidate" ] && [ -f "$candidate/tokens.txt" ] \
     && [ -f "$candidate/voices.bin" ]; then
    MODEL="$candidate"; break
  fi
done
if [ -z "$MODEL" ]; then
  echo "no $MODEL_NAME found. Unpack it and point QUALI_MODEL_DIR at the directory:" >&2
  echo "    QUALI_MODEL_DIR=/path/to/$MODEL_NAME $0" >&2
  exit 3
fi

STATE=${QUALI_E2E_ENGINE_STATE:-${TMPDIR:-/tmp}/qb-real-engine-state}
mkdir -p "$STATE"

echo "==> engine: sherpa-onnx on $MODEL"
echo "==> CPU synthesis is slower than real time; this run takes minutes, not seconds."

cd "$WEB"
OMNIVOICE_TTS_BACKEND=sherpa-onnx \
OMNIVOICE_SHERPA_MODEL="$MODEL" \
OMNIVOICE_DATA_DIR="$STATE/data" \
OMNIVOICE_ENV_FILE="$STATE/user-env" \
OMNIVOICE_CACHE_DIR="$STATE/cache" \
HF_HUB_OFFLINE=1 \
QB_E2E_ENGINE_CMD="$PY -m uvicorn backend.main:app --host 127.0.0.1 --port {port}" \
QB_E2E_SPEAK_TIMEOUT_MS="${QB_E2E_SPEAK_TIMEOUT_MS:-300000}" \
QB_E2E_UPSTREAM_TIMEOUT_SECONDS="${QB_E2E_UPSTREAM_TIMEOUT_SECONDS:-300}" \
  node e2e/dashboard.mjs "$@"