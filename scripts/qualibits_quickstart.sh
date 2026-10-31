#!/usr/bin/env bash
# One command to see real Hindi and English speech out of this fork's API.
#
#   scripts/qualibits_quickstart.sh [work-directory]      # default: .quickstart
#
# It downloads the Kokoro bundle the product page names, verifies three file
# hashes against values pinned here (so a corrupted or swapped download stops
# before anything runs), then boots the real backend and posts Hindi from two
# Hindi voices plus English. The output directory keeps the evidence.
#
# What a pass means: real WAV audio came back over the real HTTP API on CPU.
# What it does not mean: no GPU, no listening check, no hosted service, not M1.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=${1:-"$REPO/.quickstart"}
MODEL="$WORK/kokoro-int8-multi-lang-v1_0"
EVIDENCE="$WORK/evidence"
TARBALL="kokoro-int8-multi-lang-v1_0.tar.bz2"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/$TARBALL"

# From hermes-reports/M1-slot23-speech-r2/result.json; re-checked 31 Oct 2026.
SHA_MODEL=75635ea3b85a32d00094dcdd61a258aba87c160f7c264c304a7b3ae9d734195e
SHA_TOKENS=6ebb6bb288f20f3ae8d004d3c2ca27697da27c037d75e81a60e2a6a663f95425
SHA_VOICES=1c5a5b983d3d50d8586d437a51f3faa2da7919ce76a013c081e65671a3447c29

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "no .venv here. Run the uv sync step from docs/QUALIABITS-VOICE.md first." >&2; exit 2; }

if [ ! -d "$MODEL" ]; then
  mkdir -p "$WORK"
  if [ ! -f "$WORK/$TARBALL" ]; then
    echo "==> downloading $TARBALL (about 132 MB)"
    curl -fL --retry 3 -o "$WORK/$TARBALL" "$URL"
  fi
  echo "==> extracting"
  tar xjf "$WORK/$TARBALL" -C "$WORK" 2>/dev/null || \
    "$PY" -c "import tarfile,sys; tarfile.open(sys.argv[1],'r:bz2').extractall(sys.argv[2])" "$WORK/$TARBALL" "$WORK"
fi

echo "==> verifying the model against pinned hashes"
check() { # file expected
  actual=$(sha256sum "$1" | cut -d' ' -f1)
  if [ "$actual" != "$2" ]; then
    echo "HASH MISMATCH for $1" >&2
    echo "  expected $2" >&2
    echo "  actual   $actual" >&2
    echo "Stop: do not run a model whose bytes are not the ones this script pins." >&2
    exit 3
  fi
  echo "    ok $1"
}
check "$MODEL/model.int8.onnx" "$SHA_MODEL"
check "$MODEL/tokens.txt" "$SHA_TOKENS"
check "$MODEL/voices.bin" "$SHA_VOICES"

# The backend refuses to start with inherited configuration; an empty .env
# sentinel keeps upstream dotenv discovery out of the process.
if [ ! -f "$REPO/.env" ]; then
  echo "==> writing the empty .env sentinel"
  python3 -c "from pathlib import Path; Path('$REPO/.env').open('x').close()"
fi

echo "==> running the probe (takes two to four minutes on a small CPU box)"
"$PY" "$REPO/scripts/m1_hindi_probe.py" --output "$EVIDENCE" --model-dir "$MODEL"

"$PY" - "$EVIDENCE/result.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1]))
passed = result["checks"]          # only the checks that passed are listed
for name in passed:
    print(f"  PASS  {name}")
if "failure" in result:
    print(f"  FAIL  {result.get('failure_type')}: {result['failure']}")
for line in result["requests"]:
    audio = line.get("audio") or {}
    print(f"  {line['label']:<20} {line['voice']:<10} HTTP {line['http_status']} "
          f"{audio.get('duration_seconds','')} s at {audio.get('sample_rate','')} Hz")
print()
print(f"audio in {sys.argv[1].rsplit('/', 1)[0]}/audio")
print("not a listening check, not GPU evidence, not M1 acceptance.")
sys.exit(1 if "failure" in result else 0)
PY