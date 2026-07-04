from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolRequest:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    allow_execution: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
