#!/usr/bin/env python3
"""Build the cheap+ judge prompt for M3 slice A from the real artifacts.

The judge is given the artifacts, not a summary of them, and is told not to
trust the round log. Everything below is read from the repository at build
time: the README the reviewer would read, the app's own file list with line
counts, the browser end-to-end result.json (trimmed to its assertions and the
engine's own answers), the suite logs, the commits, and the rubric.

    python hermes-reports/build_m3_review_prompt.py [round-label] > prompt.txt

A missing artifact refuses the bundle instead of leaving it out silently,
because a judge scoring an incomplete bundle scores the wrong thing.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REPORTS = REPO / "hermes-reports"
RUBRIC = Path("/workspace/devpost/JUDGE-RUBRIC.md")
WEB = REPO / "hosted" / "web"


def show(path: Path, *, head: int | None = None) -> str:
    if not path.exists():
        raise SystemExit(f"missing artifact: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if head is not None:
        return "\n".join(text.splitlines()[:head])
    return text


def file_list(root: Path, limit: int = 120) -> str:
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in {"node_modules", ".next", ".evidence", ".git"} for part in rel.parts):
            continue
        try:
            lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            lines = 0
        rows.append(f"{rel}  ({lines} lines)")
        if len(rows) >= limit:
            rows.append("… truncated")
            break
    return "\n".join(rows)


def git_log(n: int = 8) -> str:
    out = subprocess.run(
        ["git", "-C", str(REPO), "log", "--oneline", f"-{n}"],
        capture_output=True, text=True, check=False,
    )
    return out.stdout.strip()


def e2e_summary(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"missing end-to-end result: {path}")
    data = json.loads(path.read_text())
    keep = {
        k: data.get(k)
        for k in ("run", "revision", "engine", "engine_command", "engine_is_stub", "engine_health",
                  "pages",
                  "engine_active", "engine_voice_count", "engine_model", "engine_model_source", "browser",
                  "deployed", "public_url", "gpu", "real_s3", "audio", "usage")
    }
    lines = ["result.json (engine and run facts):", json.dumps(keep, indent=2)]
    lines.append(f"\nchecks: {data.get('passed')} passed, {data.get('failed')} failed, "
                 f"ok={data.get('ok')}")
    for check in data.get("checks", []):
        detail = str(check.get("detail") or "")
        detail = detail[:160] + ("…" if len(detail) > 160 else "")
        lines.append(f"  [{'PASS' if check['ok'] else 'FAIL'}] {check['name']}"
                     + (f"  ({detail})" if detail else ""))
    return "\n".join(lines)


def milestone() -> str:
    """The owner's milestone this slice is measured against.

    A product milestone has no hackathon rules file, so the judge is given the
    milestone it is graded against instead: the V0 goal and its step-by-step
    plan from the queue this work comes from. Only those lines — the queue also
    carries status history, which is not a criterion.
    """
    queue = Path.home() / ".hermes" / "context" / "devpost-projects.md"
    if not queue.exists():
        return "the queue file (with the V0 milestone) is not readable from here"
    lines = queue.read_text(encoding="utf-8", errors="replace").splitlines()
    goal = next((line for line in lines if line.startswith("## V0 TOP GOAL")), None)
    start = next((i for i, line in enumerate(lines) if line.startswith("V0 STEP-BY-STEP PLAN")), None)
    if goal is None or start is None:
        return "the queue has no V0 goal or plan section"
    plan: list[str] = [lines[start]]
    for line in lines[start + 1:start + 16]:
        if line.startswith("Rules:") or line.startswith("M6") or line.startswith("M7"):
            plan.append(line)
        elif re.match(r"^M[1-5] ", line):
            plan.append(line)
    return goal[:1200] + "\n\n" + "\n".join(plan)


def since_text() -> str:
    """What was fixed after each previous pass, in pass order.

    One file per pass (`M3-slot44-since-review<N>.txt`), because the response to
    pass 2 is not a replacement for the response to pass 1: a judge checking D8
    needs to see what each pass found and what happened to it.
    """
    def pass_number(path: Path) -> int:
        match = re.search(r"review(\d+)", path.name)
        return int(match.group(1)) if match else 0

    files = sorted(REPORTS.glob("M3-slot44-since-review*.txt"), key=pass_number)
    if not files:
        return "nothing: this is the first pass on the slice"
    return "\n\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def review_files(limit: int = 3) -> str:
    """The judge passes already run on this slice, so D8 is checkable."""
    found = sorted(REPORTS.glob("M3-slot44-review*.txt"))
    if not found:
        return "no judge pass is committed yet; this is the first"
    return "\n\n".join(f"### {p.name}\n" + p.read_text(encoding="utf-8", errors="replace")
                       for p in found[-limit:])


def stub_summary(path: Path) -> str:
    """The stub smoke run, so the clone-friendly path is checkable too."""
    if not path.exists():
        raise SystemExit(f"missing stub end-to-end result: {path}")
    data = json.loads(path.read_text())
    lines = [
        f"{path.name}: {data.get('passed')} passed, {data.get('failed')} failed, "
        f"ok={data.get('ok')}, engine={data.get('engine')} "
        f"(engine_is_stub={data.get('engine_is_stub')})",
        f"revision: {json.dumps(data.get('revision'))}",
        f"pages: {json.dumps(data.get('pages'))}",
        f"audio: {json.dumps(data.get('audio'))}",
        f"usage: {json.dumps(data.get('usage'))}",
    ]
    for check in data.get("checks", []):
        if not check["ok"]:
            lines.append(f"  [FAIL] {check['name']}  ({str(check.get('detail'))[:160]})")
    return "\n".join(lines)


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "round1"
    evidence = REPORTS / "M3-slot44-e2e-r4" / "result.json"
    stub = REPORTS / "M3-slot44-e2e-stub-r3.json"
    tests = REPORTS / "M2-slot44-clean-tests-r1.log"
    unit = REPORTS / "M3-slot44-web-unit-r1.log"

    parts = [
        f"# Judge pass {label} on M3 slice A (the browser dashboard), cheap+ tier\n",
        "You are a strict hackathon judge. Score the seven criteria of the rubric "
        "below, 1 to 5 each, and pass only if every score is 4 or more with no "
        "honesty flaw. The artifacts are supplied; the builder's own round log is "
        "NOT supplied and must not be asked for — judge what is here.\n",
        "## Rubric\n", show(RUBRIC), "\n",
        "## The milestone this is measured against (criteria fit)\n",
        "There is no hackathon rules file for a product milestone, so criterion 5 "
        "is measured against the owner's own milestone below, quoted from the "
        "queue this work comes from. Score criteria fit against it, and say which "
        "of its items have no evidence here.\n\n",
        milestone(), "\n",
        "## What the slice claims to be (D1-D8, written before any code)\n",
        "D1 a real Next.js app in its own package at hosted/web/ that signs up and "
        "signs in against the real control plane, session token in an httpOnly "
        "cookie never readable by page JavaScript.\n"
        "D2 the Speak page really speaks: text, a voice from the engine's own "
        "catalogue, language; WAV back through the plane, plays and downloads; the "
        "voice list is served by the plane (GET /v1/voices), not hard-coded; an "
        "unknown voice is refused with the engine's own sentence.\n"
        "D3 keys and usage are real: shown once, listed as public id only, revoked "
        "key refused on the next request, usage page shows workspace totals and the "
        "per-key breakdown.\n"
        "D4 jobs are real: queue a paragraph, watch it move to done, play and "
        "download the stored audio; a cancelled job never speaks and never moves "
        "the meter.\n"
        "D5 tested at two levels: Python tests for the new plane route, and a "
        "committed browser end-to-end driving real headless Chromium against the "
        "real engine, the real plane and the real web server; passes from a clean "
        "clone of the pushed revision.\n"
        "D6 it looks designed: dark first, one accent, every page one click from "
        "home, no dead links, keyboard focus visible.\n"
        "D7 hosted/web/README.md and the hosted/README.md pointer are do-X-see-Y "
        "for a stranger with the honest boundary kept.\n"
        "D8 two cheap+ judge rounds on the real artifacts, every fixable flaw "
        "fixed, unfixable ones written down; originality is expected to stay "
        "below 4 because a dashboard is not a novel idea and the differentiator is "
        "the owner's decision.\n",
        "## The README a reviewer reads first (hosted/web/README.md, full)\n",
        show(WEB / "README.md"), "\n",
        "## The hosted layer's README (hosted/README.md, first 120 lines)\n",
        show(REPO / "hosted" / "README.md", head=120), "\n",
        "## The app's files (excluding node_modules, .next, .evidence)\n",
        file_list(WEB), "\n",
        "## The end-to-end, real engine, this revision (fresh clone, --keep)\n",
        e2e_summary(evidence), "\n",
        "## The end-to-end, stub engine (the default smoke path)\n",
        stub_summary(stub), "\n",
        "## Suite results, from a clean clone of the pushed revision\n",
        show(tests) if tests.exists() else "missing clean-clone suite log",
        "\n",
        "## Web unit tests (node --test)\n",
        show(unit) if unit.exists() else "missing web unit log",
        "\n",
        "## Commits in this slice\n", git_log(), "\n",
        "## The judge passes already run on this slice (D8)\n",
        "These are the independent cheap+ passes on this slice, verbatim, so that "
        "D8's 'two rounds, every fixable flaw fixed' can be checked rather than "
        "believed.\n\n",
        review_files(), "\n",
        "## What changed after the pass above\n",
        since_text(), "\n",
        "## What is NOT claimed\n",
        "Nothing is deployed and there is no public URL (deployed=false, "
        "public_url=null in the run). There is no real S3 bucket, only the local "
        "object store. There is no GPU. No human has listened to the audio. M1 "
        "(real GPU run and a listening check) and M2 (deployment, bucket, a "
        "product differentiator) stay open and are the owner's decisions. The "
        "engine on CPU runs at roughly a seventh of real time.\n",
        "## Output format\n",
        "For each of the seven criteria: score and two or three sentences of "
        "evidence from the artifacts. Then the five most damaging flaws with file "
        "or line references. Then one sentence on what would make it win. Say "
        "plainly which D items are demonstrated, partly demonstrated, or not.\n",
    ]
    sys.stdout.write("\n".join(parts))


if __name__ == "__main__":
    main()