#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from intake_platforms import PLATFORM_RULES, open_authorization, platform_catalog  # noqa: E402


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Open or inspect local platform authorization sessions; no cookie export.")
    subparsers = parser.add_subparsers(dest="command")

    status_parser = subparsers.add_parser("status", help="Print platform routing and authorization status.")
    status_parser.add_argument("--url", default="")

    open_parser = subparsers.add_parser("open", help="Open a platform login/authorization page.")
    open_parser.add_argument("--platform", required=True, choices=sorted(PLATFORM_RULES.keys()))
    open_parser.add_argument("--url", default="")
    open_parser.add_argument("--browser-path")
    open_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.command == "status":
        emit(platform_catalog(args.url or None))
        return 0
    if args.command == "open":
        payload = open_authorization(
            args.platform,
            url=args.url or None,
            browser_path=args.browser_path,
            dry_run=bool(args.dry_run),
        )
        emit(payload)
        return 0 if payload.get("ok") else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
