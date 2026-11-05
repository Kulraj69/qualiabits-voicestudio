# Running the hosted control plane by hand

The short path is `hosted/scripts/hosted_demo.sh` at the bottom of the front
page. This is the same thing as long-lived processes, which is what you want
while working on it.

It assumes a venv built with
`uv sync --frozen --no-default-groups --group cpu --group dev`, and the Kokoro
bundle from `docs/engines/sherpa-onnx.md`.

```sh
# terminal 1 — the engine, CPU, with the permissive Kokoro model
export OMNIVOICE_SHERPA_MODEL="$PWD/kokoro-int8-multi-lang-v1_0"
export OMNIVOICE_TTS_BACKEND=sherpa-onnx
.venv/bin/python -m uvicorn backend.main:app --port 3900

# terminal 2 — the control plane in front of it
export QUALI_UPSTREAM_BASE_URL=http://127.0.0.1:3900
export QUALI_DATABASE_URL="sqlite:///$PWD/.hosted/control-plane.db"
export QUALI_STORAGE_DIR="$PWD/.hosted/objects"
.venv/bin/python -m uvicorn hosted.control_plane.app:app --port 8080

# terminal 3 — a worker, which is what actually speaks the queued jobs. Without
# one, jobs sit in `queued` forever; that is the point of a queue.
export QUALI_UPSTREAM_BASE_URL=http://127.0.0.1:3900
export QUALI_DATABASE_URL="sqlite:///$PWD/.hosted/control-plane.db"
export QUALI_STORAGE_DIR="$PWD/.hosted/objects"
.venv/bin/python hosted/scripts/run_worker.py            # polls forever
```

Then, in a fourth terminal, do exactly this:

```sh
BASE=http://127.0.0.1:8080

# 1. sign up. 201 with your workspace id.
curl -sS -X POST $BASE/v1/workspaces \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"a real password"}'

# 2. log in. Copy the token out of the answer.
curl -sS -X POST $BASE/v1/auth/token \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"a real password"}'

# 3. mint a key. The api_key value is shown this once; only its digest is stored.
curl -sS -X POST $BASE/v1/api-keys \
  -H "Authorization: Bearer TOKEN_FROM_STEP_2" \
  -H 'content-type: application/json' -d '{"label":"laptop"}'

# 4. speak. Real audio comes back, proxy-passed from the engine.
curl -sS -X POST $BASE/v1/audio/speech \
  -H "Authorization: Bearer KEY_FROM_STEP_3" \
  -H 'content-type: application/json' \
  -d '{"input":"यह एक हिन्दी वाक्य है।","voice":"hf_alpha","response_format":"wav"}' \
  --output hindi.wav

# 5. queue a longer piece of speech. 202 with an id. Nothing is spoken yet.
curl -sS -X POST $BASE/v1/jobs \
  -H "Authorization: Bearer KEY_FROM_STEP_3" \
  -H 'content-type: application/json' \
  -d '{"input":"नमस्ते। यह लंबा वाक्य कतार से बोला जाएगा।","voice":"hm_omega"}'

# 6. poll it until the worker has spoken it, then take the audio out of the store.
curl -sS $BASE/v1/jobs/1 -H "Authorization: Bearer KEY_FROM_STEP_3"
curl -sS $BASE/v1/jobs/1/audio -H "Authorization: Bearer KEY_FROM_STEP_3" \
  --output job1.wav
file job1.wav        # RIFF 24 kHz mono; sha256 matches the job's own receipt

# 7. read the meter. characters are what you asked for, audio_seconds are what
#    arrived, measured from the WAV you actually received — both routes, once each.
curl -sS $BASE/v1/usage -H "Authorization: Bearer KEY_FROM_STEP_3"

# 8. bounds. A ceiling of 2000 characters for this UTC month and 5 requests a
#    minute for each key. A session sets them; a key can only read them.
curl -sS -X PUT $BASE/v1/limits \
  -H "Authorization: ***" \
  -H 'content-type: application/json' \
  -d '{"character_ceiling":2000,"requests_per_minute":5}'

# 9. and watch it hold. Speak 30-character lines in a loop: past the fifth in one
#    minute the plane answers 429 with a Retry-After, and past 2000 characters it
#    answers 402 with the moment the period resets. Both refusals carry
#    x-qualiabits-limit-remaining, and neither costs a character.
for i in $(seq 1 8); do
  curl -sS -o /dev/null -w "%{http_code}\n" -X POST $BASE/v1/audio/speech \
    -H "Authorization: ***" -H 'content-type: application/json' \
    -d '{"input":"123456789012345678901234567890","voice":"hf_alpha"}'
done

# 10. give the ceiling back (null clears it; omitting a field leaves it alone).
curl -sS -X PUT $BASE/v1/limits \
  -H "Authorization: ***" \
  -H 'content-type: application/json' -d '{"character_ceiling":null}'
```
