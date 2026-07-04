#!/usr/bin/env python
from __future__ import annotations

import argparse
import html
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def write_json(job_dir: Path, payload: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "content.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def strip_tags(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def find_meta(page: str, names: list[str]) -> str:
    for name in names:
        patterns = [
            rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']*)["\']',
            rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:name|property)=["\']{re.escape(name)}["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, page, flags=re.I)
            if match:
                return html.unescape(match.group(1)).strip()
    return ""


def maybe_need_ocr(text: str) -> bool:
    keywords = ("ppt", "代码", "流程图", "屏幕", "录屏", "字幕", "工具演示", "教程", "操作", "界面")
    lower = text.casefold()
    return any(k.casefold() in lower for k in keywords)


def base_payload(url: str) -> dict[str, Any]:
    return {
        "ok": False,
        "used_mcp": False,
        "stage": "fetch_content",
        "source_type": "douyin_video" if "douyin" in url.casefold() else "webpage",
        "original_url": url,
        "final_url": "",
        "title": "",
        "author": "",
        "published_at": "",
        "description": "",
        "visible_text": "",
        "like_count": "",
        "comment_count": "",
        "collect_count": "",
        "share_count": "",
        "status": "not_started",
        "should_continue_transcribe": True,
        "need_ocr": False,
        "can_try_comments": False,
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch basic page metadata without MCP.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--timeout-sec", type=int, default=20)
    args = parser.parse_args()

    job_dir = Path(args.job_dir).resolve()
    payload = base_payload(args.url)
    req = urllib.request.Request(
        args.url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout_sec) as resp:
            raw = resp.read(2_000_000)
            final_url = resp.geturl()
            content_type = resp.headers.get("content-type", "")
        page = raw.decode("utf-8", errors="replace")
        title_match = re.search(r"<title[^>]*>([\s\S]*?)</title>", page, flags=re.I)
        title = html.unescape(title_match.group(1)).strip() if title_match else ""
        description = find_meta(page, ["description", "og:description"])
        og_title = find_meta(page, ["og:title"])
        author = find_meta(page, ["author", "og:site_name"])
        visible_text = strip_tags(page)[:8000]
        all_text = " ".join(x for x in (title, og_title, description, visible_text) if x)
        payload.update({
            "ok": True,
            "final_url": final_url,
            "title": title or og_title,
            "author": author,
            "description": description,
            "visible_text": visible_text,
            "status": "page_fetched",
            "need_ocr": maybe_need_ocr(all_text),
            "can_try_comments": "douyin.com" in final_url.casefold(),
            "content_type": content_type,
        })
        write_json(job_dir, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except urllib.error.URLError as exc:
        payload.update({
            "ok": False,
            "status": "page_fetch_failed",
            "error": f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}",
            "should_continue_transcribe": True,
        })
    except Exception as exc:
        payload.update({
            "ok": False,
            "status": "page_fetch_failed",
            "error": str(exc),
            "should_continue_transcribe": True,
        })
    write_json(job_dir, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
