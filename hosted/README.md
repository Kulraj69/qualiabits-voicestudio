# The hosted control plane

The product side of this fork: customers, logins, API keys, a usage meter, an object
store and a job queue, in front of the engine. The engine stays what it is — a local
speech server — and this layer is what turns it into something a business can buy.

Not deployed. Not public. No GPU. Nothing here has been sold to anyone.

**Start here.** From nothing, in a fresh clone — the whole bootstrap is four commands:

```sh
git clone https://github.com/Kulraj69/qualiabits-voicestudio.git
cd qualiabits-voicestudio
uv sync --frozen --no-default-groups --group cpu --group dev --python python3.12

# about 25 seconds, no model, no download: the tests. Everything is real except the
# engine, which is a stub answering with a WAV of a known duration.
.venv/bin/python -m pytest tests/hosted -q

# about 4 minutes on a small CPU box: the whole product, on the real engine. One
# command — it finds or downloads the 132 MB model, hash-checks its bytes, starts the
# engine, the plane and a worker, runs the proof, stops them, and prints what passed.
hosted/scripts/hosted_demo.sh
```

The first is the honest short path for a reviewer on a timer; the second is the one
that proves audio comes out. The run that produced the numbers under "Measured" is
committed in `hermes-reports/`, and the reasoning behind the design is in
[`hosted/DESIGN.md`](DESIGN.md).

```
hosted/control_plane/         the plane itself
hosted/control_plane/jobs.py  the queue: claims, leases, retries, the receipt
hosted/control_plane/storage.py  where audio lives, and what the backends refuse
hosted/control_plane/migrations.py  bringing an older database forward
hosted/worker/runner.py       the worker: one job at a time, heartbeated
hosted/scripts/hosted_e2e.py  the end-to-end proof: starts every process itself
hosted/scripts/run_worker.py  the worker as a process, for a deployment or --once
hosted/scripts/hosted_demo.sh one command around all of it, with the model handling
hosted/DESIGN.md              why it is shaped this way, and what each choice cost
tests/hosted/                 its tests
```

## See it work, one command


```sh
hosted/scripts/hosted_demo.sh
```

It finds the Kokoro bundle (or downloads it and checks three sha256 values against
pinned ones), verifies the model bytes, then starts the engine, the plane and the
worker on reserved loopback sockets, runs the whole proof, stops them, and prints
what passed. Nothing to start by hand, nothing left running. Several minutes on a
small CPU box — the queued job is the slow part, because this engine spends seconds
of CPU per second of speech. Evidence lands in whatever work directory you pass
(`.hosted-demo` by default).

The tests are the faster path and need no model at all, about twenty-five seconds:

```sh
.venv/bin/python -m pytest tests/hosted -q
```

## What it does today


| Route | Who may call it | What it does |
| --- | --- | --- |
| `GET /healthz` | anyone | is the plane up, on which schema, with how many jobs in each state |
| `GET /healthz/upstream` | anyone | is the engine behind it up |
| `POST /v1/workspaces` | anyone | signup: email + password, one workspace per customer |
| `POST /v1/auth/token` | anyone | email + password → session token |
| `POST /v1/api-keys` | session | mint a key; the key is returned once |
| `GET /v1/api-keys` | session | list keys — never key material |
| `DELETE /v1/api-keys/{id}` | session | revoke one of your own keys |
| `GET /v1/usage` | session or key | this workspace's meter, and its key-by-key breakdown |
| `POST /v1/audio/speech` | API key | metered passthrough to the engine, for one short request |
| `POST /v1/jobs` | session or key | queue a longer piece of speech (up to 20,000 characters) |
| `GET /v1/jobs` | session or key | your jobs, newest first, with a status filter |
| `GET /v1/jobs/{id}` | session or key | one of your jobs, with its full text |
| `DELETE /v1/jobs/{id}` | session or key | cancel a job that has not been claimed |
| `GET /v1/jobs/{id}/audio` | session or key | the stored audio of a finished job |

Not in yet, and named as the next slices: email verification, billing, deployment.

## Run it by hand


The demo script above is the short path. This is the same thing as three long-lived
processes, which is what you want while working on it. It assumes a venv built
with `uv sync --frozen --no-default-groups --group cpu --group dev`, and the
Kokoro bundle from `docs/engines/sherpa-onnx.md`.

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

What a pass looks like: the "Measured" section below. It is rendered from
`hermes-reports/M2-slot32-e2e-r2/result.json` by `hosted/scripts/render_measured.py`,
so the page and that file cannot drift; a test also checks the run's own manifest of
tracked-file hashes against git, so a run over edited code cannot be quoted as a run
of the pushed revision. The earlier runs are kept beside it and labelled in their own
files, including the one that failed and why.

## How it is built

Four decisions carry this layer, each with its reason and its test written up in
[`hosted/DESIGN.md`](DESIGN.md):

- **Its own process and database.** The plane imports nothing from `backend/` and
  talks to the engine over HTTP, so an engine upgrade cannot change how a customer
  authenticates.
- **Keys are digests.** `qbv_<key_id>_<secret>`, and only the id and a SHA-256 digest
  are stored, so a leak of the plane's database does not hand over working keys.
- **A job is a row, not a thread.** Claiming is compare-and-swap, a claim is a lease a
  dying worker stops renewing, and attempts are bounded — including the last one,
  which would otherwise sit `running` forever.
- **Two counters that mean different things.** `characters` counts what was asked of
  the engine; `audio_seconds` counts what arrived, measured from the WAV header.
- **Audio is content-addressed.** The key *is* the SHA-256 of the bytes, so two
  identical WAVs are one object and every read can prove it got the right one. Writes
  are read back and verified before they are called stored, on both backends.

## Testing


Fast path, no model download, about twenty-five seconds:

```sh
.venv/bin/python -m pytest tests/hosted -q
```

Seven test files and two substitutions. The first is the engine: a real ASGI app
answering with a WAV of a known duration, so the meter is checked against audio
rather than against a number the test invented. The second is the object store's S3
path, which runs against a local S3-compatible server on loopback (moto, started in
process) so the client really signs requests and objects really cross a socket —
still not S3, still not a bucket, and the storage suite says which of its cases are
which. Everything else is real: real connection refusals against a closed loopback
port, a real SQLite file, real threads racing for the same jobs.

- `test_hosted_accounts.py` / `test_hosted_metering.py` — signup, login, session
  expiry, key lifecycle, revocation, cross-workspace isolation, every malformed
  credential, the engine's errors passed through, timeouts, 502s, input that never
  reaches the engine, and the two real bugs slice A hit.
- `test_hosted_storage.py` — content addressing, one object for identical bytes,
  atomic writes with no `.part` left behind, a corrupt object refused rather than
  served, traversal-shaped keys refused, the S3 backend refusing when misconfigured,
  the service's checksum checked against the digest the key was built from, a
  same-size wrong-bytes upload caught by reading the object back, and four cases on a
  real socket against a local S3-compatible server: store, read back, an object that
  no longer matches its name, and a bucket that does not exist.
- `test_readme_measured_is_generated.py` — the page's Measured section is what
  `hosted/scripts/render_measured.py` renders from the run file the page cites; the
  cited run was taken over a clean tree; the run's manifest accounts for every tracked
  entry and every hash in it matches that path in the commit the run names; and the
  code it measured has not changed since. The manifest check is the part that does the
  work: `"worktree_dirty": false` is a field the harness writes about itself, while
  the manifest is checked against git and fails if the run hashed anything the commit
  does not contain — including if it hashed a file the commit never tracked. There is
  also a test that a tampered manifest is refused.
- `test_hosted_jobs.py` — queue, worker, stored audio, meter, isolation of job ids,
  every refusal boundary (400 empty, 413 at 20,001, 422 past the body backstop),
  cancellation, and a job that survives a plane restart.
- `test_hosted_queue.py` — four threads racing for twelve jobs, leases that expire
  and are reclaimed, a dead worker's last attempt swept into `failed` instead of
  hanging, a worker that lost its lease being unable to overwrite the new owner's
  result, retries that stay bounded, and characters counted once across them.
- `test_hosted_migrations.py` — fresh create, in-place upgrade with the rows intact,
  idempotent re-run, and refusals for unknown and newer schemas.

The end-to-end run is the other half — the one-command script at the top calls
it, and this is it directly when you already have a model directory:

```sh
.venv/bin/python hosted/scripts/hosted_e2e.py \
  --output /tmp/hosted-evidence-run \
  --model /path/to/kokoro-int8-multi-lang-v1_0
```

It starts the real engine, the real plane and real worker processes, signs up two
workspaces, mints keys, speaks Hindi and English through the plane, queues a longer
Hindi paragraph and has a worker process speak it, downloads the audio back out of
the object store and checks it against the job's own digest, takes the same request
straight from the engine for comparison, compares the engine's refusal byte for byte
on both paths, checks a refused job fails once and a cancelled job is never spoken,
reads both meters and revokes a key. `result.json` records every check, the worker
counters, the model file hashes and the commit the run tested.

## Measured

From `hermes-reports/M2-slot32-e2e-r2/result.json` — engine, plane and worker processes all started fresh by the
harness on one CPU box (x86_64, 2 CPUs, Python 3.12.3, no
GPU). The section is rendered from that file by
`hosted/scripts/render_measured.py`, and a test fails if the page and the file
disagree. The run records the commit it tested
(85647e058, worktree clean), the model hashes below, and the
sha256 of every tracked file as it was at run time -- so a run over edited code
cannot pass itself off as a run of the pushed revision.

To reproduce it, point the harness at a directory outside this checkout: an
evidence directory inside the repository is untracked, so the run would report
itself dirty and its numbers would be worth less than these.

```sh
.venv/bin/python hosted/scripts/hosted_e2e.py \
  --output /tmp/hosted-evidence-run --model /path/to/kokoro-int8-multi-lang-v1_0
```

```
engine ready 9.3 s   control plane ready 11.0 s   whole run 201.5 s
every process it started: clean shutdown

three direct requests through the control plane, workspace A:
  hf_alpha  hi   1.832 s audio, 24 kHz mono, peak 0.79   engine took 18.7 s
  hm_omega  hi   1.826 s audio, 24 kHz mono, peak 0.80   engine took 10.4 s
  hf_alpha  en   3.507 s audio, 24 kHz mono, peak 0.80   engine took 17.4 s
  plus one request for a voice the model does not have, refused 400 by the
  engine and passed through with the engine's own message naming its 54 voices.

one queued job, spoken by a separate worker process (run_worker.py --once):
  job 1  hm_omega  hi   208 characters -> 16.472 s of 24 kHz mono audio,
                         790,678 bytes, engine took 102.6 s
  worker counters: claimed 1, succeeded 1, failed 0, requeued 0, lost_lease 0
  the object on disk, the job's receipt and the download all hash to
  f16713ccc9aa74a14bb64e212a58bb168b43f60b15ca8c28a6b97fc555644a44

one refused job (a voice the model does not have):
  failed 400 after 1 attempt, no retry, no audio served (409), error is the
  engine's own sentence, truncated at the ledger's 200 characters
one cancelled job:
  never claimed by the next worker pass, and it moved the meter by nothing

meter for A, at the end of the run: 6 requests, 388 characters, 23.637 s audio,
              1,134,746 bytes, failed_requests 2, by_status {"200": 4, "400": 2},
              requests_without_audio_seconds 2
              by_key: one key, label "a", same 6 requests and 23.637 s
meter for B, at the end: 0 requests, 0 characters, 0.000 s audio

same English request straight at the engine, for comparison:
  direct 3.511 s / 168,564 bytes   through the plane 3.507 s / 168,398 bytes

the engine's refusal for an unknown voice, taken both ways:
  sha256 both f9426105824c9597…, 699 bytes, byte-for-byte identical

engine seconds spent per second of speech, the number that decides whether this
is sellable on a box like this one:
  hindi_hf_alpha     10.2 s
  hindi_hm_omega     5.7 s
  english            5.0 s
```

The English lines differ because the engine samples: the two numbers above are the
same request taken both ways, and they differ by
166 bytes. No byte-for-byte claim is made anywhere for speech. What is compared byte for byte is a response the engine
computes deterministically: its refusal, checked against the real engine on every
run; the 200 path is compared against a deterministic stub in the unit suite.

The model those numbers came from is the one `scripts/qualibits_quickstart.sh`
downloads and hash-checks (`tokens.txt` 6ebb6bb2…, `voices.bin` 1c5a5b98…); the run
recorded the same hashes plus the commit it tested, in the same file.

The CPU is not production, and the rates above are the clearest evidence of it: the
queued job spent 102.6 s of engine time on 16.472 s of audio, about 6.2 s
per second. That is why the GPU run is the owner's to approve, and exactly what
this layer cannot work around — the plane can hold a request open for a paragraph,
but nothing here makes the engine faster.

## Licence


AGPL-3.0, inherited from upstream VoiceStudio. Run this for customers and you owe
them the corresponding source; keep the fork public and keep the attribution in
`LICENSE` and `LICENSE-NOTICE.md`. The application licence does not cover speech
weights or voices — that is a separate decision, and it is the owner's.