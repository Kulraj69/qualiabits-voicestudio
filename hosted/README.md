# The hosted control plane

The product side of this fork: customers, logins, API keys, a usage meter, an object
store and a job queue, in front of the engine. The engine stays what it is — a local
speech server — and this layer is what turns it into something a business can buy.

Not deployed. Not public. No GPU. Nothing here has been sold to anyone.

**Start here.** From nothing, in a fresh clone:

```sh
git clone https://github.com/Kulraj69/qualiabits-voicestudio.git
cd qualiabits-voicestudio
uv sync --frozen --no-default-groups --group cpu --group dev --python python3.12

# about half a minute (30-35 s on the box the numbers below came from), no model and
# no download: the tests. Everything is real except the engine, which is a stub
# answering with a WAV of a known duration.
.venv/bin/python -m pytest tests/hosted -q

# about 4 minutes on a small CPU box: the whole product, on the real engine. One
# command — it finds or downloads the Kokoro bundle (132,353,410 bytes to download,
# which is the release asset's own Content-Length; 189,881,734 bytes unpacked, both
# recorded with the commands that measured them in
# hermes-reports/M2-slot32-model-bytes.txt), hash-checks its files, starts the
# engine, the plane and a worker, runs the proof, stops them, and prints what passed.
hosted/scripts/hosted_demo.sh
```

The first needs no model and is the path for a reviewer on a timer; the second
proves audio comes out. The run behind "Measured" is committed under `hermes-reports/`,
with the WAVs it produced, and the reasoning behind the design is in
[`hosted/DESIGN.md`](DESIGN.md).

```
hosted/control_plane/*.py     the plane: accounts, keys, the job queue, storage, migrations
hosted/worker/runner.py       the worker: one job at a time, heartbeated
hosted/scripts/hosted_e2e.py  the end-to-end proof: starts every process itself
hosted/scripts/hosted_demo.sh one command around all of it, with the model handling
hosted/DESIGN.md              why it is shaped this way, and what each choice cost
hosted/M2-ACCEPTANCE.md       the milestone's requirements against code, tests and the run
hosted/RUN-IT-BY-HAND.md      the same thing as long-lived processes, with curl steps
tests/hosted/                 its tests
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

The demo script is the short path. When you want the three processes as long-lived
terminals — for working on it rather than checking it — the same thing is written
out step by step with a curl walkthrough in
[`hosted/RUN-IT-BY-HAND.md`](RUN-IT-BY-HAND.md).

## How it is built

Each decision below has its reason and its cost in [`hosted/DESIGN.md`](DESIGN.md).
The plane is its own process and database and imports nothing from `backend/`, so an
engine upgrade cannot change how a customer authenticates. Keys are stored as digests
(`qbv_<key_id>_<secret>`; only the id and a SHA-256 digest are kept), so a leak of the
database does not hand over working keys. A job is a row rather than a thread: claiming
is compare-and-swap, a claim is a lease a dying worker stops renewing, and attempts are
bounded — including the last one, which would otherwise sit `running` forever. And the
two counters mean different things on purpose: `characters` is what was asked of the
engine, `audio_seconds` is what arrived, measured from the WAV header of the audio that
came back. Audio is content-addressed — the key *is* the SHA-256 of the bytes — and
writes are read back and verified before they are called stored, on both backends.

## Testing

The same fast path as at the top: no model download, 30 to 35 seconds, nine test
files and two substitutions. The engine is a real ASGI app answering with a WAV of a
known duration, so the meter is checked against audio rather than against a number a
test invented. The object store's S3 path runs against a local S3-compatible server on
loopback (moto, started in process), so the client really signs requests and objects
really cross a socket — still not S3, still not a bucket. Everything else is real:
connection refusals against a closed port, a real SQLite file, real threads racing for
the same job.

- `test_hosted_accounts.py`, `test_hosted_metering.py` — signup, login, session
  expiry, key lifecycle, cross-workspace isolation, malformed credentials, the
  engine's errors passed through, timeouts, 502s, and the two real bugs slice A hit.
- `test_hosted_storage.py`, `test_hosted_jobs.py`, `test_hosted_queue.py` — content
  addressing, atomic writes, corrupt objects and traversal-shaped keys refused, the
  four socket cases against the S3 emulator; then the queue: every refusal boundary,
  cancellation, a job that survives a plane restart, four threads racing for twelve
  jobs, expiring leases, a dead worker's last attempt swept into `failed`, a worker
  that lost its lease unable to overwrite the owner's result, and characters counted
  once across retries.
- `test_hosted_migrations.py` — fresh create, in-place upgrade with the rows intact,
  idempotent re-run, unknown and newer schemas refused.
- `test_readme_measured_is_generated.py`, `test_review_bundle_is_faithful.py`,
  `test_m2_acceptance_matrix.py` — the evidence chain: the page's numbers are rendered
  from the run it cites and that run's manifest is verified against git file by file;
  the judge bundle agrees with the receipts it pastes, or refuses to be written; and
  the acceptance table still points at code, tests and run evidence that exist.

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

What a pass looks like: the "Measured" section below, rendered from
`hermes-reports/M2-slot32-e2e-r8/result.json` by `hosted/scripts/render_measured.py`.
That run is the one-command path at the top of this page — `hosted/scripts/hosted_demo.sh`
in a fresh clone, with no copy of the model where the script looks, so it downloaded the
bundle, checked the three pinned hashes and then ran the proof. The transcript is
`hermes-reports/M2-slot32-demo-r8.log`, and the suite log beside it
(`hermes-reports/M2-slot32-clean-tests-r8.log`) is from that same clone at the same
revision, `30963fd8a`. The four WAVs the run produced are committed with it under
`hermes-reports/M2-slot32-e2e-r8/audio/`, so a reader who would rather not wait five
minutes can hear what came out — they are that run's output, and nothing about the voice
is claimed beyond "this is what the engine returned".

A test refuses this citation if any file under `hosted/control_plane`, `hosted/worker`
or `hosted/scripts` changes afterwards, so a run over edited code cannot be quoted as
a run of what is pushed. The earlier runs are kept beside it and labelled in their own
files, including the ones that failed and why.

## Measured

From `hermes-reports/M2-slot32-e2e-r8/result.json` — engine, plane and worker processes all started fresh by the
harness on one CPU box (x86_64, 2 CPUs, Python 3.12.3, no
GPU). The section is rendered from that file by
`hosted/scripts/render_measured.py`, and a test fails if the page and the file
disagree. The run records the commit it tested
(30963fd8a, worktree clean), the model hashes below, and the
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
engine ready 18.5 s   control plane ready 21.8 s   whole run 248.9 s
every process it started: clean shutdown

three direct requests through the control plane, workspace A:
  hf_alpha  hi   1.830 s audio, 24 kHz mono, peak 0.79   engine took 19.7 s
  hm_omega  hi   1.826 s audio, 24 kHz mono, peak 0.79   engine took 10.3 s
  hf_alpha  en   3.487 s audio, 24 kHz mono, peak 0.80   engine took 17.7 s
  plus one request for a voice the model does not have, refused 400 by the
  engine and passed through with the engine's own message naming its 54 voices.

one queued job, spoken by a separate worker process (run_worker.py --once):
  job 1  hm_omega  hi   208 characters -> 16.477 s of 24 kHz mono audio,
                         790,946 bytes, engine took 124.1 s
  worker counters: claimed 1, succeeded 1, failed 0, requeued 0, lost_lease 0
  the object on disk, the job's receipt and the download all hash to
  71174a91bb590f02daf465ac22058c55cb68accf63e553fe072b4a6da48c0787

one refused job (a voice the model does not have):
  failed 400 after 1 attempt, no retry, no audio served (409), error is the
  engine's own sentence, truncated at the ledger's 200 characters
one cancelled job:
  never claimed by the next worker pass, and it moved the meter by nothing

meter for A, at the end of the run: 6 requests, 388 characters, 23.620 s audio,
              1,133,926 bytes, failed_requests 2, by_status {"200": 4, "400": 2},
              requests_without_audio_seconds 2
              by_key: one key, label "a", same 6 requests and 23.620 s
meter for B, at the end: 0 requests, 0 characters, 0.000 s audio

same English request straight at the engine, for comparison:
  direct 3.487 s / 167,410 bytes   through the plane 3.487 s / 167,438 bytes

the engine's refusal for an unknown voice, taken both ways:
  sha256 both f9426105824c9597…, 699 bytes, byte-for-byte identical

engine seconds spent per second of speech, the number that decides whether this
is sellable on a box like this one:
  hindi_hf_alpha     10.8 s
  hindi_hm_omega     5.6 s
  english            5.1 s
```

The English lines differ because the engine samples: the two numbers above are the
same request taken both ways, and they differ by
28 bytes. No byte-for-byte claim is made anywhere for speech. What is compared byte for byte is a response the engine
computes deterministically: its refusal, checked against the real engine on every
run; the 200 path is compared against a deterministic stub in the unit suite.

The model those numbers came from is the one `scripts/qualibits_quickstart.sh`
downloads and hash-checks (`tokens.txt` 6ebb6bb2…, `voices.bin` 1c5a5b98…); the run
recorded the same hashes plus the commit it tested, in the same file.

The CPU is not production, and the rates above are the clearest evidence of it: the
queued job spent 124.1 s of engine time on 16.477 s of audio, about 7.5 s
per second. That is why the GPU run is the owner's to approve, and exactly what
this layer cannot work around — the plane can hold a request open for a paragraph,
but nothing here makes the engine faster.

## Licence


AGPL-3.0, inherited from upstream VoiceStudio. Run this for customers and you owe
them the corresponding source; keep the fork public and keep the attribution in
`LICENSE` and `LICENSE-NOTICE.md`. The application licence does not cover speech
weights or voices — that is a separate decision, and it is the owner's.