from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from chat_gateway.message_router import route_message
from chat_gateway.message_schema import MessageEvent


def emit_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def read_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.message_file:
        return Path(args.message_file).read_text(encoding="utf-8")
    return sys.stdin.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Debug CLI adapter for Chat Gateway messages.")
    parser.add_argument("--text", help="Message text to simulate.")
    parser.add_argument("--message-file", help="Read message text from a UTF-8 file.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call run_link_job.py or write to SiYuan.")
    parser.add_argument("--timeout-sec", type=int, default=900)
    parser.add_argument("--channel", default="debug_cli")
    parser.add_argument("--conversation-id", default="debug-room")
    parser.add_argument("--sender-id", default="debug-user")
    parser.add_argument("--sender-name", default="Debug User")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    text = read_text(args)
    event = MessageEvent.from_text(
        text,
        channel=args.channel,
        conversation_id=args.conversation_id,
        sender_id=args.sender_id,
        sender_name=args.sender_name,
        metadata={"adapter": "debug_cli_adapter", "dry_run": bool(args.dry_run)},
    )
    response = route_message(event, dry_run=bool(args.dry_run), timeout_sec=args.timeout_sec)
    emit_json({"event": event.to_dict(), "response": response.to_dict()})
    return 0 if response.ok else 1


if __name__ == "__main__":
    sys.exit(main())
