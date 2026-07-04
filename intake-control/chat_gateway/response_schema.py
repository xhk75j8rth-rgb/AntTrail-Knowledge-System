from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class HandlerResponse:
    ok: bool
    reply_text: str
    job_id: str | None = None
    status: str = "unknown"
    handled_by: str = "unknown"
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
