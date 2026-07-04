#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_STATUS_PATH = PROJECT_ROOT / "runtime" / "browser_sessions" / "toutiao_session.json"
TOUTIAO_HOME = "https://www.toutiao.com/"
BROWSER_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def default_profile_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home() / "AppData" / "Local"
    return base / "LucasKnowledgeDB" / "browser_profiles" / "toutiao"


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def write_status(payload: dict[str, Any]) -> None:
    SESSION_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def find_browser(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    for candidate in BROWSER_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def is_safe_toutiao_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return parsed.scheme in {"http", "https"} and (host == "toutiao.com" or host.endswith(".toutiao.com"))


def browser_args(browser: Path, profile_dir: Path, url: str) -> list[str]:
    return [
        str(browser),
        f"--user-data-dir={profile_dir}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--lang=zh-CN",
        url,
    ]


def open_toutiao(args: argparse.Namespace) -> int:
    url = args.url or TOUTIAO_HOME
    payload: dict[str, Any] = {
        "ok": False,
        "used_mcp": False,
        "stage": "toutiao_session_open",
        "status": "not_started",
        "url": url,
        "profile_dir": "",
        "browser": "",
        "status_path": str(SESSION_STATUS_PATH),
        "error": None,
    }

    if not args.allow_any_url and not is_safe_toutiao_url(url):
        payload.update({
            "status": "url_rejected",
            "error": "URL must be under toutiao.com unless --allow-any-url is passed.",
        })
        write_status(payload)
        emit_json(payload)
        return 2

    browser = find_browser(args.browser_path)
    if not browser:
        payload.update({
            "status": "browser_unavailable",
            "error": "No supported Edge/Chrome executable found.",
        })
        write_status(payload)
        emit_json(payload)
        return 1

    profile_dir = Path(args.profile_dir).expanduser() if args.profile_dir else default_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    cmd = browser_args(browser, profile_dir, url)
    payload.update({
        "browser": str(browser),
        "profile_dir": str(profile_dir),
        "status": "dry_run" if args.dry_run else "opening_browser",
        "opened_at": now_iso() if not args.dry_run else "",
        "command_preview": [cmd[0], "--user-data-dir=<local-toutiao-profile>", *cmd[2:]],
    })

    if args.dry_run:
        payload["ok"] = True
        write_status(payload)
        emit_json(payload)
        return 0

    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=False,
            creationflags=creationflags,
        )
    except Exception as exc:
        payload.update({
            "ok": False,
            "status": "browser_open_failed",
            "error": f"{type(exc).__name__}: {exc}",
        })
        write_status(payload)
        emit_json(payload)
        return 1

    payload.update({
        "ok": True,
        "status": "browser_opened",
        "next_step": "Log in manually in the opened Toutiao browser window. Cookies stay inside the local browser profile.",
    })
    write_status(payload)
    emit_json(payload)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Open a persistent local Edge/Chrome session for Toutiao login; no MCP and no cookie export."
    )
    subparsers = parser.add_subparsers(dest="command")

    open_parser = subparsers.add_parser("open", help="Open Toutiao in a persistent local browser profile.")
    open_parser.add_argument("--url", default=TOUTIAO_HOME)
    open_parser.add_argument("--browser-path")
    open_parser.add_argument("--profile-dir", default=os.environ.get("TOUTIAO_BROWSER_PROFILE", ""))
    open_parser.add_argument("--dry-run", action="store_true", help="Validate paths and print the launch plan without opening a browser.")
    open_parser.add_argument("--allow-any-url", action="store_true", help="Allow opening a non-Toutiao URL in this profile.")
    open_parser.set_defaults(func=open_toutiao)

    args = parser.parse_args()
    if not args.command:
        args = parser.parse_args(["open"])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
