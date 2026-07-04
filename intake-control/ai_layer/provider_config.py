from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "ai_layer.local.json"


@dataclass(slots=True)
class ProviderPreset:
    id: str
    label: str
    protocol: str
    default_base_url: str
    default_model: str
    api_key_env: str
    supports_vision: bool = False
    supports_json_mode: bool = True
    notes: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProviderRuntimeConfig:
    provider_id: str
    label: str
    protocol: str
    base_url: str
    model: str
    api_key: str = ""
    api_key_env: str = ""
    api_key_source: str = ""
    supports_vision: bool = False
    supports_json_mode: bool = True
    notes: str = ""

    def masked(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "label": self.label,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "api_key_present": bool(self.api_key),
            "api_key_last4": self.api_key[-4:] if self.api_key else "",
            "api_key_source": self.api_key_source,
            "connection_tested": False,
            "connection_status": "not_tested",
            "supports_vision": self.supports_vision,
            "supports_json_mode": self.supports_json_mode,
            "notes": self.notes,
        }


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "deepseek_compatible": ProviderPreset(
        id="deepseek_compatible",
        label="DeepSeek",
        protocol="openai_compatible",
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        supports_vision=False,
        notes="DeepSeek 官方 OpenAI-compatible chat/completions；当前主要用于文本写卡。",
    ),
    "openai": ProviderPreset(
        id="openai",
        label="OpenAI",
        protocol="openai_compatible",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        supports_vision=True,
        notes="OpenAI 官方 v1 接口；可用于未来多模态模型接入。",
    ),
    "gemini_openai": ProviderPreset(
        id="gemini_openai",
        label="Gemini OpenAI 兼容",
        protocol="openai_compatible",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-1.5-pro",
        api_key_env="GEMINI_API_KEY",
        supports_vision=True,
        notes="Google Gemini OpenAI compatibility endpoint；适合未来视觉能力验证。",
    ),
    "anthropic": ProviderPreset(
        id="anthropic",
        label="Anthropic Claude",
        protocol="anthropic_messages",
        default_base_url="https://api.anthropic.com/v1",
        default_model="claude-3-5-sonnet-latest",
        api_key_env="ANTHROPIC_API_KEY",
        supports_vision=True,
        supports_json_mode=False,
        notes="Anthropic native Messages API；当前 AI Layer 支持文本调用，JSON 由文本解析。",
    ),
    "dashscope_openai": ProviderPreset(
        id="dashscope_openai",
        label="阿里云 DashScope 兼容",
        protocol="openai_compatible",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        api_key_env="DASHSCOPE_API_KEY",
        supports_vision=True,
        notes="DashScope OpenAI compatible-mode；模型名按阿里云控制台可用模型填写。",
    ),
    "moonshot": ProviderPreset(
        id="moonshot",
        label="Moonshot Kimi",
        protocol="openai_compatible",
        default_base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
        api_key_env="MOONSHOT_API_KEY",
        supports_vision=False,
        notes="Moonshot/Kimi OpenAI-compatible endpoint。",
    ),
    "siliconflow": ProviderPreset(
        id="siliconflow",
        label="SiliconFlow",
        protocol="openai_compatible",
        default_base_url="https://api.siliconflow.cn/v1",
        default_model="deepseek-ai/DeepSeek-V3",
        api_key_env="SILICONFLOW_API_KEY",
        supports_vision=True,
        notes="SiliconFlow OpenAI-compatible relay；模型名以平台可用列表为准。",
    ),
    "openrouter": ProviderPreset(
        id="openrouter",
        label="OpenRouter",
        protocol="openai_compatible",
        default_base_url="https://openrouter.ai/api/v1",
        default_model="deepseek/deepseek-chat",
        api_key_env="OPENROUTER_API_KEY",
        supports_vision=True,
        notes="OpenRouter OpenAI-compatible relay；不同模型视觉能力不同。",
    ),
    "openai_compatible": ProviderPreset(
        id="openai_compatible",
        label="自定义 OpenAI 兼容中转站",
        protocol="openai_compatible",
        default_base_url="http://127.0.0.1:8000/v1",
        default_model="deepseek-chat",
        api_key_env="AI_LAYER_API_KEY",
        supports_vision=False,
        notes="用于 one-api/new-api/litellm/公司网关等自定义中转站。",
    ),
}


def _normalize_provider_id(provider_id: str | None) -> str:
    value = (provider_id or "").strip().lower().replace("-", "_")
    aliases = {
        "": "deepseek_compatible",
        "deepseek": "deepseek_compatible",
        "deepseek-compatible": "deepseek_compatible",
        "mock_provider": "mock",
        "custom": "openai_compatible",
        "custom_openai": "openai_compatible",
        "gemini": "gemini_openai",
        "qwen": "dashscope_openai",
        "dashscope": "dashscope_openai",
        "kimi": "moonshot",
    }
    return aliases.get(value, value)


def provider_presets_public() -> list[dict[str, Any]]:
    return [preset.to_public_dict() for preset in PROVIDER_PRESETS.values()]


def _read_local_config() -> dict[str, Any]:
    path = local_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_local_config(data: dict[str, Any]) -> None:
    path = local_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def local_config_path() -> Path:
    override = os.environ.get("AI_LAYER_CONFIG_PATH")
    return Path(override).expanduser().resolve() if override else CONFIG_PATH


def load_ai_config_raw() -> dict[str, Any]:
    return deepcopy(_read_local_config())


def _env_provider_id() -> str:
    return _normalize_provider_id(os.environ.get("AI_LAYER_PROVIDER"))


def _provider_data(raw: dict[str, Any], provider_id: str) -> dict[str, Any]:
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        return {}
    data = providers.get(provider_id)
    return data if isinstance(data, dict) else {}


def get_active_provider_id() -> str:
    raw = _read_local_config()
    if os.environ.get("AI_LAYER_PROVIDER"):
        return _env_provider_id()
    env_provider = _env_provider_id()
    if env_provider != "deepseek_compatible":
        return env_provider
    return _normalize_provider_id(raw.get("active_provider") or raw.get("provider") or "deepseek_compatible")


def resolve_provider_config(provider_id: str | None = None) -> ProviderRuntimeConfig:
    raw = _read_local_config()
    normalized_id = _normalize_provider_id(provider_id) if provider_id else get_active_provider_id()
    if normalized_id == "mock":
        return ProviderRuntimeConfig(
            provider_id="mock",
            label="Mock Provider",
            protocol="mock",
            base_url="",
            model="mock-model",
            api_key="",
            api_key_env="",
            supports_vision=False,
            supports_json_mode=True,
            notes="测试专用，不用于生产运行。",
        )

    preset = PROVIDER_PRESETS.get(normalized_id) or PROVIDER_PRESETS["deepseek_compatible"]
    data = _provider_data(raw, preset.id)
    env_prefix = preset.id.upper()
    legacy_deepseek = preset.id == "deepseek_compatible"
    active_provider = _normalize_provider_id(os.environ.get("AI_LAYER_PROVIDER") or "")
    env_selected = not os.environ.get("AI_LAYER_PROVIDER") or active_provider == preset.id
    global_api_key = os.environ.get("AI_LAYER_API_KEY") if env_selected else ""
    global_base_url = os.environ.get("AI_LAYER_BASE_URL") if env_selected else ""
    global_model = os.environ.get("AI_LAYER_MODEL") if env_selected else ""

    api_key = ""
    api_key_source = ""
    api_key_candidates = [
        (data.get("api_key"), "local_config"),
        (os.environ.get(f"{env_prefix}_API_KEY"), f"env:{env_prefix}_API_KEY"),
        (global_api_key, "env:AI_LAYER_API_KEY"),
        (os.environ.get(preset.api_key_env), f"env:{preset.api_key_env}"),
    ]
    if legacy_deepseek:
        api_key_candidates.append((os.environ.get("DEEPSEEK_API_KEY"), "env:DEEPSEEK_API_KEY"))
    for candidate, source in api_key_candidates:
        if str(candidate or "").strip():
            api_key = str(candidate)
            api_key_source = source
            break
    base_url = (
        data.get("base_url")
        or os.environ.get(f"{env_prefix}_BASE_URL")
        or global_base_url
        or (os.environ.get("DEEPSEEK_BASE_URL") if legacy_deepseek else "")
        or preset.default_base_url
    )
    model = (
        data.get("model")
        or os.environ.get(f"{env_prefix}_MODEL")
        or global_model
        or (os.environ.get("DEEPSEEK_MODEL") if legacy_deepseek else "")
        or preset.default_model
    )
    return ProviderRuntimeConfig(
        provider_id=preset.id,
        label=preset.label,
        protocol=preset.protocol,
        base_url=str(base_url).rstrip("/"),
        model=str(model),
        api_key=str(api_key),
        api_key_env=preset.api_key_env,
        api_key_source=api_key_source,
        supports_vision=preset.supports_vision,
        supports_json_mode=preset.supports_json_mode,
        notes=preset.notes,
    )


def get_public_ai_config() -> dict[str, Any]:
    config = resolve_provider_config()
    config_path = local_config_path()
    return {
        "ok": True,
        "active_provider": config.provider_id,
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "provider": config.masked(),
        "presets": provider_presets_public(),
    }


def save_ai_config(update: dict[str, Any]) -> dict[str, Any]:
    provider_id = _normalize_provider_id(update.get("provider_id") or update.get("provider") or update.get("active_provider"))
    if provider_id == "mock":
        raise ValueError("mock provider can only be enabled through tests or environment, not saved from UI")
    if provider_id not in PROVIDER_PRESETS:
        raise ValueError(f"unsupported provider: {provider_id}")

    preset = PROVIDER_PRESETS[provider_id]
    raw = _read_local_config()
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    provider_data = providers.get(provider_id)
    if not isinstance(provider_data, dict):
        provider_data = {}

    base_url = str(update.get("base_url") or preset.default_base_url).strip().rstrip("/")
    model = str(update.get("model") or preset.default_model).strip()
    if not base_url:
        raise ValueError("base_url is required")
    if not model:
        raise ValueError("model is required")

    provider_data["base_url"] = base_url
    provider_data["model"] = model
    provider_data["protocol"] = preset.protocol

    if update.get("clear_api_key") is True:
        provider_data.pop("api_key", None)
    elif "api_key" in update and str(update.get("api_key") or ""):
        provider_data["api_key"] = str(update["api_key"])

    providers[provider_id] = provider_data
    raw["active_provider"] = provider_id
    raw["providers"] = providers
    _write_local_config(raw)
    return get_public_ai_config()


def build_runtime_config_from_payload(update: dict[str, Any]) -> ProviderRuntimeConfig:
    provider_id = _normalize_provider_id(update.get("provider_id") or update.get("provider") or update.get("active_provider"))
    if provider_id == "mock":
        return resolve_provider_config("mock")
    if provider_id not in PROVIDER_PRESETS:
        raise ValueError(f"unsupported provider: {provider_id}")
    preset = PROVIDER_PRESETS[provider_id]
    saved = resolve_provider_config(provider_id)
    base_url = str(update.get("base_url") or saved.base_url or preset.default_base_url).strip().rstrip("/")
    model = str(update.get("model") or saved.model or preset.default_model).strip()
    api_key = str(update.get("api_key") or saved.api_key or "").strip()
    api_key_source = "request" if str(update.get("api_key") or "").strip() else saved.api_key_source
    if not base_url:
        raise ValueError("base_url is required")
    if not model:
        raise ValueError("model is required")
    return ProviderRuntimeConfig(
        provider_id=preset.id,
        label=preset.label,
        protocol=preset.protocol,
        base_url=base_url,
        model=model,
        api_key=api_key,
        api_key_env=preset.api_key_env,
        api_key_source=api_key_source,
        supports_vision=preset.supports_vision,
        supports_json_mode=preset.supports_json_mode,
        notes=preset.notes,
    )
