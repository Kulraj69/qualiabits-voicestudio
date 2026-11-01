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
```

What a pass looks like: the "Measured" section below, rendered from
`hermes-reports/M2-slot32-e2e-r6/result.json` by `hosted/scripts/render_measured.py`.
That run tested `5702c9955` in a fresh clone whose venv was built by the commands
above, and the suite log beside it (`hermes-reports/M2-slot32-clean-tests-r6.log`)
is from that same revision, run in that same clone. The four WAVs it produced are
committed with it under `hermes-reports/M2-slot32-e2e-r6/audio/`, so a reader who
does not want to wait four minutes can still hear what came out — they are that run's
output, and nothing about the voice is claimed beyond "this is what the engine
returned".

A test refuses this citation if any file under `hosted/control_plane`, `hosted/worker`
or `hosted/scripts` changes afterwards, so a run over edited code cannot be quoted as
a run of what is pushed. The earlier runs are kept beside it and labelled in their own
files, including the ones that failed and why.
