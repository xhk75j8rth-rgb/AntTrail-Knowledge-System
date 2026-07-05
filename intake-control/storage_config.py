from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "storage.local.json"
LINK_PIPELINE_CONFIG_PATH = PROJECT_ROOT / "config" / "link_pipeline.json"
LINK_PIPELINE_EXAMPLE_PATH = PROJECT_ROOT / "config" / "link_pipeline.example.json"
ENV_PATH = PROJECT_ROOT / ".env"
CONFIG_PATH_ENV = "LUCAS_STORAGE_CONFIG_PATH"
ENV_PATH_ENV = "LUCAS_STORAGE_ENV_PATH"
LINK_PIPELINE_CONFIG_PATH_ENV = "LUCAS_LINK_PIPELINE_CONFIG_PATH"


@dataclass(slots=True)
class StorageProviderPreset:
    id: str
    label: str
    default_base_url: str
    api_key_env: str
    endpoint: str
    requires_base_url: bool = True
    notes: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


STORAGE_PRESETS: dict[str, StorageProviderPreset] = {
    "lucas_database": StorageProviderPreset(
        id="lucas_database",
        label="AntTrail Database",
        default_base_url="http://127.0.0.1:8765",
        api_key_env="LUCAS_DB_API_KEY",
        endpoint="/api/cards/ingest",
        notes="AntTrail 结构化知识主库。API Key 只在后端读取，不写入前端代码。",
    ),
    "siyuan": StorageProviderPreset(
        id="siyuan",
        label="SiYuan",
        default_base_url="http://127.0.0.1:6806",
        api_key_env="SIYUAN_TOKEN",
        endpoint="/api/filetree/createDocWithMd",
        notes="历史兼容 Markdown Sink；默认不启用。",
    ),
    "custom_http": StorageProviderPreset(
        id="custom_http",
        label="自定义",
        default_base_url="http://127.0.0.1:8000",
        api_key_env="CUSTOM_STORAGE_API_KEY",
        endpoint="/api/cards/ingest",
        notes="自定义 HTTP / Brain-compatible Sink；主链路是否写入仍以自动入库目标为准。",
    ),
}


def _normalize_provider_id(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("-", "_")
    aliases = {
        "": "lucas_database",
        "brain": "lucas_database",
        "lucas_db": "lucas_database",
        "lucas_database": "lucas_database",
        "database": "lucas_database",
        "db": "lucas_database",
        "siyuan": "siyuan",
        "思源": "siyuan",
        "custom": "custom_http",
        "custom_http": "custom_http",
        "custom_storage": "custom_http",
        "自定义": "custom_http",
    }
    return aliases.get(normalized, normalized)


def get_config_path() -> Path:
    return Path(os.environ.get(CONFIG_PATH_ENV) or CONFIG_PATH)


def get_env_path() -> Path:
    return Path(os.environ.get(ENV_PATH_ENV) or ENV_PATH)


def get_link_pipeline_config_path() -> Path:
    return Path(os.environ.get(LINK_PIPELINE_CONFIG_PATH_ENV) or LINK_PIPELINE_CONFIG_PATH)


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    path = path or get_env_path()
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
            os.environ[key] = value
    return values


def _read_local_config() -> dict[str, Any]:
    path = get_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_local_config(data: dict[str, Any]) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_link_pipeline_config() -> dict[str, Any]:
    path = get_link_pipeline_config_path()
    if not path.exists() and path == LINK_PIPELINE_CONFIG_PATH and LINK_PIPELINE_EXAMPLE_PATH.exists():
        path = LINK_PIPELINE_EXAMPLE_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_link_pipeline_config(data: dict[str, Any]) -> None:
    path = get_link_pipeline_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_dotenv_values(updates: dict[str, str]) -> None:
    path = get_env_path()
    existing: list[str] = []
    if path.exists():
        existing = path.read_text(encoding="utf-8").splitlines()

    handled: set[str] = set()
    next_lines: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            next_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            next_lines.append(f"{key}={updates[key]}")
            handled.add(key)
        else:
            next_lines.append(line)
    for key, value in updates.items():
        if key not in handled:
            next_lines.append(f"{key}={value}")
    path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")
    for key, value in updates.items():
        os.environ[key] = value


def provider_presets_public() -> list[dict[str, Any]]:
    return [preset.to_public_dict() for preset in STORAGE_PRESETS.values()]


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _normalize_storage_targets(value: Any, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"", "none", "off", "disabled", "local_only", "local-only"}:
            return []
        items = [item.strip().casefold() for item in lowered.replace("，", ",").replace(" ", ",").split(",") if item.strip()]
    elif isinstance(value, list):
        items = [str(item).strip().casefold() for item in value if str(item).strip()]
    else:
        items = []

    aliases = {
        "brain": "lucas_database",
        "lucas-db": "lucas_database",
        "lucas_db": "lucas_database",
        "lucas_database": "lucas_database",
        "database": "lucas_database",
        "db": "lucas_database",
        "siyuan": "siyuan",
        "思源": "siyuan",
        "both": "both",
        "none": "none",
        "off": "none",
        "disabled": "none",
        "local_only": "none",
        "local-only": "none",
    }
    targets: list[str] = []
    for item in items:
        normalized = aliases.get(item, item)
        if normalized == "both":
            candidates = ["siyuan", "lucas_database"]
        elif normalized == "none":
            candidates = []
        else:
            candidates = [normalized]
        for candidate in candidates:
            if candidate in {"siyuan", "lucas_database"} and candidate not in targets:
                targets.append(candidate)
    return targets


def _public_storage_provider(provider_id: str) -> dict[str, Any]:
    provider = dict(resolve_storage_provider(provider_id))
    provider.pop("api_key", None)
    return provider


def _pipeline_storage_public() -> dict[str, Any]:
    config = _read_link_pipeline_config()
    storage_enabled = True
    if "enable_storage_write" in config and not _truthy(config.get("enable_storage_write"), True):
        storage_enabled = False
    if "storage_write_enabled" in config and not _truthy(config.get("storage_write_enabled"), True):
        storage_enabled = False
    default_targets = ["lucas_database"]
    targets = [] if not storage_enabled else _normalize_storage_targets(
        config.get("storage_targets", config.get("storage_target")),
        default_targets,
    )
    return {
        "link_pipeline_path": str(get_link_pipeline_config_path()),
        "link_pipeline_exists": get_link_pipeline_config_path().exists(),
        "storage_targets": targets,
        "auto_ingest_enabled": bool(targets),
        "enable_storage_write": bool(targets),
        "lucas_database_write_policy": str(config.get("lucas_database_write_policy") or ""),
        "enable_lucas_database_write": "lucas_database" in targets,
        "siyuan_base_url": str(config.get("siyuan_base_url") or ""),
    }


def _lucas_database_runtime_provider_id(raw: dict[str, Any] | None = None) -> str:
    config = raw if isinstance(raw, dict) else _read_local_config()
    active_provider = _normalize_provider_id(config.get("active_provider") or "")
    return "custom_http" if active_provider == "custom_http" else "lucas_database"


def _target_status(target_id: str, enabled_targets: list[str], raw: dict[str, Any]) -> dict[str, Any]:
    provider_id = "siyuan" if target_id == "siyuan" else _lucas_database_runtime_provider_id(raw)
    provider = resolve_storage_provider(provider_id)
    enabled = target_id in enabled_targets
    has_base_url = bool(provider.get("base_url")) or not provider.get("requires_base_url", True)
    has_key = bool(provider.get("api_key_present"))
    can_write = bool(enabled and has_base_url and has_key)
    if not enabled:
        status = "disabled"
        reason = "not_enabled_in_storage_targets"
    elif not has_base_url:
        status = "missing_base_url"
        reason = "base_url_missing"
    elif not has_key:
        status = "missing_key"
        reason = f"missing_{provider.get('api_key_env') or 'api_key'}"
    else:
        status = "writable"
        reason = ""
    label = "SiYuan" if target_id == "siyuan" else "AntTrail Database"
    return {
        "target_id": target_id,
        "label": label,
        "enabled": enabled,
        "can_write": can_write,
        "status": status,
        "reason": reason,
        "provider_id": provider["provider_id"],
        "provider_label": provider["label"],
        "base_url": provider["base_url"],
        "endpoint": provider["endpoint"],
        "api_key_env": provider["api_key_env"],
        "api_key_present": provider["api_key_present"],
        "notes": provider["notes"],
    }


def _should_show_target_status(target: dict[str, Any]) -> bool:
    if target.get("enabled") or target.get("can_write"):
        return True
    if str(target.get("target_id") or "") == "siyuan":
        return False
    return True


def get_storage_capabilities() -> dict[str, Any]:
    raw = _read_local_config()
    pipeline = _pipeline_storage_public()
    targets = pipeline["storage_targets"]
    all_target_options = [
        _target_status("siyuan", targets, raw),
        _target_status("lucas_database", targets, raw),
    ]
    target_options = [target for target in all_target_options if _should_show_target_status(target)]
    writable_targets = [target for target in target_options if target["can_write"]]
    enabled_targets = [target for target in target_options if target["enabled"]]
    return {
        "ok": True,
        "active_provider": resolve_storage_provider()["provider_id"],
        "all_target_options": all_target_options,
        "target_options": target_options,
        "enabled_targets": enabled_targets,
        "writable_targets": writable_targets,
        **pipeline,
        "used_mcp": False,
    }


def _save_link_pipeline_storage_settings(
    *,
    targets: list[str] | None = None,
    auto_ingest_enabled: bool | None = None,
    siyuan_base_url: str | None = None,
) -> None:
    config = _read_link_pipeline_config()
    if siyuan_base_url is not None:
        config["siyuan_base_url"] = siyuan_base_url.strip().rstrip("/")
    if targets is not None or auto_ingest_enabled is not None:
        if auto_ingest_enabled is False:
            normalized_targets: list[str] = []
        else:
            normalized_targets = _normalize_storage_targets(targets or [])
        config["storage_targets"] = normalized_targets
        config["enable_storage_write"] = bool(normalized_targets)
        config["enable_lucas_database_write"] = "lucas_database" in normalized_targets
        if "lucas_database" in normalized_targets and not config.get("lucas_database_write_policy"):
            config["lucas_database_write_policy"] = "all_cards"
    _write_link_pipeline_config(config)


def _safe_database_target_id(value: Any, fallback: str) -> str:
    raw = str(value or fallback).strip().casefold().replace("-", "_")
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw)
    safe = "_".join(part for part in safe.split("_") if part)
    return safe[:40] or fallback


def _default_database_target_env(target_id: str, index: int) -> str:
    if index == 0 and target_id in {"main", "default", "lucas_database"}:
        return "LUCAS_DB_API_KEY"
    suffix = "".join(ch if ch.isalnum() else "_" for ch in target_id.upper()).strip("_")
    return f"LUCAS_DB_API_KEY_{suffix or index + 1}"


def _default_database_web_url(base_url: str = "") -> str:
    text = str(base_url or "").strip().rstrip("/")
    if text.startswith("http://127.0.0.1:8765") or text.startswith("http://localhost:8765"):
        return "http://127.0.0.1:5173/"
    return "http://127.0.0.1:5173/"


def _database_targets_from_raw(raw: dict[str, Any]) -> list[dict[str, Any]]:
    items = raw.get("database_targets")
    targets: list[dict[str, Any]] = []
    if isinstance(items, list):
        seen: set[str] = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            target_id = _safe_database_target_id(item.get("id"), f"database_{index + 1}")
            if target_id in seen:
                target_id = _safe_database_target_id(f"{target_id}_{index + 1}", f"database_{index + 1}")
            seen.add(target_id)
            base_url = str(item.get("base_url") or "").strip().rstrip("/")
            if not base_url:
                continue
            endpoint = str(item.get("endpoint") or STORAGE_PRESETS["lucas_database"].endpoint).strip()
            targets.append({
                "id": target_id,
                "label": str(item.get("label") or f"数据库 {index + 1}").strip(),
                "base_url": base_url,
                "web_url": str(item.get("web_url") or _default_database_web_url(base_url)).strip(),
                "endpoint": "/" + endpoint.lstrip("/"),
                "api_key_env": str(item.get("api_key_env") or _default_database_target_env(target_id, index)).strip(),
                "enabled": item.get("enabled", True) is not False,
            })
    if targets:
        return targets

    provider = resolve_storage_provider("lucas_database")
    return [{
        "id": "main",
        "label": provider["label"],
        "base_url": provider["base_url"],
        "web_url": _default_database_web_url(provider["base_url"]),
        "endpoint": provider["endpoint"],
        "api_key_env": provider["api_key_env"],
        "enabled": True,
    }]


def _selected_database_target_ids(raw: dict[str, Any], targets: list[dict[str, Any]]) -> list[str]:
    available = {target["id"] for target in targets}
    raw_ids = raw.get("selected_database_target_ids")
    if isinstance(raw_ids, list):
        selected = [str(item).strip() for item in raw_ids if str(item).strip() in available]
    else:
        selected = []
    if selected:
        return selected
    enabled = [target["id"] for target in targets if target.get("enabled") is not False]
    return enabled or ([targets[0]["id"]] if targets else [])


def _public_database_targets(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    load_dotenv()
    targets = _database_targets_from_raw(raw)
    selected_ids = _selected_database_target_ids(raw, targets)
    public_targets: list[dict[str, Any]] = []
    for target in targets:
        item = dict(target)
        item["selected"] = target["id"] in selected_ids
        item["api_key_present"] = bool(os.environ.get(str(target.get("api_key_env") or "")))
        public_targets.append(item)
    return public_targets, selected_ids


def _provider_data(raw: dict[str, Any], provider_id: str) -> dict[str, Any]:
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        return {}
    data = providers.get(provider_id)
    return data if isinstance(data, dict) else {}


def resolve_storage_provider(provider_id: str | None = None) -> dict[str, Any]:
    load_dotenv()
    raw = _read_local_config()
    normalized = _normalize_provider_id(provider_id or raw.get("active_provider") or "lucas_database")
    if normalized not in STORAGE_PRESETS:
        normalized = "lucas_database"
    preset = STORAGE_PRESETS[normalized]
    data = _provider_data(raw, normalized)
    base_url = str(data.get("base_url") or preset.default_base_url).strip().rstrip("/")
    endpoint = str(data.get("endpoint") or preset.endpoint).strip() or preset.endpoint
    api_key = str(os.environ.get(preset.api_key_env) or data.get("api_key") or "").strip()
    return {
        "provider_id": preset.id,
        "label": preset.label,
        "base_url": base_url,
        "endpoint": "/" + endpoint.lstrip("/"),
        "api_key_env": preset.api_key_env,
        "api_key": api_key,
        "api_key_present": bool(api_key),
        "requires_base_url": preset.requires_base_url,
        "notes": preset.notes,
    }


def get_public_storage_config() -> dict[str, Any]:
    active = resolve_storage_provider()
    public_provider = dict(active)
    public_provider.pop("api_key", None)
    raw = _read_local_config()
    capabilities = get_storage_capabilities()
    database_targets, selected_database_target_ids = _public_database_targets(raw)
    return {
        "ok": True,
        "active_provider": active["provider_id"],
        "config_path": str(get_config_path()),
        "config_exists": get_config_path().exists(),
        "env_path": str(get_env_path()),
        "link_pipeline_config_path": str(get_link_pipeline_config_path()),
        "database_targets": database_targets,
        "selected_database_target_ids": selected_database_target_ids,
        "provider": public_provider,
        "providers": {provider_id: _public_storage_provider(provider_id) for provider_id in STORAGE_PRESETS},
        "presets": provider_presets_public(),
        **capabilities,
        "used_mcp": False,
    }


def save_storage_config(update: dict[str, Any]) -> dict[str, Any]:
    provider_id = _normalize_provider_id(update.get("provider_id") or update.get("provider") or update.get("active_provider"))
    if provider_id not in STORAGE_PRESETS:
        raise ValueError(f"unsupported storage provider: {provider_id}")
    preset = STORAGE_PRESETS[provider_id]

    raw = _read_local_config()
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    provider_data = providers.get(provider_id)
    if not isinstance(provider_data, dict):
        provider_data = {}

    base_url = str(update.get("base_url") or preset.default_base_url).strip().rstrip("/")
    endpoint = str(update.get("endpoint") or preset.endpoint).strip()
    if preset.requires_base_url and not base_url:
        raise ValueError("base_url is required")
    provider_data["base_url"] = base_url
    provider_data["endpoint"] = "/" + endpoint.lstrip("/")

    api_key = str(update.get("api_key") or "").strip()
    if api_key:
        _write_dotenv_values({preset.api_key_env: api_key})
    elif update.get("clear_api_key") is True:
        _write_dotenv_values({preset.api_key_env: ""})

    providers[provider_id] = provider_data
    raw["active_provider"] = provider_id
    raw["providers"] = providers

    database_targets_update = update.get("database_targets")
    if isinstance(database_targets_update, list):
        clean_targets: list[dict[str, Any]] = []
        env_updates: dict[str, str] = {}
        seen: set[str] = set()
        for index, item in enumerate(database_targets_update):
            if not isinstance(item, dict):
                continue
            target_id = _safe_database_target_id(item.get("id"), f"database_{index + 1}")
            if target_id in seen:
                target_id = _safe_database_target_id(f"{target_id}_{index + 1}", f"database_{index + 1}")
            seen.add(target_id)
            base_url_value = str(item.get("base_url") or "").strip().rstrip("/")
            if not base_url_value:
                continue
            endpoint_value = str(item.get("endpoint") or STORAGE_PRESETS["lucas_database"].endpoint).strip()
            api_key_env = str(item.get("api_key_env") or _default_database_target_env(target_id, index)).strip()
            target = {
                "id": target_id,
                "label": str(item.get("label") or f"数据库 {index + 1}").strip(),
                "base_url": base_url_value,
                "web_url": str(item.get("web_url") or _default_database_web_url(base_url_value)).strip(),
                "endpoint": "/" + endpoint_value.lstrip("/"),
                "api_key_env": api_key_env,
                "enabled": item.get("enabled", True) is not False,
            }
            clean_targets.append(target)
            target_api_key = str(item.get("api_key") or "").strip()
            if target_api_key:
                env_updates[api_key_env] = target_api_key
        if env_updates:
            _write_dotenv_values(env_updates)
        if clean_targets:
            raw["database_targets"] = clean_targets
            selected_requested = update.get("selected_database_target_ids")
            if isinstance(selected_requested, list):
                selected_ids = [str(item).strip() for item in selected_requested if str(item).strip() in {target["id"] for target in clean_targets}]
            else:
                selected_ids = [target["id"] for target in clean_targets if target.get("enabled") is not False]
            raw["selected_database_target_ids"] = selected_ids or [clean_targets[0]["id"]]
            selected_target = next((target for target in clean_targets if target["id"] == raw["selected_database_target_ids"][0]), clean_targets[0])
            providers["lucas_database"] = {
                "base_url": selected_target["base_url"],
                "endpoint": selected_target["endpoint"],
            }
            raw["providers"] = providers

    if provider_id == "siyuan":
        _save_link_pipeline_storage_settings(siyuan_base_url=base_url)

    should_update_pipeline = any(
        key in update
        for key in (
            "storage_write_enabled",
            "enable_storage_write",
            "auto_ingest_enabled",
            "storage_targets",
            "storage_target",
        )
    )
    if should_update_pipeline:
        pipeline = _read_link_pipeline_config()
        storage_write_enabled = _truthy(
            update.get("storage_write_enabled", update.get("enable_storage_write", update.get("auto_ingest_enabled"))),
            True,
        )
        pipeline["enable_storage_write"] = storage_write_enabled
        default_targets = _normalize_storage_targets(pipeline.get("storage_targets"), ["lucas_database"])
        targets = [] if not storage_write_enabled else _normalize_storage_targets(
            update.get("storage_targets", update.get("storage_target")),
            default_targets,
        )
        pipeline["storage_targets"] = targets
        pipeline["enable_lucas_database_write"] = "lucas_database" in targets and storage_write_enabled
        if "lucas_database" in targets and not pipeline.get("lucas_database_write_policy"):
            pipeline["lucas_database_write_policy"] = "all_cards"
        _write_link_pipeline_config(pipeline)

    _write_local_config(raw)
    return get_public_storage_config()


def resolve_lucas_database_runtime(target_id: str | None = None) -> dict[str, Any]:
    if target_id:
        runtimes = resolve_lucas_database_runtimes()
        for runtime in runtimes:
            if runtime["target_id"] == target_id:
                return runtime
    raw = _read_local_config()
    if _lucas_database_runtime_provider_id(raw) == "custom_http":
        provider = resolve_storage_provider("custom_http")
        return {
            "target_id": "custom_http",
            "label": provider["label"],
            "base_url": provider["base_url"],
            "web_url": _default_database_web_url(provider["base_url"]),
            "endpoint": provider["endpoint"],
            "api_key": provider["api_key"],
            "api_key_present": provider["api_key_present"],
            "api_key_env": provider["api_key_env"],
        }
    targets = _database_targets_from_raw(raw)
    selected_ids = _selected_database_target_ids(raw, targets)
    if targets and selected_ids:
        target = next((item for item in targets if item["id"] == selected_ids[0]), targets[0])
        load_dotenv()
        api_key = str(os.environ.get(str(target.get("api_key_env") or "")) or "").strip()
        return {
            "target_id": target["id"],
            "label": target["label"],
            "base_url": target["base_url"],
            "web_url": target.get("web_url") or _default_database_web_url(target["base_url"]),
            "endpoint": target["endpoint"],
            "api_key": api_key,
            "api_key_present": bool(api_key),
            "api_key_env": target["api_key_env"],
        }

    provider = resolve_storage_provider(_lucas_database_runtime_provider_id(raw))
    return {
        "target_id": "main",
        "label": provider["label"],
        "base_url": provider["base_url"],
        "web_url": _default_database_web_url(provider["base_url"]),
        "endpoint": provider["endpoint"],
        "api_key": provider["api_key"],
        "api_key_present": provider["api_key_present"],
        "api_key_env": provider["api_key_env"],
    }


def resolve_lucas_database_runtimes() -> list[dict[str, Any]]:
    raw = _read_local_config()
    if _lucas_database_runtime_provider_id(raw) == "custom_http":
        return [resolve_lucas_database_runtime()]
    targets = _database_targets_from_raw(raw)
    selected_ids = _selected_database_target_ids(raw, targets)
    load_dotenv()
    runtimes: list[dict[str, Any]] = []
    for target in targets:
        if target["id"] not in selected_ids:
            continue
        api_key = str(os.environ.get(str(target.get("api_key_env") or "")) or "").strip()
        runtimes.append({
            "target_id": target["id"],
            "label": target["label"],
            "base_url": target["base_url"],
            "web_url": target.get("web_url") or _default_database_web_url(target["base_url"]),
            "endpoint": target["endpoint"],
            "api_key": api_key,
            "api_key_present": bool(api_key),
            "api_key_env": target["api_key_env"],
        })
    return runtimes or [resolve_lucas_database_runtime()]


def _read_response_body(response: Any) -> tuple[dict[str, Any], str]:
    raw = response.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}, ""
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}, raw
    except json.JSONDecodeError:
        return {}, raw


def _post_json(
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout_sec: int,
) -> tuple[int, dict[str, Any], str]:
    url = base_url.rstrip("/") + "/" + endpoint.lstrip("/")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            **headers,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            data, raw = _read_response_body(response)
            return int(response.status), data, raw
    except urllib.error.HTTPError as exc:
        data, raw = _read_response_body(exc)
        return int(exc.code), data, raw


def _compact_error(data: dict[str, Any], raw: str) -> str:
    for key in ("error", "detail", "message", "msg"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    return raw.strip()[:240]


def test_storage_connection(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    update = payload or {}
    provider_id = _normalize_provider_id(update.get("provider_id") or update.get("provider") or update.get("active_provider"))
    if provider_id not in STORAGE_PRESETS:
        raise ValueError(f"unsupported storage provider: {provider_id}")

    provider = resolve_storage_provider(provider_id)
    base_url = str(update.get("base_url") or provider["base_url"]).strip().rstrip("/")
    endpoint = str(update.get("endpoint") or provider["endpoint"]).strip() or provider["endpoint"]
    api_key = str(update.get("api_key") or provider["api_key"] or "").strip()
    timeout_sec = int(update.get("timeout_sec") or 8)
    timeout_sec = min(max(timeout_sec, 1), 30)
    base = {
        "provider_id": provider_id,
        "base_url": base_url,
        "endpoint": "/" + endpoint.lstrip("/"),
        "api_key_env": provider["api_key_env"],
        "token_present": bool(api_key),
        "write_attempted": False,
        "used_mcp": False,
    }
    if not base_url:
        return {**base, "ok": False, "status": "missing_base_url", "stage": "config"}
    if not api_key:
        return {**base, "ok": False, "status": "missing_token", "stage": "auth"}

    try:
        if provider_id == "siyuan":
            status_code, data, raw = _post_json(
                base_url,
                "/api/notebook/lsNotebooks",
                {},
                headers={"Authorization": f"Token {api_key}"},
                timeout_sec=timeout_sec,
            )
            code = data.get("code")
            ok = status_code == 200 and (code in (0, None))
            return {
                **base,
                "ok": ok,
                "status": "connected" if ok else ("auth_failed" if status_code in {401, 403} else "api_error"),
                "stage": "notebook_list",
                "http_status": status_code,
                "auth_ok": ok,
                "connection_ok": status_code < 500,
                "error": "" if ok else _compact_error(data, raw),
            }

        status_code, data, raw = _post_json(
            base_url,
            endpoint,
            {"connection_probe": True},
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-API-Token": api_key,
                "User-Agent": "Lucas-Knowledge-DB-Lab/0.1",
            },
            timeout_sec=timeout_sec,
        )
        if status_code in {400, 422}:
            return {
                **base,
                "ok": True,
                "status": "connected_schema_rejected",
                "stage": "schema_probe",
                "http_status": status_code,
                "auth_ok": True,
                "connection_ok": True,
                "error": _compact_error(data, raw),
            }
        ok = 200 <= status_code < 300
        return {
            **base,
            "ok": ok,
            "status": "connected_unexpected_success" if ok else ("auth_failed" if status_code in {401, 403} else "api_error"),
            "stage": "schema_probe",
            "http_status": status_code,
            "auth_ok": ok,
            "connection_ok": status_code < 500,
            "error": "" if ok else _compact_error(data, raw),
        }
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return {
            **base,
            "ok": False,
            "status": "unreachable",
            "stage": "request",
            "auth_ok": False,
            "connection_ok": False,
            "error": str(reason)[:240],
        }
