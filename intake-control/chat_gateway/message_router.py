from __future__ import annotations

from typing import Mapping

from chat_gateway.config_preflight import attach_config_preflight
from chat_gateway.handlers import command_handler, fallback_handler, link_handler
from chat_gateway.link_extractor import extract_links
from chat_gateway.message_schema import MessageEvent
from chat_gateway.response_schema import HandlerResponse


def route_message(
    event: MessageEvent,
    *,
    dry_run: bool = False,
    timeout_sec: int = 900,
    runner_env: Mapping[str, str] | None = None,
) -> HandlerResponse:
    command_response = command_handler.maybe_handle(event)
    if command_response is not None:
        return command_response

    links = extract_links(event.text)
    if links.primary_url:
        response = link_handler.handle(
            event,
            links,
            dry_run=dry_run,
            timeout_sec=timeout_sec,
            runner_env=runner_env,
        )
        return attach_config_preflight(response, include_ai=True, include_storage=True, runner_env=runner_env)

    response = fallback_handler.handle(event)
    return attach_config_preflight(response, include_ai=True, include_storage=False, runner_env=runner_env)
