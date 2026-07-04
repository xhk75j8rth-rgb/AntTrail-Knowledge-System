from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse


TRAILING_PUNCT = "，,。.!！?？；;：:)]）】》>'\"“”"
URL_RE = re.compile(
    r"(https?://[^\s<>\"'“”\]\)]+|(?:(?:v|m|www)\.douyin\.com)(?:/[^\s<>\"'“”\]\)]*)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LinkExtractionResult:
    urls: list[str]

    @property
    def primary_url(self) -> str | None:
        return self.urls[0] if self.urls else None

    @property
    def extra_url_count(self) -> int:
        return max(0, len(self.urls) - 1)


def normalize_url(raw: str) -> str:
    url = (raw or "").strip().rstrip(TRAILING_PUNCT)
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = "https://" + url
    return url


def _valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item not in seen:
            output.append(item)
            seen.add(item)
    return output


def extract_urls(message: str) -> list[str]:
    candidates = [normalize_url(match.group(1)) for match in URL_RE.finditer(message or "")]
    return _dedupe(url for url in candidates if _valid_url(url))


def extract_links(message: str) -> LinkExtractionResult:
    return LinkExtractionResult(urls=extract_urls(message))


def extract_context_text(message: str, *, limit: int = 30000) -> str:
    text = URL_RE.sub(" ", message or "")
    text = re.sub(r"\]\(\s*\)", "]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()
