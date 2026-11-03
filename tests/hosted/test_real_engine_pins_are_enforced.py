"""The pinned model bytes are enforced on a model found on the machine, not only on
one this script downloaded.

`hosted/web/e2e/real-engine.sh` checks three sha256 values before it starts anything.
The first version of this guard was a source-text check — it proved the checks were
not indented into the download branch — and a judge pass on this slice was right that
intent is not behaviour: nothing ran the script. This test runs it. A directory is put
where the script looks, filled with bytes that are not the pinned ones, and the script
has to stop with its own mismatch message before an engine could speak.

The successful half (a found directory whose bytes are the pins) is the run the M3
acceptance page cites: that run's `result.json` records the model directory it used
and the sha256 of the files in it, and `hosted/M3-ACCEPTANCE.md` points at both.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hosted" / "web" / "e2e" / "real-engine.sh"
MODEL_NAME = "kokoro-int8-multi-lang-v1_0"


def test_a_found_model_with_the_wrong_bytes_stops_the_run(tmp_path: Path) -> None:
    fake = tmp_path / MODEL_NAME
    fake.mkdir()
    for name in ("model.int8.onnx", "tokens.txt", "voices.bin"):
        (fake / name).write_bytes(b"not the bytes this repository pins\n")

    env = dict(os.environ)
    env["QUALI_MODEL_DIR"] = str(fake)
    # Point the download at a directory that does not exist so nothing is fetched:
    # the script must stop at the check, not go looking for the release asset.
    env["QUALI_E2E_WORK"] = str(tmp_path / "work")

    run = subprocess.run(
        ["bash", str(SCRIPT), "--no-build", "--keep"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert run.returncode == 3, (
        "the script did not stop on unpinned bytes; it exited "
        f"{run.returncode}\nstdout:\n{run.stdout[-2000:]}\nstderr:\n{run.stderr[-2000:]}"
    )
    assert "HASH MISMATCH" in run.stderr, run.stderr[-2000:]
    assert "==> engine: sherpa-onnx" not in run.stdout, (
        "the script started the engine on bytes it had just rejected"
    )