from __future__ import annotations

from chat_gateway.message_schema import MessageEvent
from chat_gateway.response_schema import HandlerResponse


def maybe_handle(event: MessageEvent) -> HandlerResponse | None:
    text = (event.text or "").strip()
    if not text.startswith("/"):
        return None

    command = text.split(maxsplit=1)[0].lower()
    if command in {"/help", "/start"}:
        return HandlerResponse(
            ok=True,
            reply_text="Chat Gateway 已连接。当前 V1 支持链接消息；普通消息会进入 fallback 占位处理。",
            status="command_help",
            handled_by="command_handler",
            data={"command": command},
        )

    if command == "/ping":
        return HandlerResponse(
            ok=True,
            reply_text="pong",
            status="command_ping",
            handled_by="command_handler",
            data={"command": command},
        )

    return HandlerResponse(
        ok=True,
        reply_text=f"未识别的命令：{command}。当前 V1 支持 /help 和 /ping。",
        status="command_unknown",
        handled_by="command_handler",
        data={"command": command},
    )
