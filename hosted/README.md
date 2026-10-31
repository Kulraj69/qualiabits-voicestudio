# The hosted control plane

The product side of this fork: customers, logins, API keys and a usage meter, in
front of the engine. The engine stays what it is — a local speech server — and
this layer is what turns it into something a business can buy.

Not deployed. Not public. No GPU. Nothing here has been sold to anyone.

```
hosted/control_plane/       the plane itself
hosted/scripts/hosted_e2e.py  the end-to-end proof against the real engine
tests/hosted/               its tests
```

## What it does today

| Route | Who may call it | What it does |
| --- | --- | --- |
| `GET /healthz` | anyone | is the plane up, and which schema is it on |
| `GET /healthz/upstream` | anyone | is the engine behind it up |
| `POST /v1/workspaces` | anyone | signup: email + password, one workspace per customer |
| `POST /v1/auth/token` | anyone | email + password → session token |
| `POST /v1/api-keys` | session | mint a key; the key is returned once |
| `GET /v1/api-keys` | session | list keys — never key material |
| `DELETE /v1/api-keys/{id}` | session | revoke one of your own keys |
| `GET /v1/usage` | session or key | this workspace's meter |
| `POST /v1/audio/speech` | API key | metered passthrough to the engine |

Not in yet, and named as the next slices: object storage (S3) for generated
audio, the per-workspace job queue, email verification, billing, deployment.

## Run it

Two processes. The engine first, then the plane pointed at it. This assumes a
venv built with `uv sync --frozen --no-default-groups --group cpu --group dev`,
and the Kokoro bundle from `docs/engines/sherpa-onnx.md`.

```sh
# terminal 1 — the engine, CPU, with the permissive Kokoro model
export OMNIVOICE_SHERPA_MODEL="$PWD/kokoro-int8-multi-lang-v1_0"
export OMNIVOICE_TTS_BACKEND=sherpa-onnx
.venv/bin/python -m uvicorn backend.main:app --port 3900

# terminal 2 — the control plane in front of it
export QUALI_UPSTREAM_BASE_URL=http://127.0.0.1:3900
export QUALI_DATABASE_URL="sqlite:///$PWD/.hosted/control-plane.db"
.venv/bin/python -m uvicorn hosted.control_plane.app:app --port 8080
```

Then, in a third terminal, do exactly this:

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
  -H "Authorization: Bearer $SESSION" \
  -H 'content-type: application/json' -d '{"label":"laptop"}'

# 4. speak. Real audio comes back, proxy-passed from the engine.
curl -sS -X POST $BASE/v1/audio/speech \
  -H "Authorization: Bearer $QBV_KEY" \
  -H 'content-type: application/json' \
  -d '{"input":"यह एक हिन्दी वाक्य है।","voice":"hf_alpha","response_format":"wav"}' \
  --output hindi.wav

# 5. read the meter. characters are what you asked for, audio_seconds are what
#    arrived, measured from the WAV you just received.
curl -sS $BASE/v1/usage -H "Authorization: Bearer $QBV_KEY"
```

What a pass looks like, from the run in `hermes-reports/M2-slot25-e2e-r1/`: see
"Measured" below.

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

**What is deliberately absent.** No route takes a workspace id, so a customer
cannot even ask for someone else's numbers. Speaking always requires a key, so
every byte of audio is attributable. Revoking twice is fine; revoking someone
else's key is a 404, not a 403, so key ids are not a probe.

## Testing

```sh
.venv/bin/python -m pytest tests/hosted -q
```

The suite runs the real plane against a stub engine (a real ASGI app answering
with a WAV of a known duration, so the meter is checked against audio rather than
against a number the test invented), and real connection failures against a
closed loopback port. It covers signup/login/session expiry, key lifecycle,
revocation, cross-workspace isolation, refusal of every malformed credential, the
engine's own errors passed through, timeouts, 502s, input that never reaches the
engine, and the two real bugs this slice hit: a key whose secret contains an
underscore being refused as malformed, and a short password answering 422 instead
of 400.

The end-to-end run is the other half:

```sh
.venv/bin/python hosted/scripts/hosted_e2e.py \
  --output hermes-reports/M2-slot25-e2e-r1 \
  --model /path/to/kokoro-int8-multi-lang-v1_0
```

It starts the real engine and the real plane, signs up two workspaces, mints
keys, speaks Hindi and English through the plane, compares with a direct engine
call, checks the refusals, reads both meters and revokes a key. `result.json`
records every check, the model file hashes and the commit the run tested.

## Measured

From `hermes-reports/M2-slot25-e2e-r1/result.json` (31 Oct 2026, one t3a.medium,
no GPU) — filled in from the run, not from intention:

<!-- MEASURED -->

## Licence

AGPL-3.0, inherited from upstream VoiceStudio. Run this for customers and you owe
them the corresponding source; keep the fork public and keep the attribution in
`LICENSE` and `LICENSE-NOTICE.md`. The application licence does not cover speech
weights or voices — that is a separate decision, and it is the owner's.