# The hosted control plane

The product side of this fork: customers, logins, API keys, a usage meter, an object
store and a job queue, in front of the engine. The engine stays what it is — a local
speech server — and this layer is what turns it into something a business can buy.

Not deployed. Not public. No GPU. Nothing here has been sold to anyone.

**Start here.** Three ways in, cheapest first — none of them needs a GPU:

```sh
# 1. about 25 seconds, no model, no download: the tests, which are the real thing
#    except for the engine (a stub that answers with a WAV of a known duration).
.venv/bin/python -m pytest tests/hosted -q

# 2. about 4 minutes: the whole product, on the real engine. One command; it finds
#    or downloads the 132 MB model, hash-checks its bytes, and starts and stops
#    every process itself.
hosted/scripts/hosted_demo.sh

# 3. read on: "How a job works" is the part of this slice worth arguing about.
```

Both commands assume a venv built once with
`uv sync --frozen --no-default-groups --group cpu --group dev --python python3.12`.
The first is the honest short path for a reviewer on a timer; the second is the one
that proves audio comes out.

```
hosted/control_plane/         the plane itself
hosted/control_plane/jobs.py  the queue: claims, leases, retries, the receipt
hosted/control_plane/storage.py  where audio lives, and what the backends refuse
hosted/control_plane/migrations.py  bringing an older database forward
hosted/worker/runner.py       the worker: one job at a time, heartbeated
hosted/scripts/hosted_e2e.py  the end-to-end proof: starts every process itself
hosted/scripts/run_worker.py  the worker as a process, for a deployment or --once
hosted/scripts/hosted_demo.sh one command around all of it, with the model handling
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

What a pass looks like: the "Measured" section below, which quotes
`hermes-reports/M2-slot27-e2e-r4/result.json` — a clone of the pushed revision, so
the run and the page describe the same code. The two earlier runs (`-r1/`, which
failed on a comparison this page's author wrote wrong, and `-r2`/`-r3`, from the
working tree) are kept beside it.

## How it is put together

**Why a separate layer instead of editing the engine.** The engine loads models
and serves the desktop app. Hosted customers need logins, keys and a meter. Those
two sets of concerns change for different reasons, so they live in different
processes and different databases. The plane imports nothing from `backend/`; it
talks to the engine over HTTP, which also means an engine upgrade cannot alter
how a customer authenticates.

**Keys.** `qbv_<key_id>_<secret>`, 32 random bytes of secret. The database stores
the key id and a SHA-256 digest of the whole key, never the key. A leak of the
control-plane database therefore does not hand over working keys. Lookups go
through the key id, comparisons through `hmac.compare_digest`.

**Passwords.** scrypt (n=2¹⁴, r=8, p=1) with a per-user salt, parameters stored
beside the digest so they can be raised later. Logins against an unknown address
spend the same work as a real verify, so the endpoint is not an account-
enumeration oracle.

**Metering, two counters that mean different things.** `characters` is
attempt-based: the text you asked the engine to speak, counted for every request
that got past input validation, whether or not the engine answered.
`audio_seconds` is delivery-based: measured from the WAV header of the audio you
actually got, so a failed call adds a failure and no minutes. The response says
which is which so nobody reads a blended number as a bill.

**Per key, not just per workspace.** `GET /v1/usage` also breaks the same rows
down by key (`by_key`), with each key's public id and label, so a customer with a
laptop and a server can see which one is spending. Key material never appears
there — not the key, not its digest — and the breakdown is asserted to add up to
the workspace total rather than silently under-report.

**What is deliberately absent.** No route takes a workspace id, so a customer
cannot even ask for someone else's numbers. Speaking always requires a key, so
every byte of audio is attributable. (A job may be queued from the dashboard
instead, and then it is attributed to the workspace; the meter's `by_key` is empty
rather than inventing a key.) Revoking twice is fine; revoking someone else's key
is a 404, not a 403, so key ids are not a probe. A job id that belongs to another
workspace answers exactly like one that does not exist.

## How a job works

`POST /v1/audio/speech` is a passthrough: it holds your connection open while the
engine speaks. That is fine for a sentence and wrong for a paragraph, which is what
`POST /v1/jobs` is for. A job is a row, not a thread: it survives a restart of
anything, and its status is the answer to "what happened to my work".

```
POST /v1/jobs   -> 202, status queued, nothing spoken yet, meter untouched
worker process  -> claims it, asks the engine, stores the bytes, settles the job
GET /v1/jobs/{id}      -> queued | running | succeeded | failed | cancelled
GET /v1/jobs/{id}/audio -> the stored WAV (409 until it is finished)
```

Three mechanisms keep "one worker per job" true, and each has its own test:

1. **Claiming is a compare-and-swap.** The worker reads a candidate id, then issues
   one `UPDATE ... WHERE id = :candidate AND <still claimable>` and believes only a
   rowcount of 1. Two workers that read the same candidate both write, and the loser's
   `WHERE` no longer matches. This is what SQLite has instead of
   `SELECT ... FOR UPDATE SKIP LOCKED`, and it is checked with four worker threads
   racing over twelve jobs.
2. **A lease, not a lock.** A claim is valid until `lease_expires_at`; a worker that
   dies holding a job releases it by not renewing. The worker heartbeats every third
   of the lease while the engine works, so a slow job does not lose the lease it is
   using.
3. **Bounded attempts, and a sweep for the last one.** A claim requires an attempt
   in hand, so a job whose lease expired on its final attempt would sit `running`
   forever. The next claim pass fails it and writes the usage event it owes.

**Which failures retry.** The engine not answering (502/504) is our side of the
wire, so the job goes back to `queued` until its attempts run out. The engine's own
refusal (400) is deterministic, so it fails the job on the first try with the
engine's own message — spending two more minutes of CPU to be told the same thing
is not diligence. **Cancelling** a queued job is free: nothing reached the engine, so
the meter never sees it.

**One usage event per job**, written when the job reaches a terminal state. A retry
after our own 502 is not a second order, so the characters are counted once. Engine
time burned on attempts that were discarded is kept separately as
`wasted_engine_seconds`, so no single number implies we did work we threw away.

## Where the audio lives

Bytes go to an object store; the job row keeps the key, the digest and the length.
Keys are content addresses — `<sha256[:2]>/<sha256[2:4]>/<sha256>.wav` — so two
identical WAVs are one object, and the name is checkable: every read re-hashes the
bytes and refuses to serve anything that does not match the name it was stored
under. Writes go to a temp file, `fsync`, then `rename`, and are read back and
verified before the write is reported as done.

`QUALI_STORAGE_BACKEND=local` (the default) keeps objects under
`QUALI_STORAGE_DIR`. `=s3` needs `QUALI_S3_BUCKET` and credentials, and with a
bucket missing or `boto3` absent it **refuses at startup** rather than quietly
falling back to local disk — finding out where customer audio went by watching a
disk fill is not a debugging experience anybody should have. The S3 backend is
written and its logic is tested against a stub client; no bucket has ever been
used, and the page says so rather than implying otherwise.

## Migrations

The plane's database is its own (the engine has a separate one), and it now has two
versions: `m2a1` (slice A: workspaces, keys, sessions, usage) and `m2b1` (slice B:
the jobs table). `hosted/control_plane/migrations.py` applies ordered steps, each
stamping its version in the same transaction as its DDL, so a crash halfway leaves
the stamp alone and the next start retries — which is why every step is idempotent.

A database from a version nobody knows is still refused, naming both versions: that
is somebody else's schema, not a file to guess at. The tests build a real slice-A
database (slice A's DDL, copied rather than generated from today's models, so the
fixture cannot drift), put a customer, a key and a metered request in it, upgrade
it through the real app, and check that customer can still log in and use their
ledger afterwards.

Why not alembic, which the engine's database already uses: the control plane owns
one schema, applied linearly, with no branches and one consumer. When a step needs
data from a table a later step drops, or two people ship steps at once, that is the
moment to convert these functions into an alembic baseline revision — not before.

## Testing

Fast path, no model download, about twenty-five seconds:

```sh
.venv/bin/python -m pytest tests/hosted -q
```

Four suites, and the stub is the only substitution (a real ASGI app answering with a
WAV of a known duration, so the meter is checked against audio rather than against a
number the test invented; real connection refusals against a closed loopback port):

- `test_hosted_accounts.py` / `test_hosted_metering.py` — signup, login, session
  expiry, key lifecycle, revocation, cross-workspace isolation, every malformed
  credential, the engine's errors passed through, timeouts, 502s, input that never
  reaches the engine, and the two real bugs slice A hit.
- `test_hosted_storage.py` — content addressing, one object for identical bytes,
  atomic writes with no `.part` left behind, a corrupt object refused rather than
  served (and repaired by storing the right bytes again), traversal-shaped keys
  refused, and the S3 backend refusing when misconfigured.
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

From `hermes-reports/M2-slot27-e2e-r3/result.json` — engine, plane and worker
processes all started fresh by the harness, on one t3a.medium with no GPU
(31 Oct 2026). Numbers are copied from that file; the run records the commit it
tested (`4372299f0`) and the model hashes below.

```
engine ready 10.2 s   control plane ready 12.0 s   whole run 234.8 s
every process it started: clean shutdown

three direct requests through the control plane, workspace A:
  hf_alpha  hi   1.822 s audio, 24 kHz mono, peak 0.79   engine took 19.5 s
  hm_omega  hi   1.829 s audio,              peak 0.78   engine took 10.6 s
  hf_alpha  en   3.508 s audio,              peak 0.79   engine took 18.2 s
  plus one request for a voice the model does not have, refused 400 by the
  engine and passed through with the engine's own message naming its 54 voices.

one queued job, spoken by a separate worker process (run_worker.py --once):
  job 1  hm_omega  hi   208 characters -> 16.472 s of 24 kHz mono audio,
                         790,682 bytes, engine took 108.5 s
  worker counters: claimed 1, succeeded 1, failed 0, requeued 0, lost_lease 0
  the object on disk, the job's receipt and the download all hash to
  dac7e3e614e3f45ce5b6d3cf9a6d16be527f8057b1a669b09f700bd2a3429c72

one refused job (a voice the model does not have):
  failed 400 after 1 attempt, no retry, no audio served (409), error is the
  engine's own sentence, truncated at the ledger's 200 characters
one cancelled job:
  never claimed by the next worker pass, and it moved the meter by nothing

meter for A, at the end of the run: 6 requests, 388 characters, 23.630 s audio,
              1,134,420 bytes, failed_requests 2, by_status {"200": 4, "400": 2},
              requests_without_audio_seconds 2
              by_key: one key, label "a", same six requests and 23.630 s
meter for B, at the end: 0 requests, 0 characters, 0.000 s audio

same English request straight at the engine, for comparison:
  direct 3.488 s / 167,458 bytes   through the plane 3.508 s / 168,414 bytes

the engine's refusal for an unknown voice, taken both ways:
  sha256 both f9426105824c9597…, 699 bytes, byte-for-byte identical
```

The last two lines are the honest version of the pass-through claim. Speech
cannot be compared byte for byte between two calls because the engine samples,
and those two English numbers differ by 800 bytes — so no such claim is made
anywhere. What is compared byte for byte is a response the engine computes
deterministically: its refusal. That is checked against the real engine on every
run, and the 200 path is checked against a deterministic stub in the unit suite.

The model those numbers came from is the one `scripts/qualibits_quickstart.sh`
downloads and hash-checks (`tokens.txt` 6ebb6bb2…, `voices.bin` 1c5a5b98…); the
run recorded the same hashes plus the commit it tested, in the same file.

The CPU is not production, and this is the clearest evidence of it: 5 to 10
seconds of compute per second of speech, from 10.6 s for the fastest of the
three. The queued job is the same story told plainly: 16.5 seconds of audio cost
108.5 seconds of engine time, about 6.6 s per second. That is why the GPU run is
the owner's to approve, and exactly what this layer cannot work around — the plane
can hold the request open for a paragraph, but nothing here makes the engine
faster.

## Licence

AGPL-3.0, inherited from upstream VoiceStudio. Run this for customers and you owe
them the corresponding source; keep the fork public and keep the attribution in
`LICENSE` and `LICENSE-NOTICE.md`. The application licence does not cover speech
weights or voices — that is a separate decision, and it is the owner's.