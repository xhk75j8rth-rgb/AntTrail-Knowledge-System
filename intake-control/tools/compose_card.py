#!/usr/bin/env python
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compatibility wrapper for tools/card_composer.py.")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--metadata-output", default="")
    parser.add_argument("--timeout-sec", type=int, default=120)
    parser.add_argument("--force-fallback", action="store_true", help="Ignored: strict composer never creates formal fallback cards.")
    args = parser.parse_args()

    job_dir = Path(args.job_dir).resolve()
    output_markdown = Path(args.output).resolve() if args.output else job_dir / "composed_card.md"
    output_json = Path(args.metadata_output).resolve() if args.metadata_output else job_dir / "composed_card.json"
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "card_composer.py"),
        "--job-dir",
        str(job_dir),
        "--output-json",
        str(output_json),
        "--output-markdown",
        str(output_markdown),
        "--timeout-sec",
        str(args.timeout_sec),
    ]
    completed = subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True)
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
