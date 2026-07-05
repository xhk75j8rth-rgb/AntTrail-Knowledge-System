from __future__ import annotations

from typing import Any, Mapping

from ai_layer.provider_config import resolve_provider_config
from chat_gateway.response_schema import HandlerResponse
from storage_config import get_public_storage_config, resolve_storage_provider


AI_CONFIG_CODES = {"ai_api_key_missing", "ai_base_url_missing", "ai_model_missing"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _env_present(runner_env: Mapping[str, str] | None, name: str) -> bool:
    if not name:
        return False
    if runner_env is not None and _clean(runner_env.get(name)):
        return True
    return False


def _warning(code: str, title: str, message: str, action: str, *, target: str) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "warning",
        "target": target,
        "title": title,
        "message": message,
        "action": action,
    }


def _ai_status(runner_env: Mapping[str, str] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = resolve_provider_config()
    api_key_present = bool(config.api_key) or _env_present(runner_env, config.api_key_env)
    base_url_present = bool(_clean(config.base_url)) or config.protocol == "mock"
    model_present = bool(_clean(config.model)) or config.protocol == "mock"
    configured = config.protocol == "mock" or (api_key_present and base_url_present and model_present)
    api_key_source = config.api_key_source
    if not api_key_source and _env_present(runner_env, config.api_key_env):
        api_key_source = f"runner_env:{config.api_key_env}"
    status = {
        "provider_id": config.provider_id,
        "label": config.label,
        "kind": "ai",
        "protocol": config.protocol,
        "configured": configured,
        "api_key_present": api_key_present,
        "api_key_env": config.api_key_env,
        "api_key_source": api_key_source,
        "base_url_present": base_url_present,
        "base_url_label": config.base_url,
        "model": config.model,
        "supports_vision": config.supports_vision,
        "supports_json_mode": config.supports_json_mode,
        "connection_tested": False,
        "connection_status": "not_tested",
        "status": "configured_not_tested" if configured else "not_configured",
        "error": "",
    }
    warnings: list[dict[str, Any]] = []
    if config.protocol == "mock":
        return status, warnings
    if not api_key_present:
        warnings.append(_warning(
            "ai_api_key_missing",
            "模型 API Key 未配置",
            f"当前模型提供方是 {config.label}，但没有检测到 API Key。普通聊天和正式知识卡写作会失败。",
            f"在设置里的模型配置保存 API Key，或配置环境变量 {config.api_key_env}。",
            target="ai",
        ))
    if not base_url_present:
        warnings.append(_warning(
            "ai_base_url_missing",
            "模型 Base URL 未配置",
            f"当前模型提供方是 {config.label}，但 Base URL 为空。",
            "在设置里的模型配置补全接口地址。",
            target="ai",
        ))
    if not model_present:
        warnings.append(_warning(
            "ai_model_missing",
            "模型名未配置",
            f"当前模型提供方是 {config.label}，但模型名为空。",
            "在设置里的模型配置选择或填写模型名。",
            target="ai",
        ))
    return status, warnings


def _storage_target_status(target: str, runner_env: Mapping[str, str] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    provider = resolve_storage_provider(target)
    api_key_env = _clean(provider.get("api_key_env"))
    api_key_present = bool(provider.get("api_key_present")) or _env_present(runner_env, api_key_env)
    base_url_present = bool(_clean(provider.get("base_url"))) or not bool(provider.get("requires_base_url", True))
    configured = api_key_present and base_url_present
    status = {
        "provider_id": provider.get("provider_id"),
        "label": provider.get("label"),
        "kind": "storage",
        "configured": configured,
        "api_key_present": api_key_present,
        "api_key_env": api_key_env,
        "base_url_present": base_url_present,
        "base_url_label": provider.get("base_url"),
        "endpoint": provider.get("endpoint"),
        "status": "configured" if configured else "not_configured",
        "error": "",
    }
    warnings: list[dict[str, Any]] = []
    label = _clean(provider.get("label")) or target
    if not api_key_present:
        code = "lucas_database_api_key_missing" if target == "lucas_database" else "siyuan_token_missing"
        title = "Lucas Database API Key 未配置" if target == "lucas_database" else "SiYuan Token 未配置"
        destination = "Brain / Lucas Database" if target == "lucas_database" else "SiYuan"
        warnings.append(_warning(
            code,
            title,
            f"当前写入目标包含 {destination}，但没有检测到 {api_key_env}。对应写入会失败或被跳过。",
            f"在设置里的存储配置保存 {label} 的 API Key / Token，或配置环境变量 {api_key_env}。",
            target=target,
        ))
    if not base_url_present:
        code = "lucas_database_base_url_missing" if target == "lucas_database" else "siyuan_base_url_missing"
        warnings.append(_warning(
            code,
            f"{label} Base URL 未配置",
            f"当前写入目标包含 {label}，但 Base URL 为空。",
            "在设置里的存储配置补全接口地址。",
            target=target,
        ))
    return status, warnings


def _storage_status(runner_env: Mapping[str, str] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    public = get_public_storage_config()
    targets = list(public.get("storage_targets") or [])
    target_statuses: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not targets and public.get("auto_ingest_enabled") is False:
        warnings.append(_warning(
            "storage_targets_disabled",
            "自动写入目标未启用",
            "当前没有启用 AntTrail Database 写入目标；真实处理只会保留本地 job 和卡片草稿。",
            "在设置里的存储配置启用 AntTrail Database。",
            target="storage",
        ))
    for target in targets:
        status, target_warnings = _storage_target_status(str(target), runner_env)
        target_statuses.append(status)
        warnings.extend(target_warnings)
    configured = bool(targets) and all(item.get("configured") for item in target_statuses)
    status = {
        "active_provider": public.get("active_provider"),
        "config_exists": public.get("config_exists"),
        "storage_targets": targets,
        "auto_ingest_enabled": public.get("auto_ingest_enabled"),
        "lucas_database_write_policy": public.get("lucas_database_write_policy"),
        "configured": configured,
        "status": "configured" if configured else "not_configured",
        "targets": target_statuses,
    }
    return status, warnings


def build_config_preflight(
    *,
    include_ai: bool = True,
    include_storage: bool = True,
    runner_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    payload: dict[str, Any] = {"used_mcp": False}
    try:
        if include_ai:
            ai, ai_warnings = _ai_status(runner_env)
            payload["ai"] = ai
            warnings.extend(ai_warnings)
        if include_storage:
            storage, storage_warnings = _storage_status(runner_env)
            payload["storage"] = storage
            warnings.extend(storage_warnings)
    except Exception as exc:
        warnings.append(_warning(
            "config_preflight_failed",
            "配置预检失败",
            f"读取本地配置状态时失败：{type(exc).__name__}",
            "检查配置文件格式，或打开设置页重新保存模型 / 存储配置。",
            target="system",
        ))
    payload["ok"] = not warnings
    payload["status"] = "ready" if not warnings else "degraded"
    payload["warnings"] = warnings
    return payload


def _reply_already_mentions_ai_config(reply_text: str) -> bool:
    text = str(reply_text or "")
    return "模型配置" in text or "API Key" in text or "api_key_missing" in text


def _visible_warnings(reply_text: str, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not _reply_already_mentions_ai_config(reply_text):
        return warnings
    return [item for item in warnings if item.get("code") not in AI_CONFIG_CODES]


def _warning_block(warnings: list[dict[str, Any]]) -> str:
    lines = ["配置提醒："]
    for item in warnings[:4]:
        lines.append(f"- {item.get('message')} {item.get('action')}")
    if len(warnings) > 4:
        lines.append(f"- 还有 {len(warnings) - 4} 项配置提醒，请查看设置页或 data.config_warnings。")
    return "\n".join(lines)


def attach_config_preflight(
    response: HandlerResponse,
    *,
    include_ai: bool,
    include_storage: bool,
    runner_env: Mapping[str, str] | None = None,
) -> HandlerResponse:
    if not include_ai and not include_storage:
        return response
    preflight = build_config_preflight(include_ai=include_ai, include_storage=include_storage, runner_env=runner_env)
    response.data["config_preflight"] = preflight
    response.data["config_warnings"] = preflight.get("warnings", [])
    visible = _visible_warnings(response.reply_text, list(preflight.get("warnings") or []))
    if visible:
        response.reply_text = f"{_warning_block(visible)}\n\n{response.reply_text}"
    return response


def attach_config_preflight_to_payload(
    payload: dict[str, Any],
    *,
    include_ai: bool,
    include_storage: bool,
    runner_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not include_ai and not include_storage:
        return payload
    preflight = build_config_preflight(include_ai=include_ai, include_storage=include_storage, runner_env=runner_env)
    data = payload.setdefault("data", {})
    if isinstance(data, dict):
        data["config_preflight"] = preflight
        data["config_warnings"] = preflight.get("warnings", [])
    visible = _visible_warnings(str(payload.get("reply_text") or ""), list(preflight.get("warnings") or []))
    if visible:
        payload["reply_text"] = f"{_warning_block(visible)}\n\n{payload.get('reply_text') or ''}"
    return payload
