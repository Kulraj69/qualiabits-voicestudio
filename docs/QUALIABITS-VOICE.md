# QualiaBits Voice

QualiaBits builds an AI call centre for Indian BPOs: agents that run calls on our
own dialer, and a QA layer that scores every call. This fork is where the voice
side is built.

The base is [VoiceStudio](https://github.com/debpalash/VoiceStudio), a local
studio that clones voices, designs them, dubs, dictates and transcribes on your
own machine. We keep it under AGPL-3.0 with its attribution intact and build the
hosted product on top. Hindi and English first, and the HTTP API as the
integration point for the dialer and the scoring engine.

**M1 is not finished and nothing here is live.** No hosted URL, no login, no
workspaces, no phone calls. This page says what the fork does today, what it
does not, and how to see both for yourself.

## What works today

- The backend runs headless. `scripts/headless_baseline.py` starts the real
  `backend.main:app` on a reserved loopback socket, waits for readiness, reads
  `/openapi.json` and the model and engine catalogues, posts English and Hindi
  to `/v1/audio/speech`, then stops the server. With no model configured the
  speech requests are refused on purpose; the API contract can still pass.
- With an operator-supplied Kokoro model on CPU, `/v1/audio/speech` returns real
  audio for English and for Hindi. Measured 31 October 2026 on a 2-core x86
  instance, one sentence per request: Hindi `hf_alpha` 5.19 s, Hindi `hm_omega`
  5.11 s, English `af_bella` 4.91 s, all 24 kHz mono WAV, three distinct files.
  The bundle carries four Hindi voices: `hf_alpha`, `hf_beta`, `hm_omega` and
  `hm_psi`. The two above are the ones measured so far.
- A `voice` that the model does not have is refused with a 400 naming the voice
  families and the count, and an out-of-range numeric id is refused too, instead
  of quietly speaking with voice 0. Text that phonemizes to nothing is refused
  rather than returned as a zero-length WAV.
- 139 tests pass from a clean checkout of the branch (the command and its output
  are in `hermes-reports/M1-slot23-clean-tests.log`).

## What is not verified

Nothing below is claimed anywhere in this repo, and the evidence files say so in
their own fields (`m1_complete`, `gpu_verified`, `listening_verified` are false).

- **No GPU run.** Every number above is CPU. There is no GPU evidence at all.
- **No human listening check.** `faster-whisper small` recovers 11 of 12 English
  tokens and 6 of 13 and 9 of 13 Hindi tokens from those WAVs. That is a machine
  lower bound on whether the words are there, not a verdict on how the voice
  sounds. Whether the Hindi is good enough to sell is the owner's call, and it
  is still open.
- **No hosted service.** Login, workspaces, multi-tenancy, quotas and the hosted
  dashboard from the product plan do not exist here yet. The Electron app in
  this repo is upstream VoiceStudio's desktop UI, not our hosted console.
- **No telephony.** No agents, no dialer, no call logs, no call recording.
- **No production engine or model decision.** Kokoro is used here because its
  publisher declares Apache-2.0 and it runs on CPU; that is a working demo
  configuration, not a cleared production choice. No speech-model terms have
  been accepted for anyone.
- **The timings are synthesis time** for one short sentence on a small shared
  box. They are not call latency, not concurrency, and not capacity.

## See it yourself

Two paths. The first needs no model and takes about a minute; the second needs a
132 MB model download and produces actual Hindi audio.

```sh
git clone --branch work/m1-headless https://github.com/Kulraj69/qualiabits-voicestudio.git
cd qualiabits-voicestudio
uv sync --frozen --no-default-groups --group cpu --group dev --python python3.12
python3 -c "from pathlib import Path; Path('.env').open('x').close()"   # empty sentinel, refuses to overwrite
.venv/bin/python scripts/headless_baseline.py --output /tmp/vq-baseline
```

The harness prints each check and writes `result.json` and `server.log` into the
output directory. Expect the API-contract checks to pass and the two speech
requests to come back `403 model_licence_required`, because no model is
configured yet.

For real speech, point the engine at a Kokoro bundle. The download and a curl
example are in
[docs/engines/sherpa-onnx.md](engines/sherpa-onnx.md#example-a-multilingual-kokoro-bundle-54-voices-four-of-them-hindi);
the same model then drives our probe:

```sh
curl -LO https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-int8-multi-lang-v1_0.tar.bz2
tar xjf kokoro-int8-multi-lang-v1_0.tar.bz2
.venv/bin/python scripts/m1_hindi_probe.py --output /tmp/vq-speech --model-dir "$PWD/kokoro-int8-multi-lang-v1_0"
```

The probe starts the real backend with `OMNIVOICE_TTS_BACKEND=sherpa-onnx`,
records the model's own metadata and file hashes, posts Hindi from two Hindi
voices plus English, then posts an unknown voice and an out-of-range id. A pass
means eight checks are true and `/tmp/vq-speech/audio/` holds three WAV files.
It is not a listening check and not M1 acceptance. The result file says both in
plain words.

## Where the evidence lives

All of it is committed, so a clone can check the claims without rerunning
anything: `hermes-reports/M1-slot23-speech-r1/` (a round that failed its own
assertion, kept), `hermes-reports/M1-slot23-speech-r2/` (result, server log,
three WAVs, three ASR transcripts), `hermes-reports/M1-slot23-speech-r3-freshclone/`
(the same probe replayed from a clean clone), and `hermes-reports/M1-slot23-review1.txt`
and `hermes-reports/M1-slot23-review2.txt` (two reviews that both failed and are
kept anyway).

## Licence, attribution, and what hosting means

VoiceStudio is AGPL-3.0. This fork keeps `LICENSE`, `LICENSE-NOTICE.md` and the
upstream attribution, and the same terms ride along: serving modified code to
users over a network means offering them the complete corresponding source,
build and install scripts included. Speech weights, voices and generated audio
carry their own licences. The app's licence grants nothing there. The Kokoro
bundle used above ships an Apache-2.0 LICENSE and its publisher declares
Apache-2.0; that is the publisher's statement, not a legal clearance, and no
model terms have been accepted on anyone's behalf.

Last reviewed: 31 October 2026.