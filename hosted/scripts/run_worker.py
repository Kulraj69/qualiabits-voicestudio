#!/usr/bin/env python3
"""Run one worker process against a control-plane database.

    QUALI_UPSTREAM_BASE_URL=http://127.0.0.1:3900 \
    QUALI_DATABASE_URL=sqlite:///$PWD/.hosted/control-plane.db \
    QUALI_STORAGE_DIR=$PWD/.hosted/objects \
    .venv/bin/python hosted/scripts/run_worker.py --once

``--once`` drains what is queued right now and exits, which is what the demo and the
end-to-end script use. Without it the process polls forever, which is what a
deployment runs. Each job's progress goes to stderr as it is worked; the last line
of stdout is the run's counters as JSON, so a script can read them without parsing
prose.

The worker creates the schema if the database has none, so it can be started before
or after the plane without either one depending on the other's start order. Run
both: ``hosted/scripts/hosted_demo.sh`` does exactly that.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hosted.control_plane.config import Settings  # noqa: E402
from hosted.worker.runner import Worker  # noqa: E402


def default_worker_id() -> str:
    """Enough to tell two workers apart in a log, and no secret in it."""
    return f"{socket.gethostname()[:32]}-{os.getpid()}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QualiaBits Voice job worker")
    parser.add_argument("--worker-id", default=default_worker_id())
    parser.add_argument(
        "--once",
        action="store_true",
        help="drain the queue and exit instead of polling forever",
    )
    parser.add_argument(
        "--poll", type=float, default=None, help="seconds between polls (default: settings)"
    )
    parser.add_argument(
        "--max-jobs", type=int, default=0, help="stop after this many jobs (0 = no limit)"
    )
    parser.add_argument(
        "--stats-file",
        default="",
        help="also write the counters as JSON here (the demo writes evidence)",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    worker = Worker(settings, args.worker_id)
    # Creating or migrating the schema here means the worker is usable on its own,
    # and is a no-op when the plane already did it.
    state = worker.db.init_schema(settings.schema_version)
    print(
        f"worker {args.worker_id}: schema {state}, upstream {settings.upstream_base_url}, "
        f"storage {worker.store.describe()['backend']}",
        file=sys.stderr,
        flush=True,
    )
    try:
        stats = await worker.serve(
            poll_seconds=args.poll, idle_exit=args.once, max_jobs=args.max_jobs
        )
    finally:
        await worker.aclose()
    payload = {"worker_id": args.worker_id, "mode": "once" if args.once else "serve", **stats.as_dict()}
    if args.stats_file:
        Path(args.stats_file).write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())