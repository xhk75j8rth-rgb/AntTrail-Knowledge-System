#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from intake_platforms import find_browser, profile_dir  # noqa: E402

SENSITIVE_QUERY_MARKERS = ("token", "secret", "key", "auth", "session", "cookie", "sid")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 LucasKnowledgeDB/1.0"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def make_job_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-webpage-{uuid.uuid4().hex[:6]}"


def redact_url(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return url
    items = []
    redacted = False
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if any(marker in key.casefold() for marker in SENSITIVE_QUERY_MARKERS):
            items.append((key, "<redacted>"))
            redacted = True
        else:
            items.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(items), fragment="" if redacted else parsed.fragment))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_job_dir(args: argparse.Namespace) -> Path:
    if args.job_dir:
        return Path(args.job_dir).resolve()
    runtime_dir = Path(args.runtime_dir or "runtime/jobs")
    if not runtime_dir.is_absolute():
        runtime_dir = PROJECT_ROOT / runtime_dir
    return runtime_dir / make_job_id()


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def chinese_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def latin_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z0-9'_-]*", text or ""))


def is_supported_webpage_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def decode_body(raw: bytes, content_type: str) -> str:
    charset = ""
    match = re.search(r"charset=([\w.-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    for encoding in [charset, "utf-8", "gb18030", "latin-1"]:
        if not encoding:
            continue
        try:
            return raw.decode(encoding, errors="replace")
        except LookupError:
            continue
    return raw.decode("utf-8", errors="replace")


class WebpageTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.text_parts: list[str] = []
        self.image_count = 0
        self.video_count = 0
        self.audio_count = 0
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attr = {key.casefold(): value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            name = (attr.get("name") or attr.get("property") or "").casefold()
            content = clean_text(attr.get("content") or "")
            if name and content:
                self.meta[name] = content
        if tag == "img":
            self.image_count += 1
        elif tag == "video":
            self.video_count += 1
        elif tag == "audio":
            self.audio_count += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg", "canvas"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = clean_text(data)
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
        if self._skip_depth:
            return
        self.text_parts.append(text)


BLOCKED_LINE_PATTERNS = (
    re.compile(r"^(登录|注册|首页|搜索|菜单|广告|隐私政策|用户协议|版权|分享|收藏|点赞)$"),
    re.compile(r"^(上一篇|下一篇|相关推荐|相关文章|热门推荐|更多精彩)$"),
    re.compile(r"^(©|copyright\b)", re.I),
)
SHELL_LINE_PATTERNS = (
    re.compile(r"^(skip to content|new chat|search chats|chat history|settings|help)$", re.I),
    re.compile(r"^(log in|sign up|sign up for free|see plans and pricing)$", re.I),
    re.compile(r"^(terms|privacy policy|cookie policy|learn more)$", re.I),
    re.compile(r"^(images|apps|voice|deep research)$", re.I),
)
WEBAPP_SHELL_MARKERS = (
    "skip to content",
    "log in",
    "sign up",
    "enable javascript",
    "__next",
    "window.__",
    "app-root",
    "id=\"root\"",
    "id=\"app\"",
)


def normalize_lines(text: str) -> list[str]:
    seen: set[str] = set()
    lines: list[str] = []
    for raw in re.split(r"[\n\r]+", text or ""):
        line = clean_text(raw)
        if not line or len(line) <= 1:
            continue
        if any(pattern.search(line) for pattern in BLOCKED_LINE_PATTERNS):
            continue
        if any(pattern.search(line) for pattern in SHELL_LINE_PATTERNS):
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines


def text_quality(material: dict[str, Any]) -> dict[str, Any]:
    text = clean_text(material.get("main_text") or material.get("visible_text") or "")
    return {
        "text_length": len(text),
        "chinese_char_count": chinese_char_count(text),
        "latin_word_count": latin_word_count(text),
    }


def material_has_primary_text(material: dict[str, Any]) -> bool:
    quality = text_quality(material)
    return (
        quality["chinese_char_count"] >= 80
        or quality["latin_word_count"] >= 90
        or quality["text_length"] >= 900
    )


def looks_like_webapp_shell(html: str, material: dict[str, Any]) -> bool:
    text = clean_text(material.get("main_text") or material.get("visible_text") or "")
    lower = f"{html[:12000]} {text}".casefold()
    marker_hits = sum(1 for marker in WEBAPP_SHELL_MARKERS if marker in lower)
    quality = text_quality(material)
    if marker_hits >= 2 and not material_has_primary_text(material):
        return True
    if any(marker in lower for marker in ('__next', 'id="root"', 'id="app"')) and quality["text_length"] < 1200:
        return True
    return False


def should_try_rendered_fallback(
    html: str,
    material: dict[str, Any],
    *,
    force: bool = False,
    disabled: bool = False,
) -> bool:
    if disabled:
        return False
    if force:
        return True
    if material_has_primary_text(material):
        return False
    if not material.get("ok"):
        return True
    return looks_like_webapp_shell(html, material) or bool(material.get("title"))


def extract_html_material(html: str, url: str, content_type: str = "text/html") -> dict[str, Any]:
    parser = WebpageTextParser()
    parser.feed(html or "")
    parser.close()
    raw_text = "\n".join(parser.text_parts)
    title = clean_text(parser.meta.get("og:title") or parser.meta.get("twitter:title") or parser.title)
    if title:
        title = re.sub(r"\s*[-_|]\s*.+$", "", title).strip() or title
    description = clean_text(
        parser.meta.get("description")
        or parser.meta.get("og:description")
        or parser.meta.get("twitter:description")
    )
    author = clean_text(parser.meta.get("author") or parser.meta.get("article:author"))
    published_at = clean_text(parser.meta.get("article:published_time") or parser.meta.get("pubdate"))
    lines = normalize_lines(raw_text)
    if title:
        lines = [line for line in lines if line != title]
    main_text = "\n".join(lines)
    if len(main_text) < 120 and description and description not in main_text:
        main_text = f"{main_text}\n{description}".strip()
    main_text = main_text[:30000]
    return {
        "ok": bool(title or chinese_char_count(main_text) >= 20 or len(main_text) >= 80),
        "used_mcp": False,
        "stage": "fetch_webpage",
        "status": "webpage_extracted",
        "source_type": "webpage",
        "content_kind": "webpage",
        "original_url": redact_url(url),
        "final_url": redact_url(url),
        "title": title,
        "author": author,
        "published_at": published_at,
        "description": description,
        "visible_text": main_text,
        "main_text": main_text,
        "text_length": len(main_text),
        "chinese_char_count": chinese_char_count(main_text),
        "latin_word_count": latin_word_count(main_text),
        "image_count": parser.image_count,
        "has_video": parser.video_count > 0,
        "video_source_count": parser.video_count,
        "audio_source_count": parser.audio_count,
        "status_code": None,
        "content_type": content_type,
        "extraction_source": "html_parser",
        "should_continue_transcribe": False,
        "need_ocr": parser.image_count > 0 and chinese_char_count(main_text) < 80,
        "can_try_comments": False,
        "error": None,
    }


def fetch_url(url: str, timeout_sec: int, max_bytes: int) -> tuple[dict[str, Any], bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        raw = response.read(max_bytes + 1)
        info = response.info()
        return {
            "status_code": getattr(response, "status", None),
            "final_url": redact_url(response.geturl()),
            "content_type": info.get("Content-Type", ""),
            "truncated": len(raw) > max_bytes,
        }, raw[:max_bytes]


def node_cdp_script() -> str:
    return r"""
const fs = require('fs');
const { spawn } = require('child_process');

const browserPath = process.argv[1];
const targetUrl = process.argv[2];
const profileDir = process.argv[3];
const headed = process.argv[4] === '1';
const timeoutMs = Number(process.argv[5] || 35000);
const port = 12600 + Math.floor(Math.random() * 1200);

fs.mkdirSync(profileDir, { recursive: true });

const browserArgs = [
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${profileDir}`,
  '--profile-directory=Default',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-background-networking',
  '--disable-sync',
  '--autoplay-policy=user-gesture-required',
  '--lang=zh-CN',
  'about:blank',
];
if (!headed) browserArgs.push('--headless=new', '--disable-gpu');

const browser = spawn(browserPath, browserArgs, { stdio: ['ignore', 'ignore', 'pipe'] });
let browserStderr = '';
browser.stderr.on('data', chunk => {
  browserStderr += chunk.toString();
  if (browserStderr.length > 4000) browserStderr = browserStderr.slice(-4000);
});

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function fetchJson(endpoint, timeout) {
  const started = Date.now();
  let lastError = '';
  while (Date.now() - started < timeout) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}${endpoint}`);
      if (response.ok) return await response.json();
      lastError = `HTTP ${response.status}`;
    } catch (err) {
      lastError = String(err && err.message || err);
    }
    await sleep(300);
  }
  throw new Error(`CDP endpoint unavailable: ${endpoint}; ${lastError}`);
}

function makeCdp(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const pending = new Map();
    let nextId = 1;
    ws.onopen = () => {
      resolve({
        send(method, params = {}) {
          const id = nextId++;
          ws.send(JSON.stringify({ id, method, params }));
          return new Promise((res, rej) => {
            const timer = setTimeout(() => {
              pending.delete(id);
              rej(new Error(`CDP timeout: ${method}`));
            }, 15000);
            pending.set(id, { res, rej, timer });
          });
        },
        close() {
          ws.close();
        },
      });
    };
    ws.onerror = err => reject(new Error(String(err && err.message || err)));
    ws.onmessage = event => {
      const msg = JSON.parse(event.data);
      if (!msg.id || !pending.has(msg.id)) return;
      const item = pending.get(msg.id);
      pending.delete(msg.id);
      clearTimeout(item.timer);
      if (msg.error) item.rej(new Error(msg.error.message || JSON.stringify(msg.error)));
      else item.res(msg.result || {});
    };
  });
}

function mediaPlaybackGuardScript() {
  return `(() => {
    if (window.__lucasMediaPlaybackGuardInstalled) return true;
    window.__lucasMediaPlaybackGuardInstalled = true;
    const stopMedia = media => {
      try { media.muted = true; } catch (_) {}
      try { media.autoplay = false; } catch (_) {}
      try { media.removeAttribute('autoplay'); } catch (_) {}
      try { media.preload = 'metadata'; } catch (_) {}
      try { media.pause(); } catch (_) {}
    };
    const stopAllMedia = () => {
      for (const media of document.querySelectorAll('video,audio')) stopMedia(media);
    };
    const originalPlay = HTMLMediaElement.prototype.play;
    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value: function lucasBlockedPlay() {
        stopMedia(this);
        return Promise.resolve();
      },
    });
    document.addEventListener('play', event => {
      if (event && event.target instanceof HTMLMediaElement) stopMedia(event.target);
    }, true);
    const observer = new MutationObserver(stopAllMedia);
    observer.observe(document.documentElement || document, { childList: true, subtree: true });
    stopAllMedia();
    window.__lucasRestoreMediaPlay = () => {
      try { HTMLMediaElement.prototype.play = originalPlay; } catch (_) {}
      try { observer.disconnect(); } catch (_) {}
    };
    return true;
  })()`;
}

function extractionScript() {
  function clean(value) {
    return String(value || '').replace(/\u00a0/g, ' ').replace(/[ \t\r\f\v]+/g, ' ').trim();
  }
  function chineseCount(text) {
    return (String(text || '').match(/[\u4e00-\u9fff]/g) || []).length;
  }
  function latinWordCount(text) {
    return (String(text || '').match(/[A-Za-z][A-Za-z0-9'_-]*/g) || []).length;
  }
  const blockedPatterns = [
    /^(登录|注册|首页|搜索|菜单|广告|隐私政策|用户协议|版权|分享|收藏|点赞)$/,
    /^(上一篇|下一篇|相关推荐|相关文章|热门推荐|更多精彩)$/,
    /^(skip to content|new chat|search chats|chat history|settings|help)$/i,
    /^(log in|sign up|sign up for free|see plans and pricing)$/i,
    /^(terms|privacy policy|cookie policy|learn more)$/i,
    /^(images|apps|voice|deep research)$/i,
    /^(©|copyright\b)/i,
  ];
  function normalizeLines(text) {
    const seen = new Set();
    const lines = [];
    for (const raw of String(text || '').split(/\n+/)) {
      const line = clean(raw);
      if (!line || line.length <= 1) continue;
      if (blockedPatterns.some(pattern => pattern.test(line))) continue;
      if (seen.has(line)) continue;
      seen.add(line);
      lines.push(line);
    }
    return lines;
  }
  function meta(name) {
    const el = document.querySelector(`meta[name="${name}"], meta[property="${name}"]`);
    return el ? clean(el.getAttribute('content')) : '';
  }
  function parseJsonLd() {
    const candidates = [];
    function visit(value, depth = 0) {
      if (!value || depth > 7) return;
      if (Array.isArray(value)) {
        for (const item of value.slice(0, 80)) visit(item, depth + 1);
        return;
      }
      if (typeof value !== 'object') return;
      const body = clean(value.articleBody || value.text || value.description || '');
      const headline = clean(value.headline || value.name || value.title || '');
      if (body || headline) candidates.push({ title: headline, text: body, source: 'json_ld' });
      for (const child of Object.values(value).slice(0, 120)) visit(child, depth + 1);
    }
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        visit(JSON.parse(script.textContent || '{}'));
      } catch (_) {}
    }
    return candidates;
  }
  function candidateElements() {
    const selectors = [
      'article',
      'main',
      '[role="main"]',
      '[data-message-author-role]',
      '[class*="markdown"]',
      '[class*="prose"]',
      '[class*="article"]',
      '[class*="post"]',
      '[class*="content"]',
      '[class*="conversation"]',
      '[class*="message"]',
      '[class*="thread"]',
    ];
    const elements = new Set();
    for (const selector of selectors) {
      for (const el of document.querySelectorAll(selector)) elements.add(el);
    }
    const h1 = document.querySelector('h1');
    if (h1) {
      let current = h1.parentElement;
      for (let i = 0; current && i < 5; i += 1, current = current.parentElement) elements.add(current);
    }
    elements.add(document.body);
    return Array.from(elements).filter(Boolean);
  }
  function scoreText(text, source) {
    const lines = normalizeLines(text);
    const normalized = lines.join('\n');
    const cn = chineseCount(normalized);
    const words = latinWordCount(normalized);
    const shellHits = lines.filter(line => blockedPatterns.some(pattern => pattern.test(line))).length;
    const shortLinePenalty = Math.max(0, lines.filter(line => line.length < 8).length - 8) * 3;
    const sourceBonus = source === 'json_ld' ? 140 : source === 'message_dom' ? 90 : source === 'article_dom' ? 50 : 0;
    return {
      text: normalized,
      lines,
      chinese_char_count: cn,
      latin_word_count: words,
      text_length: normalized.length,
      score: cn * 3 + words + Math.min(normalized.length, 4000) / 10 + sourceBonus - shellHits * 30 - shortLinePenalty,
      source,
    };
  }
  const candidates = [];
  for (const item of parseJsonLd()) {
    candidates.push(scoreText([item.title, item.text].filter(Boolean).join('\n'), item.source));
  }
  const messageEls = Array.from(document.querySelectorAll('[data-message-author-role]'));
  if (messageEls.length) {
    candidates.push(scoreText(messageEls.map(el => el.innerText || el.textContent || '').join('\n\n'), 'message_dom'));
  }
  for (const el of candidateElements()) {
    const tag = (el.tagName || '').toLowerCase();
    const source = tag === 'article' || tag === 'main' ? 'article_dom' : 'rendered_dom';
    candidates.push(scoreText(el.innerText || el.textContent || '', source));
  }
  candidates.sort((a, b) => b.score - a.score);
  const best = candidates[0] || scoreText('', 'rendered_dom');
  const title = clean(
    meta('og:title') ||
    meta('twitter:title') ||
    (document.querySelector('h1') && document.querySelector('h1').innerText) ||
    document.title ||
    ''
  ).replace(/\s*[-_|]\s*.+$/, '').trim();
  const description = clean(meta('description') || meta('og:description') || meta('twitter:description'));
  const author = clean(meta('author') || meta('article:author'));
  const publishedAt = clean(meta('article:published_time') || meta('pubdate'));
  const visibleText = document.body ? document.body.innerText || '' : '';
  const loginWallDetected = /扫码登录|手机号登录|请完成安全验证|输入验证码|enable javascript|please enable javascript/i.test(visibleText);
  const imageCount = Array.from(document.images || []).filter(img => {
    const rect = img.getBoundingClientRect();
    return (rect.width || 0) >= 80 && (rect.height || 0) >= 80;
  }).length;
  const videos = Array.from(document.querySelectorAll('video, video source')).map(el => el.currentSrc || el.src || '').filter(Boolean);
  const audios = Array.from(document.querySelectorAll('audio, audio source')).map(el => el.currentSrc || el.src || '').filter(Boolean);
  return {
    ok: best.text_length >= 80 || best.chinese_char_count >= 20 || best.latin_word_count >= 60,
    status: loginWallDetected && best.text_length < 120 ? 'login_or_verification_wall' : 'webpage_rendered_extracted',
    final_url: location.href,
    title,
    author,
    published_at: publishedAt,
    description,
    main_text: best.text.slice(0, 30000),
    visible_text: best.text.slice(0, 30000),
    text_length: Math.min(best.text.length, 30000),
    chinese_char_count: chineseCount(best.text.slice(0, 30000)),
    latin_word_count: latinWordCount(best.text.slice(0, 30000)),
    extraction_source: best.source,
    candidate_count: candidates.length,
    image_count: imageCount,
    has_video: videos.length > 0,
    video_source_count: videos.length,
    audio_source_count: audios.length,
    login_wall_detected: loginWallDetected,
    body_text_length: visibleText.length,
  };
}

(async () => {
  let cdp;
  try {
    const targets = await fetchJson('/json/list', 15000);
    const pageTarget = targets.find(t => t.type === 'page' && t.webSocketDebuggerUrl);
    if (!pageTarget) throw new Error('No CDP page target found');
    cdp = await makeCdp(pageTarget.webSocketDebuggerUrl);
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
      source: mediaPlaybackGuardScript(),
    }).catch(() => {});
    await cdp.send('Page.navigate', { url: targetUrl });
    await sleep(3500);
    await cdp.send('Runtime.evaluate', {
      expression: mediaPlaybackGuardScript(),
      returnByValue: true,
    }).catch(() => {});
    const started = Date.now();
    let latest = {};
    while (Date.now() - started < timeoutMs) {
      const result = await cdp.send('Runtime.evaluate', {
        expression: `(${extractionScript.toString()})()`,
        returnByValue: true,
        awaitPromise: true,
      }).catch(err => ({ result: { value: { ok: false, status: 'evaluate_failed', error: String(err && err.message || err) } } }));
      latest = result.result && result.result.value || {};
      if ((latest.chinese_char_count || 0) >= 80 || (latest.latin_word_count || 0) >= 90 || (latest.text_length || 0) >= 900) break;
      if (latest.status === 'login_or_verification_wall' && Date.now() - started > 8000) break;
      await cdp.send('Runtime.evaluate', {
        expression: 'window.scrollBy(0, Math.max(700, window.innerHeight || 900));',
        returnByValue: true,
      }).catch(() => {});
      await sleep(1500);
    }
    await cdp.send('Runtime.evaluate', {
      expression: `(() => {
        for (const media of document.querySelectorAll('video,audio')) {
          try { media.pause(); media.muted = true; media.removeAttribute('autoplay'); } catch (_) {}
        }
        return true;
      })()`,
      returnByValue: true,
    }).catch(() => {});
    cdp.close();
    browser.kill();
    console.log(JSON.stringify({
      ...latest,
      used_mcp: false,
      browser_stderr_tail: browserStderr.slice(-1200),
    }));
  } catch (err) {
    if (cdp) cdp.close();
    browser.kill();
    console.log(JSON.stringify({
      ok: false,
      used_mcp: false,
      status: 'rendered_cdp_failed',
      error: String(err && err.message || err),
      browser_stderr_tail: browserStderr.slice(-1200),
    }));
    process.exit(1);
  }
})();
"""


def run_rendered_extract(
    *,
    browser: Path,
    url: str,
    profile: Path,
    timeout_sec: int,
    headed: bool,
) -> tuple[int, dict[str, Any], str]:
    completed = subprocess.run(
        [
            "node",
            "-e",
            node_cdp_script(),
            str(browser),
            url,
            str(profile),
            "1" if headed else "0",
            str(max(10, timeout_sec) * 1000),
        ],
        cwd=str(PROJECT_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_sec + 35,
    )
    try:
        data = json.loads((completed.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        data = {
            "ok": False,
            "status": "invalid_rendered_json",
            "error": f"Invalid rendered JSON: {(completed.stdout or '')[-1000:]}",
        }
    return completed.returncode, data, completed.stderr or ""


def rendered_material_to_webpage(url: str, rendered: dict[str, Any]) -> dict[str, Any]:
    main_text = str(rendered.get("main_text") or rendered.get("visible_text") or "")[:30000]
    return {
        "ok": bool(rendered.get("ok")),
        "used_mcp": False,
        "stage": "fetch_webpage",
        "status": rendered.get("status") or ("webpage_rendered_extracted" if rendered.get("ok") else "webpage_rendered_incomplete"),
        "source_type": "webpage",
        "content_kind": "webpage",
        "original_url": redact_url(url),
        "final_url": redact_url(str(rendered.get("final_url") or url)),
        "title": rendered.get("title") or "",
        "author": rendered.get("author") or "",
        "published_at": rendered.get("published_at") or "",
        "description": rendered.get("description") or "",
        "visible_text": main_text,
        "main_text": main_text,
        "text_length": len(main_text),
        "chinese_char_count": chinese_char_count(main_text),
        "latin_word_count": latin_word_count(main_text),
        "image_count": int(rendered.get("image_count") or 0),
        "has_video": bool(rendered.get("has_video")),
        "video_source_count": int(rendered.get("video_source_count") or 0),
        "audio_source_count": int(rendered.get("audio_source_count") or 0),
        "status_code": None,
        "content_type": "text/html; rendered=1",
        "extraction_source": rendered.get("extraction_source") or "rendered_dom",
        "rendered_fallback_used": True,
        "rendered_candidate_count": rendered.get("candidate_count") or 0,
        "should_continue_transcribe": False,
        "need_ocr": bool((rendered.get("image_count") or 0) and chinese_char_count(main_text) < 80),
        "can_try_comments": False,
        "error": rendered.get("error"),
    }


def merge_static_and_rendered_material(static: dict[str, Any], rendered: dict[str, Any], url: str) -> dict[str, Any]:
    rendered_material = rendered_material_to_webpage(url, rendered)
    if not rendered_material.get("ok"):
        merged = dict(static)
        merged.update({
            "rendered_fallback_used": True,
            "rendered_fallback_ok": False,
            "rendered_fallback_status": rendered.get("status") or "rendered_failed",
            "rendered_fallback_error": rendered.get("error"),
        })
        return merged
    static_quality = text_quality(static)
    rendered_quality = text_quality(rendered_material)
    use_rendered = (
        rendered_quality["chinese_char_count"] > static_quality["chinese_char_count"] + 20
        or rendered_quality["latin_word_count"] > static_quality["latin_word_count"] + 40
        or rendered_quality["text_length"] > static_quality["text_length"] * 1.5
        or not material_has_primary_text(static)
    )
    if not use_rendered:
        merged = dict(static)
        merged.update({
            "rendered_fallback_used": True,
            "rendered_fallback_ok": True,
            "rendered_fallback_status": rendered_material.get("status"),
            "rendered_text_length": rendered_material.get("text_length") or 0,
            "rendered_chinese_char_count": rendered_material.get("chinese_char_count") or 0,
            "rendered_latin_word_count": rendered_material.get("latin_word_count") or 0,
        })
        return merged
    if not rendered_material.get("title"):
        rendered_material["title"] = static.get("title") or ""
    if not rendered_material.get("description"):
        rendered_material["description"] = static.get("description") or ""
    rendered_material["status_code"] = static.get("status_code")
    rendered_material["content_type"] = static.get("content_type") or rendered_material.get("content_type")
    rendered_material["static_extraction_source"] = static.get("extraction_source") or ""
    rendered_material["static_text_length"] = static_quality["text_length"]
    rendered_material["static_chinese_char_count"] = static_quality["chinese_char_count"]
    rendered_material["static_latin_word_count"] = static_quality["latin_word_count"]
    rendered_material["extraction_source"] = f"html_parser+{rendered_material.get('extraction_source') or 'rendered_dom'}"
    return rendered_material


def build_content_payload(url: str, material: dict[str, Any]) -> dict[str, Any]:
    payload = dict(material)
    payload["ok"] = bool(material.get("ok"))
    payload["used_mcp"] = False
    payload["stage"] = "fetch_webpage"
    payload["source_type"] = "webpage"
    payload["content_kind"] = "webpage"
    payload["original_url"] = redact_url(url)
    payload["final_url"] = material.get("final_url") or redact_url(url)
    payload["status"] = material.get("status") or ("webpage_extracted" if material.get("ok") else "webpage_extract_incomplete")
    payload["should_continue_transcribe"] = False
    payload["need_ocr"] = bool(payload.get("need_ocr"))
    payload["can_try_comments"] = False
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch generic webpage text without MCP.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--job-dir")
    parser.add_argument("--runtime-dir", default="runtime/jobs")
    parser.add_argument("--timeout-sec", type=int, default=35)
    parser.add_argument("--max-bytes", type=int, default=2_000_000)
    parser.add_argument("--browser-path")
    parser.add_argument("--headed", action="store_true", help="Show the browser window while rendered fallback extracts text.")
    parser.add_argument("--use-temp-profile", action="store_true", help="Use a fresh temporary browser profile for rendered fallback.")
    parser.add_argument("--disable-rendered-fallback", action="store_true", help="Skip local browser rendered text fallback.")
    parser.add_argument("--force-rendered", action="store_true", help="Always run local browser rendered text extraction after static fetch.")
    args = parser.parse_args()

    job_dir = make_job_dir(args)
    job_dir.mkdir(parents=True, exist_ok=True)
    write_json(job_dir / "input.json", {
        "url": redact_url(args.url),
        "received_at": now_iso(),
        "job_id": job_dir.name,
        "source_type": "webpage",
    })

    result: dict[str, Any] = {
        "ok": False,
        "used_mcp": False,
        "stage": "fetch_webpage",
        "status": "not_started",
        "job_id": job_dir.name,
        "job_dir": str(job_dir),
        "url": redact_url(args.url),
        "source_type": "webpage",
        "error": None,
    }

    if not is_supported_webpage_url(args.url):
        result.update({"status": "url_rejected", "error": "Only http/https webpage URLs are supported."})
        write_json(job_dir / "webpage.json", result)
        write_json(job_dir / "content.json", build_content_payload(args.url, result))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    try:
        fetch_meta, raw = fetch_url(args.url, max(5, args.timeout_sec), max(100_000, args.max_bytes))
        content_type = str(fetch_meta.get("content_type") or "")
        if "html" in content_type.casefold() or "text/plain" in content_type.casefold() or not content_type:
            text = decode_body(raw, content_type)
            material = (
                extract_html_material(text, args.url, content_type)
                if "html" in content_type.casefold() or "<html" in text[:1000].casefold()
                else {
                    "ok": len(text.strip()) >= 40,
                    "used_mcp": False,
                    "stage": "fetch_webpage",
                    "status": "webpage_extracted",
                    "source_type": "webpage",
                    "content_kind": "webpage",
                    "original_url": redact_url(args.url),
                    "final_url": fetch_meta.get("final_url") or redact_url(args.url),
                    "title": "",
                    "author": "",
                    "published_at": "",
                    "description": "",
                    "visible_text": text[:30000],
                    "main_text": text[:30000],
                    "text_length": len(text[:30000]),
                    "chinese_char_count": chinese_char_count(text[:30000]),
                    "latin_word_count": latin_word_count(text[:30000]),
                    "image_count": 0,
                    "has_video": False,
                    "video_source_count": 0,
                    "audio_source_count": 0,
                    "content_type": content_type,
                    "extraction_source": "plain_text",
                    "should_continue_transcribe": False,
                    "need_ocr": False,
                    "can_try_comments": False,
                    "error": None,
                }
            )
            material["final_url"] = fetch_meta.get("final_url") or material.get("final_url") or redact_url(args.url)
            material["status_code"] = fetch_meta.get("status_code")
            material["truncated"] = bool(fetch_meta.get("truncated"))
            if not material.get("ok"):
                material["status"] = "webpage_extract_incomplete"
            if "html" in content_type.casefold() or "<html" in text[:1000].casefold():
                if should_try_rendered_fallback(
                    text,
                    material,
                    force=bool(args.force_rendered),
                    disabled=bool(args.disable_rendered_fallback),
                ):
                    browser = find_browser(args.browser_path)
                    if browser:
                        if args.use_temp_profile:
                            profile = Path(tempfile.mkdtemp(prefix="LucasWebpageRender-"))
                            profile_source = "temp_profile"
                        else:
                            profile = profile_dir("webpage")
                            profile_source = "persistent_webpage_profile"
                        profile.mkdir(parents=True, exist_ok=True)
                        render_timeout = min(max(10, args.timeout_sec), 90)
                        exit_code, rendered, stderr = run_rendered_extract(
                            browser=browser,
                            url=args.url,
                            profile=profile,
                            timeout_sec=render_timeout,
                            headed=bool(args.headed),
                        )
                        rendered.setdefault("ok", False)
                        if exit_code != 0 and not rendered.get("error"):
                            rendered["error"] = stderr[-1200:] or f"rendered extractor exited with code {exit_code}"
                        material = merge_static_and_rendered_material(material, rendered, args.url)
                        material.update({
                            "rendered_fallback_exit_code": exit_code,
                            "rendered_fallback_profile_source": profile_source,
                            "rendered_fallback_profile_dir": str(profile),
                            "rendered_fallback_browser": str(browser),
                        })
                        if rendered.get("browser_stderr_tail"):
                            material["rendered_browser_stderr_tail"] = clean_text(rendered.get("browser_stderr_tail"))[:1200]
                    else:
                        material.update({
                            "rendered_fallback_used": False,
                            "rendered_fallback_status": "browser_unavailable",
                            "rendered_fallback_error": "No supported Edge/Chrome executable found.",
                        })
        else:
            material = {
                "ok": False,
                "used_mcp": False,
                "stage": "fetch_webpage",
                "status": "unsupported_content_type",
                "source_type": "webpage",
                "content_kind": "webpage",
                "original_url": redact_url(args.url),
                "final_url": fetch_meta.get("final_url") or redact_url(args.url),
                "title": "",
                "author": "",
                "published_at": "",
                "description": "",
                "visible_text": "",
                "main_text": "",
                "text_length": 0,
                "chinese_char_count": 0,
                "image_count": 0,
                "has_video": False,
                "video_source_count": 0,
                "audio_source_count": 0,
                "content_type": content_type,
                "status_code": fetch_meta.get("status_code"),
                "should_continue_transcribe": False,
                "need_ocr": False,
                "can_try_comments": False,
                "error": f"Unsupported content type: {content_type}",
            }
    except urllib.error.HTTPError as exc:
        material = {
            "ok": False,
            "status": "http_error",
            "error": f"HTTP {exc.code}: {exc.reason}",
            "status_code": exc.code,
        }
    except urllib.error.URLError as exc:
        material = {
            "ok": False,
            "status": "network_error",
            "error": str(exc.reason),
        }
    except Exception as exc:
        material = {
            "ok": False,
            "status": "webpage_fetch_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }

    content = build_content_payload(args.url, material)
    write_json(job_dir / "webpage.json", content)
    write_json(job_dir / "content.json", content)
    result.update({
        "ok": bool(content.get("ok")),
        "status": content.get("status"),
        "title": content.get("title") or "",
        "final_url": content.get("final_url") or "",
        "text_length": content.get("text_length") or 0,
        "chinese_char_count": content.get("chinese_char_count") or 0,
        "latin_word_count": content.get("latin_word_count") or 0,
        "image_count": content.get("image_count") or 0,
        "content_type": content.get("content_type") or "",
        "extraction_source": content.get("extraction_source") or "",
        "rendered_fallback_used": bool(content.get("rendered_fallback_used")),
        "rendered_fallback_status": content.get("rendered_fallback_status") or content.get("status"),
        "material_path": str(job_dir / "webpage.json"),
        "content_path": str(job_dir / "content.json"),
        "error": content.get("error"),
    })
    write_json(job_dir / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if content.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
