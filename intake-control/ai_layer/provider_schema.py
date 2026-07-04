from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ModelResult:
    ok: bool
    text: str = ""
    json_data: dict[str, Any] | None = None
    model_provider: str = ""
    model_name: str = ""
    error: str = ""
    raw_usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelProvider(Protocol):
    name: str

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelResult: ...

    def generate_json(
        self,
        prompt: str,
        schema_name: str | None = None,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelResult: ...

    def generate_markdown(
        self,
        prompt: str,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelResult: ...
