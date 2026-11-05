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


The plane's database is its own (the engine has a separate one), and it now has three
versions: `m2a1` (slice A: workspaces, keys, sessions, usage), `m2b1` (slice B: the
jobs table) and `m2c1` (slice C: `usage_events.transcript_characters`). `hosted/control_plane/migrations.py` applies ordered steps, each
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


## Speech in, and the third counter

Slice C adds transcription, and with it the first genuinely new decision since
slice A: what a counter *means*.

Speech out and speech in are different products. `characters` is text a workspace
asked the engine to say; `transcript_characters` is text the engine handed back for
a recording it was sent. Adding them would let a workspace that transcribes its own
call archive read as if it had asked us to speak all of it, so they are two columns,
two tiles on the Usage page and two notes beside them explaining which is which.
`audio_seconds` stays one counter because it means one thing — seconds of audio the
customer got or gave — but which way it moved is visible in the row's own content:
speech measures the WAV the engine returned, transcription measures the WAV the
customer uploaded, and both are read from the bytes' own header rather than from a
duration anybody reported. An MP3 has no WAV header, so it records no seconds and
the meter counts it under "requests without audio seconds" instead of inventing a
bitrate.

The upload is bounded before it is parsed: a declared `Content-Length` over the
ceiling is refused with no body read at all, and a chunked upload (which declares
nothing) is refused right after Starlette spooled the part — the part goes to a
temporary file, so that refusal is not a memory event either. Both refusals are metered
as failures and neither reaches the engine. The ceiling is a setting,
`QUALI_TRANSCRIBE_MAX_BYTES`, 25 MiB by default: about 25 minutes of 16-bit 44.1 kHz
mono.

Authentication on this route is a session *or* a key, unlike speech, which needs a
key. The key rule exists so that every byte of audio produced is attributable to a
credential; a recording handed *to* us is not that, and forcing a customer to mint a
credential before they can send their own file is friction with no safety gain. It is
still metered, still attributed to the workspace, and the per-key section of the meter
shows only what a key did.

The transcription model is the operator's, like the speech model: a plain directory
path (`ASR_MODEL_FASTER`), which the engine's own licence machinery treats as a
user-provided asset rather than a catalogue entry, so no terms are accepted on
anybody's behalf. `hosted/web/e2e/real-engine.sh` pins the bytes it uses by sha256
and stops rather than substituting anything. Nothing in this repository has ever
downloaded a model behind a licence gate.


## Limits, and why they are decided before the engine

Metering is a record. It is not a brake: the plane could always say what a workspace
had spent and could do nothing about the next request. Slice G adds the two limits a
hosted product needs before one customer's script can spend the month — a character
ceiling per workspace per UTC calendar month, and a request rate per API key in a
fixed one-minute window — and this section is the reasoning behind the shape they took.

**Both halves of the month live in one row.** The gate is a single conditional UPDATE:

```
UPDATE quota_periods SET reserved = reserved + n
 WHERE id = ? AND committed + reserved + n <= ceiling
```

A `rowcount` of 1 means the characters are taken; anything else is a refusal. The first
version compared the reservation against the ledger it had read a statement earlier, and
the thread-race test caught what that costs: a `settle` committing between the read and
the UPDATE moves spend out of `reserved` and into the ledger, so the reader's stale total
is too small and it lets the request through. Reading both figures from the row it is
about to update removes the window. `committed_characters` is a projection of the ledger
rather than a second record — it is seeded from the ledger when the row is created (so a
workspace that sets a ceiling mid-month, or a database upgraded from a slice with no
counters, counts what it has already spent) and advanced only in the same transaction as
the usage event it mirrors.

**Settle and release are different verbs, and confusing them charges for nothing.**
`settle` moves characters from reserved to committed because the engine *was* asked;
`release` removes them because it was not — a queued job its owner cancelled before any
worker claimed it. A cancellation that called `settle` charged the ceiling for work
nobody did, and a test in this slice found it.

**A reservation is exactly what will be charged.** A project reserves the chapter texts
the splitter produced, not the manuscript they came from, because the two characters at
each chapter boundary are never spoken — reserving the manuscript stranded them in the
counter for the rest of the month, which the project test measured.

**Two limits, two refusals, two meanings.** `402` for the ceiling, because waiting a
minute cannot help and the body carries the moment the period resets. `429` for the rate,
with a real `Retry-After`, because waiting always helps. Both carry the limit, the used
and the remaining numbers in the headers and a machine-readable `error` in the body, so
an integration branches on a code instead of matching English. A refused request costs
no characters — it never reached the engine, so writing characters for it would make the
ceiling inflate its own spend — but it does spend its rate slot, because a limiter counts
attempts. The refusal itself is a ledger row with zero characters, so "how often was this
workspace told no, and why" is a question the meter already answers.

**Fixed window, said out loud.** The rate is a fixed minute rather than a sliding one.
That is a real weakening: two bursts either side of a boundary can together total twice
the limit. A sliding window costs a row per request per key to buy fairness nobody has
asked for yet, and the alternative to naming it is pretending. The same honesty applies
to what is *not* here: nothing prunes `rate_windows` yet, and a request that dies
mid-flight leaves its reservation standing until the period turns over. The dashboard
shows `characters_reserved` apart from `characters_settled`, so that difference is
visible rather than inferred.

## The width: two breakpoints, and what decided them

M3's brief asks for a dashboard that is "responsive, fast, dark/light" — and until slice H
the first of those three words had nothing behind it. `hosted/web/app/globals.css` held
exactly one `@media` rule and it was `prefers-color-scheme`; there was no width breakpoint
in the file at all, and the browser suite drove the nine pages at one viewport. What that
cost was measured before it was fixed, and the transcript of that run is the reason the
numbers in this section are numbers rather than adjectives: at 390px **every one of the
nine pages scrolled sideways**, the document measuring 560px wide in a 390px window; the
session controls (`span.who`, the sign-out button) sat at x=415..560, off the right edge;
the theme switch, a 34px circle by design, was squeezed to 17x34. Every one of those
numbers is that run's own output — `hermes-reports/M3-slot81-web-e2e-before-r1.log` — which
also records its own verdict (151 of 155 checks passed). It is one file and it is the only
one that records the before-state: it was taken over a working tree and kept no
`result.json`, which is said here rather than glossed.

**Why the page was 560px wide.** The top bar is a flex row, and a flex item's automatic
minimum size is its content's, so a row of items that cannot shrink below their content
cannot be narrower than the sum of those minima either. The bar's minimum came to 560px
and every page inherited it. No amount of padding or wrapping *inside* the page could fix
that, because the offending row was the page's own frame.

**Why the breakpoints are where they are.** Below 900px the bar lays out as two rows — the
brand, the theme switch and the session on the first, the nine nav links on their own
full-width line underneath — because that is the honest way to give nine links room without
either hiding some or letting the row push the session off the page. It used to be decided
at 640px only, and the band between 768 and 900 is the reason it is not any more: a single
row still “fits” there in the arithmetic sense — the bar's one-row minimum is 560px — and
the nav is what pays for it. That band is reasoned from the 560px minimum rather than
measured: the harness drives 390, 768 and 1440, so the tablet end of it is measured and the
rest is an inference this page labels as one. The free space in that row is what the nav
has to live in, so the nav is given its own instead. Below 640px the page padding tightens
once more, the brand's
“Dashboard” suffix goes, so does the signed-in name beside the sign-out button — the home
page's own heading carries it — and the two places with a fixed minimum width — the session
block and the revealed API key — are allowed to shrink. Which of those is *needed* is not a
judgement call: the bar's rows are counted from its own children by the run, and until this
slice's last round that count was published as `undefined`, so a third row at 390px — the
sign-out button pushed onto a line of its own by a name capped at 32vw — was reported as a
pass. With the count fixed the run failed on the real value, the name went, and the phone
bar now measures two rows on all nine pages.

**What the check refuses to call a defect.** A wide table on a phone may scroll inside its
own box: `.table-wrap` is an `overflow-x: auto` container and that is the whole reason it
exists. So the run walks every element on every page and accepts one that overflows *only*
if it sits inside a scroll container that is itself scrollable; anything else is reported
with its tag, class and coordinates. That is also why the assertion is per element and not
per page: `scrollWidth > innerWidth` alone would say a page is broken without saying what
broke it, and the fix would be guesswork.

**Why a width block may not declare a token.** The light palette is declared twice — once
under `data-theme="light"`, once inside `prefers-color-scheme` — and
`tests/hosted/test_theme_tokens.py` exists to keep the two copies identical. A breakpoint
that declared a colour token would be a third copy that guard knows nothing about, so the
rule is that width blocks are layout only, and `tests/hosted/test_dashboard_width_rules.py`
fails if one declares a custom property. The same file checks the breakpoints' order
(widest first, so a narrower rule can override a wider one) and that the specific rules
that hold the two-row bar together are still there: deleting one of them would put the
560px minimum back, and only a browser run would notice.

**What this does not claim.** Three widths, one browser, on loopback: Chromium at 390x844,
768x1024 and 1440x900, driven by the same harness as everything else in this suite. There
is no testing on a real phone, no touch-device testing, no Safari or Firefox pass, and no
claim about a width between the three (the rules are `max-width`, so a 500px window gets
the 640px block and a 900px one gets the desktop layout). The screenshots the run keeps —
every page at both narrow widths — are the artefact a reader can look at; the numbers are
what the run asserts.
