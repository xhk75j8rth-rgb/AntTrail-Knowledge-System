#!/usr/bin/env python
from __future__ import annotations

import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser(description="V0 card generator stub.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--job-dir")
    args = parser.parse_args()
    print(json.dumps({
        "ok": False,
        "implemented": False,
        "stage": "generate_card",
        "input": args.input,
        "job_dir": args.job_dir,
        "used_mcp": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
