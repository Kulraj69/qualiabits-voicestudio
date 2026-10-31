#!/usr/bin/env bash
# One command to see the hosted layer work: real engine, real signup, real audio.
#
#   hosted/scripts/hosted_demo.sh [work-directory]
#
# It finds the Kokoro bundle (a directory you already have, or it downloads and
# hash-checks it), then runs hosted_e2e.py — which starts BOTH the engine and the
# control plane itself, one reserved loopback socket each, and stops them when it
# is done. You do not start anything by hand and you do not need to stop anything.
#
# What a pass means: two workspaces signed up, keys minted, Hindi and English
# WAVs came back through the control plane from the real engine, a longer Hindi
# job was queued and spoken by a separate worker process and fetched back out of
# the object store, the meter matched the audio and stayed inside its workspace,
# and every process it started shut down cleanly. What it does not mean: nothing
# is deployed, there is no GPU, no S3 bucket, no human has listened to the audio,
# and M1 stays open.
#
# The fast path, if you only want to see the tests: .venv/bin/python -m pytest tests/hosted -q
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${1:-"$REPO/.hosted-demo"}
MODEL_NAME=kokoro-int8-multi-lang-v1_0
TARBALL="$MODEL_NAME.tar.bz2"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/$TARBALL"
# The same three values scripts/qualibits_quickstart.sh pins, from the M1 run.
SHA_MODEL=75635ea3b85a32d00094dcdd61a258aba87c160f7c264c304a7b3ae9d734195e
SHA_TOKENS=6ebb6bb288f20f3ae8d004d3c2ca27697da27c037d75e81a60e2a6a663f95425
SHA_VOICES=1c5a5b983d3d50d8586d437a51f3faa2da7919ce76a013c081e65671a3447c29

PY="$REPO/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "no venv in $REPO yet. Build it once, then call this again:" >&2
  echo "    cd $REPO && uv sync --frozen --no-default-groups --group cpu --group dev --python python3.12" >&2
  exit 2
fi
mkdir -p "$WORK"

# Model resolution order: an explicit QUALI_MODEL_DIR, a directory in the work
# area, a sibling of this checkout, then the download.
MODEL=""
for candidate in "${QUALI_MODEL_DIR:-}" "$WORK/$MODEL_NAME" "$REPO/$MODEL_NAME" \
                 "$(dirname "$REPO")/$MODEL_NAME" /workspace/m1-models/"$MODEL_NAME"; do
  if [ -n "$candidate" ] && [ -d "$candidate" ] && [ -f "$candidate/tokens.txt" ]; then
    MODEL="$candidate"; break
  fi
done

if [ -z "$MODEL" ]; then
  mkdir -p "$WORK"
  if [ ! -f "$WORK/$TARBALL" ]; then
    echo "==> downloading $TARBALL (about 132 MB)"
    curl -fL --retry 3 -o "$WORK/$TARBALL" "$URL"
  fi
  echo "==> extracting"
  tar xjf "$WORK/$TARBALL" -C "$WORK" 2>/dev/null || \
    "$PY" -c "import tarfile,sys; tarfile.open(sys.argv[1],'r:bz2').extractall(sys.argv[2])" "$WORK/$TARBALL" "$WORK"
  MODEL="$WORK/$MODEL_NAME"
fi

echo "==> verifying the model bytes against the pinned hashes"
check() { # file expected
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
check "$MODEL/model.int8.onnx" "$SHA_MODEL"
check "$MODEL/tokens.txt" "$SHA_TOKENS"
check "$MODEL/voices.bin" "$SHA_VOICES"

OUT="$WORK/evidence-$(date +%Y%m%d-%H%M%S)"
LOG="$WORK/hosted_e2e.log"
echo "==> running the end-to-end proof (several minutes on a small CPU box; the"
echo "    queued job is the slow part — this engine spends seconds of CPU per"
echo "    second of speech)"
echo "    evidence goes to $OUT"
# The harness prints its own JSON summary; keep it in the evidence directory so
# this output stays readable, and let the block below say what happened.
"$PY" "$REPO/hosted/scripts/hosted_e2e.py" --output "$OUT" --model "$MODEL" \
  > "$LOG" 2>&1 || true

"$PY" - "$OUT/result.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1]))
for name in result.get("checks", []):
    print(f"  PASS  {name}")
if "failure" in result:
    print(f"  FAIL  {result.get('failure_type')}: {result['failure']}")
for row in result.get("speech", []):
    print(f"  {row['label']:<16} {row['voice']:<9} {row['language']}  "
          f"{row['seconds']} s at {row['sample_rate']} Hz, peak {row['peak']}, "
          f"engine took {row['engine_seconds_header']} s")
job = result.get("job")
if job:
    stats = job.get("worker_stats", {})
    print(f"  queued job {job['id']:<6} {job['characters']} characters, {job['seconds']} s "
          f"at 24 kHz, sha256 {job['sha256'][:12]}…, engine took {job['engine_seconds']} s, "
          f"worker claimed/succeeded {stats.get('claimed')}/{stats.get('succeeded')}")
refused = result.get("job_refused")
if refused:
    print(f"  refused job {refused['id']:<4} {refused['status']} "
          f"{refused['status_code']} after {refused['attempts']} attempt: {refused['error'][:60]}")
cancelled = result.get("job_cancelled")
if cancelled:
    stats = cancelled.get("worker_stats", {})
    print(f"  cancelled job {cancelled['id']:<4} never claimed "
          f"(worker claimed {stats.get('claimed')})")
meter = result.get("usage_workspace_a") or {}
print(f"  meter for workspace A: {meter.get('requests')} requests, "
      f"{meter.get('characters')} characters, {meter.get('audio_seconds')} s of audio")
print(f"  meter for workspace B: {(result.get('usage_workspace_b') or {}).get('requests')} requests")
print()
print(f"audio in {sys.argv[1].rsplit('/', 1)[0]}/audio")
print(f"full result in {sys.argv[1]}")
print("not a listening check, not GPU evidence, not deployed, and not M1 acceptance.")
sys.exit(0 if result.get("passed") else 1)
PY