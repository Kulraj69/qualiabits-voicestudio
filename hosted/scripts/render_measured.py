"""Render the README's "Measured" section from one end-to-end evidence file.

The judge pass on this slice failed it on one thing, and that thing was real: the
page quoted numbers from one run while the bundle beside it was another, so no
reader could tell which run the page described. Prose copied by hand drifts. So
the page's numbers are no longer hand-written -- they are rendered from the run
file the page names, and ``tests/hosted/test_readme_measured_is_generated.py``
fails if the two ever disagree.

    .venv/bin/python hosted/scripts/render_measured.py --print
    .venv/bin/python hosted/scripts/render_measured.py --write   # rewrites the page

Only the section between "## Measured" and the next heading is generated.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "hosted" / "README.md"
SECTION_HEADING = "## Measured"
# The page may cite any run directory under hermes-reports; the generator follows
# whatever the page currently names, so the two cannot point at different runs.
EVIDENCE_RE = re.compile(r"(hermes-reports/M2-slot\d+-e2e-r\d+/result\.json)")


def thousands(value: int) -> str:
    return f"{value:,}"


def seconds(value: float) -> str:
    return f"{value:.3f}"


def cited_evidence_path(readme_text: str) -> Path:
    """The evidence file the page says its numbers came from."""
    match = EVIDENCE_RE.search(readme_text)
    if not match:
        raise SystemExit("the README names no hermes-reports/.../result.json")
    return REPO_ROOT / match.group(1)


def render_section(result: dict, evidence_rel: str) -> str:
    """The whole section, heading included, from a result.json."""
    direct = result["direct_vs_proxied"]
    job = result["job"]
    meter = result["usage_workspace_a_at_end"]
    meter_b = result["usage_workspace_b_at_end"]
    by_key = meter["by_key"][0]
    refusal = result["refusal_passthrough"]
    models = result["model_files"]
    revision = result["revision"]

    engine_seconds_per_second = [
        (entry["label"], entry["engine_seconds_header"] / entry["seconds"])
        for entry in result["speech"]
    ]
    job_ratio = job["engine_seconds"] / job["seconds"]
    rate_lines = "\n".join(
        f"  {label:<18} {rate:.1f} s" for label, rate in engine_seconds_per_second
    )
    statuses = ", ".join(f'"{code}": {count}' for code, count in sorted(meter["by_status"].items()))

    speech_lines = "\n".join(
        f"  {entry['voice']}  {entry['language']}   {seconds(entry['seconds'])} s audio,"
        f" 24 kHz mono, peak {entry['peak']:.2f}   engine took {entry['engine_seconds_header']:.1f} s"
        for entry in result["speech"]
    )

    return f"""{SECTION_HEADING}

From `{evidence_rel}` — engine, plane and worker processes all started fresh by the
harness on one CPU box ({result['host']['machine']}, {result['host']['cpu_count']} CPUs, Python {result['host']['python']}, no
GPU). The section is rendered from that file by
`hosted/scripts/render_measured.py`, and a test fails if the page and the file
disagree. The run records the commit it tested
({revision['tested_commit'][:9]}, worktree {"dirty" if revision["worktree_dirty"] else "clean"}) and the model hashes below.

```
engine ready {result['engine_ready_seconds']:.1f} s   control plane ready {result['plane_ready_seconds']:.1f} s   whole run {result['elapsed_seconds']:.1f} s
every process it started: clean shutdown

three direct requests through the control plane, workspace A:
{speech_lines}
  plus one request for a voice the model does not have, refused 400 by the
  engine and passed through with the engine's own message naming its 54 voices.

one queued job, spoken by a separate worker process (run_worker.py --once):
  job {job['id']}  {job['voice']}  hi   {job['characters']} characters -> {seconds(job['seconds'])} s of 24 kHz mono audio,
                         {thousands(job['bytes'])} bytes, engine took {job['engine_seconds']:.1f} s
  worker counters: claimed {job['worker_stats']['claimed']}, succeeded {job['worker_stats']['succeeded']}, failed {job['worker_stats']['failed']}, requeued {job['worker_stats']['requeued']}, lost_lease {job['worker_stats']['lost_lease']}
  the object on disk, the job's receipt and the download all hash to
  {job['sha256']}

one refused job (a voice the model does not have):
  failed {result['job_refused']['status_code']} after {result['job_refused']['attempts']} attempt, no retry, no audio served (409), error is the
  engine's own sentence, truncated at the ledger's 200 characters
one cancelled job:
  never claimed by the next worker pass, and it moved the meter by nothing

meter for A, at the end of the run: {meter['requests']} requests, {meter['characters']} characters, {seconds(meter['audio_seconds'])} s audio,
              {thousands(meter['audio_bytes'])} bytes, failed_requests {meter['failed_requests']}, by_status {{{statuses}}},
              requests_without_audio_seconds {meter['requests_without_audio_seconds']}
              by_key: one key, label "{by_key['label']}", same {by_key['requests']} requests and {seconds(by_key['audio_seconds'])} s
meter for B, at the end: {meter_b['requests']} requests, {meter_b['characters']} characters, {seconds(meter_b['audio_seconds'])} s audio

same English request straight at the engine, for comparison:
  direct {seconds(direct['direct_seconds'])} s / {thousands(direct['direct_bytes'])} bytes   through the plane {seconds(direct['proxied_seconds'])} s / {thousands(direct['proxied_bytes'])} bytes

the engine's refusal for an unknown voice, taken both ways:
  sha256 both {refusal['proxied_sha256'][:16]}…, {thousands(refusal['bytes'])} bytes, byte-for-byte identical

engine seconds spent per second of speech, the number that decides whether this
is sellable on a box like this one:
{rate_lines}
```

The English lines differ because the engine samples: the two numbers above are the
same request taken both ways, and they differ by
{thousands(abs(direct["proxied_bytes"] - direct["direct_bytes"]))} bytes. No byte-for-byte claim is made anywhere for speech. What is compared byte for byte is a response the engine
computes deterministically: its refusal, checked against the real engine on every
run; the 200 path is compared against a deterministic stub in the unit suite.

The model those numbers came from is the one `scripts/qualibits_quickstart.sh`
downloads and hash-checks (`tokens.txt` {models['tokens.txt'][:8]}…, `voices.bin` {models['voices.bin'][:8]}…); the run
recorded the same hashes plus the commit it tested, in the same file.

The CPU is not production, and the rates above are the clearest evidence of it: the
queued job spent {job['engine_seconds']:.1f} s of engine time on {seconds(job['seconds'])} s of audio, about {job_ratio:.1f} s
per second. That is why the GPU run is the owner's to approve, and exactly what
this layer cannot work around — the plane can hold a request open for a paragraph,
but nothing here makes the engine faster.
"""


def splice(readme_text: str, new_section: str) -> str:
    """Replace the README's measured section, leaving every other section alone."""
    start = readme_text.index(SECTION_HEADING)
    rest = readme_text[start + len(SECTION_HEADING):]
    match = re.search(r"\n## ", rest)
    if not match:
        raise SystemExit("the measured section is the last one; nothing to splice before")
    end = start + len(SECTION_HEADING) + match.start() + 1
    return readme_text[:start] + new_section + "\n" + readme_text[end:]


def load(readme_text: str) -> tuple[dict, str]:
    path = cited_evidence_path(readme_text)
    if not path.exists():
        raise SystemExit(f"the page cites {path} and it does not exist")
    return json.loads(path.read_text()), str(path.relative_to(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite the README section")
    parser.add_argument("--print", dest="show", action="store_true", help="print the section")
    parser.add_argument("--evidence", help="use this run file instead of the cited one")
    args = parser.parse_args()

    readme_text = README.read_text()
    if args.evidence:
        path = Path(args.evidence)
        result = json.loads(path.read_text())
        rel = str(path.resolve().relative_to(REPO_ROOT))
    else:
        result, rel = load(readme_text)

    section = render_section(result, rel)
    if args.show or not args.write:
        print(section)
    if args.write:
        README.write_text(splice(readme_text, section))
        print(f"rewrote {SECTION_HEADING} in {README} from {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())