# V0 / M1 fork evidence

Status: M1 incomplete; V0 not live. Canonical cross-repository continuation log:
/workspace/qualiabits-voice/hermes-reports/V0-rounds.md.

## Plan (recorded before implementation in the canonical log)

M1 requires unchanged VoiceStudio on approved spot GPU, actual English/Hindi
speech via its API and architecture notes. This run builds only a reproducible
CPU empty-cache diagnostic while GPU access/model rights are blocked. Test the
real unchanged backend, judge with /workspace/devpost/JUDGE-RUBRIC.md on cheap+,
fix findings, repeat in a fresh source copy. Do not claim GPU/audio completion.

## Round 1

Frozen CPU `uv sync --frozen --no-default-groups --group cpu --group dev
--python /usr/bin/python3.12` succeeded (M1-install.log).
Actual unmodified backend booted on loopback; initial health 503 then 200,
CPU device. Catalogs: 42 models, zero installed; TTS/ASR/LLM entries 19/12/2.
English and Hindi synthesis requests both returned HTTP 403
model_licence_required. No audio generated, weights downloaded or terms accepted.

`HF_HUB_OFFLINE=1 HF_HUB_CACHE=<empty scratch cache> .venv/bin/python -m pytest
-q --tb=short tests/test_loopback_server_mode.py tests/test_api.py
tests/test_model_manager_preload.py tests/test_engine_language_rejection.py`
-> 151 passed, 1 xfailed, 7 warnings (M1-round1-upstream-tests.log).
`pytest -q --tb=short tests/test_headless_baseline.py` -> 15 passed
(M1-round1-harness-tests.log).
`.venv/bin/python scripts/headless_baseline.py --output hermes-reports/M1-round1-http`
-> all seven HTTP checks reached, overall false because SIGTERM was unclassified.
Raw result and server log retained. Startup loop-stall warnings are observed,
not fixed. Test fixtures have fake engines; actual HTTP harness does not.

Cheap+ review: /workspace/qualiabits-voice/hermes-reports/M1-review1.md.
Scores originality/taste/human/depth/fit/path/honesty: chunk 1/3/4/3/1/4/5;
full M1 1/2/3/1/1/2/5. FAIL. No false done claim.

Round 2 fixes: classify Uvicorn's re-raised SIGTERM only with both shutdown
markers, normal process liveness before stop, no forced kill; add regressions.
Make evidence scope and m1_complete=false explicit. Reject checkout/ancestor
.env presence without reading it. Preserve first failed output. Startup stalls,
GPU synthesis and legal/spend gates remain real M1 blockers. No assertion that
making the negative diagnostic pass makes M1 pass.

AWS read-only discovery: credential provider iam-role exists; Mumbai EC2
DescribeInstances returns UnauthorizedOperation. No provisioning attempted.
Default model inventory says noncommercial weights and unresolved tokenizer
terms. Owner must select/clear an appropriate engine and accept required terms.
See docs/ARCHITECTURE-NOTES.md for exact next steps and architecture.

## Rotation continuation — 2026-10-29, V0-build (first slot)

Rotation history was absent, so this run starts the declared cycle at slot 1.
Resume the existing M1 work; do not substitute M2 or agent work for gated M1.
Definition of done remains actual English/Hindi API synthesis on an approved
spot GPU, documented architecture, passing tests and independent review.
The local deliverable is narrower: finish and publish the diagnostic branch,
without marking the milestone done. Existing uncommitted M1 work is retained.

Plan before edits: round 3 adversarial harness tests and real HTTP run, cheap+
review against the full rubric; fix concrete local findings. Round 4 repeats
from a fresh source copy and frozen install, then a second cheap+ review.
Target honesty, reviewer reproducibility and technical correctness. Keep source
hashes, preserve failure evidence, and explicitly label every failure result
as not M1/speech/GPU acceptance. Review all staged content for secrets before
pushing the existing authorized fork branch; read back the exact remote SHA.
Update the canonical V0 log, queue, summary and rotation history. No cloud
provisioning, model acceptance, provider calls, messages or public demo upload.

## Round 3 — resumed failure-path regression and actual HTTP

`HF_HUB_OFFLINE=1 HF_HUB_CACHE=$TMPDIR/v0-m1-rotation-empty-cache
.venv/bin/python -m pytest -q --tb=short tests/test_headless_baseline.py`
reproduced missing explicit scope/M1 flags on startup failure: 1 failed,
28 passed (M1-round3-before.log). Initialize all acceptance flags before startup.
The same command with the four upstream test files listed in the architecture
note then passed: 180 passed, 1 xfailed, 3 warnings, 24.86s
(M1-round3-tests.log). This command DOES include the harness regression.
Ruff executable absent in fork venv; retry with
`/workspace/qualiabits-voice/.venv/bin/ruff check scripts/headless_baseline.py
tests/test_headless_baseline.py` passed, exit 0 (M1-round3-lint-retry.log).
`.venv/bin/python scripts/headless_baseline.py --output hermes-reports/M1-round3-http`
passed, exit 0: seven actual HTTP contract checks, CPU, both speech attempts
403, no audio, shutdown verified; startup stall explicitly warned.

Cheap+ review: /workspace/qualiabits-voice/hermes-reports/M1-review3.md.
Full M1 scores 1/2/3/2/1/3/5, FAIL. Concrete fix: move process creation into
the failure-report/cleanup scope and test spawn errors. Added interruption
and SystemExit regressions: the existing finally block already owns these
paths, contrary to the review's claim that ordinary Exception handling bypasses
finally. No signal/SIGKILL guarantee is claimed. Socket/file setup failures
before child creation may exit without structured evidence; no child exists
then. Repeated interrupts during cleanup remain outside this diagnostic's
guarantees. Startup responsiveness is explicitly not production acceptance;
changing upstream imports would contradict this M1 unchanged-server baseline.
No clean/full-suite claim: upstream warnings and xfail remain visible.

Round 4 will record exact commands/exit codes and individual harness results
from fresh source, plus an unchanged-upstream-files comparison. GPU/model gates
are not local code defects and cannot be scored as passed.

## Round 4 — fresh source, frozen CPU install, adversarial cleanup

Fresh copy: /home/ubuntu/.hermes/cache/scratch/v0-m1-rotation-round4.
M1-round4-manifest.json records all copied file hashes and base revision;
README is the only changed pre-existing tracked file. New files are the
diagnostic script/tests and architecture notes. No backend/lockfile change.
Exact commands, cwd and exit codes are in M1-round4-{install,harness,tests,lint,http}.log.
Frozen uv CPU/dev install exit 0; verbose harness 32 passed; combined targeted
tests 183 passed, 1 xfailed, 7 warnings; Ruff exit 0. Actual server HTTP exit 0,
seven contract checks, both languages refused 403, CPU, shutdown verified,
startup stall warning. No audio/GPU/production evidence. Source hashes match
the fresh-copy manifest. M1-round4-upstream-warning.log independently reproduces
the upstream unawaited coroutine without harness imports: 52 passed, 1 xfailed,
3 warnings. No full-upstream-suite or warning-free claim.

Cheap+ final review: /workspace/qualiabits-voice/hermes-reports/M1-review4.md.
Full M1 scores 1/2/3/3/1/3/5: FAIL. Diagnostic engineering assessment:
"correct for its declared CPU API-contract scope". No new in-scope code defect
was identified. Startup stalls, upstream warning and non-supervisor limits are
documented, not waived production passes. Full M1 remains blocked, not done.
No further code changes after the fresh-copy run. Architecture/log updates
afterward do not change the tested script or backend hashes.

Needs-Kulraj exact action: "Provide an approved g5/g6 spot target with scoped
runtime access, automatic idle shutdown and a hard spending limit. Select an
English/Hindi-capable engine whose weights, tokenizer and output terms permit
the intended service, and complete any required owner acceptance. Then approve
the bounded M1 GPU run and listening check; supply configuration privately."

Do not start M2 while M1 is blocked. No GPU provisioned, model weights fetched,
terms accepted, provider call, message, deployment or public demo upload.
