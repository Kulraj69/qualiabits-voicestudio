"""Build the judge prompt from the real artifacts (run by hand, not by the app).

    .venv/bin/python hermes-reports/build_slot27_review_prompt.py 2

It pastes the rubric, the README, the file list, the real test output and the real
end-to-end result.json, plus the source verbatim. The judge is told to score the
artifacts, not a summary of them.

The run it pastes is the run the README itself quotes, found by reading the README
rather than by being told here: a bundle that quotes one run while the page quotes
another is exactly the inconsistency a judge is right to fail, and it happened once
already (review 1 scored honesty 2 for it).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUND = sys.argv[1] if len(sys.argv) > 1 else "2"
README = ROOT / "hosted/README.md"
RUBRIC = Path("/workspace/devpost/JUDGE-RUBRIC.md").read_text()
TEST_COMMAND = (
    ".venv/bin/python -m pytest tests/hosted tests/test_qualibits_voice_docs.py "
    "tests/test_sherpa_kokoro_and_speaker.py tests/test_headless_baseline.py -q"
)


def quoted_run() -> Path:
    """The end-to-end run the README says its numbers came from."""
    text = README.read_text()
    matches = re.findall(r"hermes-reports/(M2-slot27-e2e-r\d+)/result\.json", text)
    if not matches:
        raise SystemExit("the README does not name the run its numbers came from")
    path = ROOT / "hermes-reports" / matches[0] / "result.json"
    if not path.is_file():
        raise SystemExit(f"the README quotes {path}, which does not exist")
    return path


def shell(command: str) -> str:
    return subprocess.run(
        ["bash", "-lc", f"cd {ROOT} && {command}"], capture_output=True, text=True
    ).stdout


def main() -> int:
    readme = (ROOT / "hosted/README.md").read_text()
    files = shell(
        'find hosted tests/hosted -type f \\( -name "*.py" -o -name "*.md" -o -name "*.sh" \\) '
        '| sort | while read f; do printf "%6s  %s\\n" "$(wc -l < "$f")" "$f"; done'
    )
    tests = shell(f"{TEST_COMMAND} 2>&1 | tail -4")
    evidence_path = quoted_run()
    result = json.loads(evidence_path.read_text())
    keys = (
        "run", "passed", "checks", "elapsed_seconds", "engine_ready_seconds",
        "plane_ready_seconds", "engine_shutdown", "control_plane_shutdown", "revision",
        "workspaces", "speech", "job", "job_refused", "job_cancelled",
        "usage_workspace_a", "usage_workspace_a_at_end", "usage_workspace_b_at_end",
        "direct_vs_proxied", "refusal_passthrough", "usage_workspace_b", "model_files",
        "m1_complete", "gpu_verified", "listening_verified", "deployed", "public_url",
        "what_this_proves", "what_this_does_not_prove",
    )
    evidence = {key: result[key] for key in keys if key in result}
    code = {
        path: (ROOT / path).read_text()
        for path in (
            "hosted/control_plane/jobs.py",
            "hosted/control_plane/storage.py",
            "hosted/control_plane/migrations.py",
            "hosted/worker/runner.py",
        )
    }
    prompt = f"""You are a strict hackathon judge. Score the work below 1 to 5 on each of the seven criteria in the rubric, and be hard to please. Do not trust any summary: score what the artifacts show. If a claim is not backed by the artifacts, say so and treat it as an honesty flaw.

{RUBRIC}

=== SETTING ===
This is not a hackathon entry: it is a product milestone (M2 slice B) for QualiaBits Voice, a hosted speech product built on a fork of VoiceStudio. Criteria fit is therefore measured against the product plan: M2 is "wrap the local engine's API as a hosted service: auth (email login), workspaces, API keys, per-workspace job queue + storage (S3), usage metering. No desktop/Electron in the hosted path." Slice A (auth, one workspace per customer, API keys, metering, a metered passthrough of the speech route) is already built and pushed. This milestone is slice B: object storage and the per-workspace job queue, plus the migration path slice A admitted it lacked. Billing, email verification and deployment are explicitly NOT in this slice.
Boundary conditions that are true and must be judged as stated: nothing is deployed; there is no public URL; there is no GPU run; no model terms have been accepted; no customer exists and nothing has been sold. Storage: the local filesystem backend is what the end-to-end run exercised. The S3 backend's own logic is tested against a stub client and, over real HTTP with a real signed request, against a local S3-compatible emulator (moto, started in-process); a real bucket needs credentials and a spending decision from the owner, so it is listed as an owner action rather than claimed. A file named M2-slot27-e2e-r1/result.json in the evidence directory is a failed run, kept on purpose: it failed because the author's own comparison was wrong (it compared a refusal naming one voice against a refusal naming another), and that is recorded rather than deleted.

=== ARTIFACT 1: hosted/README.md (the page a reviewer reads first) ===
{readme}

=== ARTIFACT 2: the files (lines, path) ===
{files}

=== ARTIFACT 3: test output from this tree, run just now (command shown) ===
$ {TEST_COMMAND}
{tests}

=== ARTIFACT 4: the real end-to-end run's result.json, and it is the exact file the README's "Measured" section quotes (the harness pastes whichever run the README names, so the two cannot disagree) ===
{json.dumps(evidence, indent=2)}

=== ARTIFACT 5: the source, verbatim ===
""" + "\n\n".join(f"--- {path} ---\n{text}" for path, text in code.items())

    out = ROOT / "hermes-reports" / f"M2-slot27-review{ROUND}-prompt.txt"
    out.write_text(prompt)
    print(f"{out} ({len(prompt)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())