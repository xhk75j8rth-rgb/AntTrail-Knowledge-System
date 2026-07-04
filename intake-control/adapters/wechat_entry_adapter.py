from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from chat_gateway.link_extractor import extract_urls
from chat_gateway.message_router import route_message
from chat_gateway.message_schema import MessageEvent
from chat_gateway.response_schema import HandlerResponse


def emit_text(text: str) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(text)


def emit_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def read_user_environment_variable(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value) if value else None
    except Exception:
        return None


def build_runner_env() -> dict[str, str]:
    env = os.environ.copy()
    token = read_user_environment_variable("SIYUAN_TOKEN")
    if token and not env.get("SIYUAN_TOKEN"):
        env["SIYUAN_TOKEN"] = token
    return env


def read_message(args: argparse.Namespace) -> str:
    if args.message is not None:
        return args.message
    if args.message_file:
        return Path(args.message_file).read_text(encoding="utf-8")
    return sys.stdin.read()


def message_to_event(message: str, args: argparse.Namespace) -> MessageEvent:
    return MessageEvent.from_text(
        message,
        channel="wechat",
        conversation_id=args.conversation_id,
        sender_id=args.sender_id,
        sender_name=args.sender_name,
        metadata={
            "adapter": "wechat_entry_adapter",
            "source": "cli-wechat-bridge",
        },
    )


def response_to_legacy_payload(response: HandlerResponse, event: MessageEvent) -> dict[str, Any]:
    payload = response.to_dict()
    payload["reply"] = response.reply_text
    payload["stage"] = response.status
    payload["used_mcp"] = False
    payload["message_event"] = event.to_dict()
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WeChat bridge adapter for the generic Chat Gateway.")
    parser.add_argument("--message", help="Raw WeChat text message.")
    parser.add_argument("--message-file", help="Read raw WeChat text from a UTF-8 file.")
    parser.add_argument("--timeout-sec", type=int, default=900)
    parser.add_argument("--extract-only", action="store_true", help="Only extract URL; do not call run_link_job.py.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON instead of only the WeChat reply text.")
    parser.add_argument("--conversation-id", default="wechat-bridge")
    parser.add_argument("--sender-id", default="wechat-user")
    parser.add_argument("--sender-name", default="WeChat User")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    message = read_message(args)
    event = message_to_event(message, args)
    response = route_message(
        event,
        dry_run=bool(args.extract_only),
        timeout_sec=args.timeout_sec,
        runner_env=build_runner_env(),
    )

    if args.json:
        payload = response_to_legacy_payload(response, event)
        if args.extract_only:
            payload["extract_only"] = True
            payload["urls"] = extract_urls(message)
        emit_json(payload)
    else:
        emit_text(response.reply_text)
    return 0 if response.ok else 1


if __name__ == "__main__":
    sys.exit(main())
