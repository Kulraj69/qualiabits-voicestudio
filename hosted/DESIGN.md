# How the hosted control plane is built

The reasoning behind `hosted/`. `hosted/README.md` is the short path a reviewer
reads first; this file is where the arguments are, and every claim in it points
at code or at a test.

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
disk fill is not a debugging experience anybody should have. On the S3 path a write
is verified twice: `HEAD` must agree on the length, and then the object's own digest
must match the SHA-256 its key is built from — taken from the service's checksum when
it returns one (`put_object` asks for `SHA256`), and otherwise by reading the object
back and hashing it. A service holding the right number of wrong bytes is refused, not
recorded as stored.

The S3 backend's logic is covered against a stub client and over real HTTP against a
local S3-compatible server (moto, in process), which does return the checksum: real
signed requests, a store, a read-back, an object misconfigured so it no longer matches
the name it is stored under, and a bucket that does not exist. Neither is a bucket: no
real bucket has ever been used, because that needs credentials and a spending decision
from the owner. IAM policy, lifecycle rules, multipart upload and cross-region
behaviour are reasoned about here, not proven, and the docstring in `storage.py` says
so.

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

