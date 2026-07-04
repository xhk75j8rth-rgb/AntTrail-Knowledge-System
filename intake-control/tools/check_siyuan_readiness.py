#!/usr/bin/env python
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import urllib.error
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def token_status() -> dict[str, Any]:
    token = os.environ.get("SIYUAN_TOKEN") or ""
    return {
        "token_present": bool(token),
        "token_last4": token[-4:] if token else "",
    }


def siyuan_status() -> dict[str, Any]:
    config_path = PROJECT_ROOT / "config" / "link_pipeline.json"
    if not config_path.exists():
        config_path = PROJECT_ROOT / "config" / "link_pipeline.example.json"
    config = read_json(config_path)
    base_url = str(config.get("siyuan_base_url") or "http://127.0.0.1:6806").rstrip("/")
    health_url = f"{base_url}/api/system/bootProgress"
    req = urllib.request.Request(health_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            body = response.read().decode("utf-8", errors="replace")
        return {
            "base_url": base_url,
            "reachable": True,
            "status_code": 200,
            "response_preview": body[:120],
        }
    except urllib.error.HTTPError as exc:
        return {
            "base_url": base_url,
            "reachable": True,
            "status_code": exc.code,
            "response_preview": "",
        }
    except Exception as exc:
        return {
            "base_url": base_url,
            "reachable": False,
            "error": str(exc),
        }


def main() -> int:
    payload = {
        "token": token_status(),
        "siyuan": siyuan_status(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["siyuan"].get("reachable") else 1


if __name__ == "__main__":
    sys.exit(main())
