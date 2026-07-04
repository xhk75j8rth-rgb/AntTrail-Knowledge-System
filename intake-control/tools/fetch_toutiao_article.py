#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BROWSER_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def make_job_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-toutiao-{uuid.uuid4().hex[:6]}"


def default_profile_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "LucasKnowledgeDB" / "browser_profiles" / "toutiao"


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def article_id_from_url(url: str) -> str:
    match = re.search(r"/article/(\d+)", url)
    return match.group(1) if match else ""


def is_toutiao_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return parsed.scheme in {"http", "https"} and (host == "toutiao.com" or host.endswith(".toutiao.com"))


def find_browser(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    for candidate in BROWSER_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


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


def node_cdp_script() -> str:
    return r"""
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const browserPath = process.argv[1];
const targetUrl = process.argv[2];
const profileDir = process.argv[3];
const headed = process.argv[4] === '1';
const timeoutMs = Number(process.argv[5] || 45000);
const port = 10500 + Math.floor(Math.random() * 1000);

fs.mkdirSync(profileDir, { recursive: true });

const browserArgs = [
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${profileDir}`,
  '--profile-directory=Default',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-background-networking',
  '--disable-sync',
  '--lang=zh-CN',
  'about:blank',
];
if (!headed) {
  browserArgs.push('--headless=new', '--disable-gpu');
}

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
    const listeners = [];
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
        onEvent(fn) {
          listeners.push(fn);
        },
        close() {
          ws.close();
        },
      });
    };
    ws.onerror = err => reject(new Error(String(err && err.message || err)));
    ws.onmessage = event => {
      const msg = JSON.parse(event.data);
      if (msg.id && pending.has(msg.id)) {
        const item = pending.get(msg.id);
        pending.delete(msg.id);
        clearTimeout(item.timer);
        if (msg.error) item.rej(new Error(msg.error.message || JSON.stringify(msg.error)));
        else item.res(msg.result || {});
        return;
      }
      if (msg.method) {
        for (const listener of listeners) listener(msg);
      }
    };
  });
}

function extractionScript() {
  function clean(value) {
    return String(value || '').replace(/\u00a0/g, ' ').replace(/[ \t\r\f\v]+/g, ' ').trim();
  }
  function normalizeLines(text) {
    const blocked = [
      /^展开全文$/, /^打开今日头条/, /^相关推荐$/, /^评论$/, /^写评论/, /^登录后/, /^广告$/,
      /^更多精彩/, /^举报$/, /^收藏$/, /^点赞$/, /^分享$/, /^关注$/, /^已关注$/,
      /^输入验证码/, /^请完成安全验证/, /^扫码登录/, /^手机号登录/, /^密码登录$/,
    ];
    const seen = new Set();
    const lines = [];
    for (const raw of String(text || '').split(/\n+/)) {
      const line = clean(raw);
      if (!line) continue;
      if (line.length <= 1) continue;
      if (blocked.some(pattern => pattern.test(line))) continue;
      if (seen.has(line)) continue;
      seen.add(line);
      lines.push(line);
    }
    return lines;
  }
  function chineseCount(text) {
    return (String(text || '').match(/[\u4e00-\u9fff]/g) || []).length;
  }
  function meta(name) {
    const el = document.querySelector(`meta[name="${name}"], meta[property="${name}"]`);
    return el ? clean(el.getAttribute('content')) : '';
  }
  function selectorText(selectors) {
    for (const selector of selectors) {
      const el = document.querySelector(selector);
      const text = el ? clean(el.innerText || el.textContent) : '';
      if (text) return text;
    }
    return '';
  }
  function safeJsonParse(raw) {
    const attempts = [raw];
    try { attempts.push(decodeURIComponent(raw)); } catch (_) {}
    try { attempts.push(JSON.parse(`"${String(raw).replace(/"/g, '\\"')}"`)); } catch (_) {}
    for (const attempt of attempts) {
      try {
        const parsed = JSON.parse(attempt);
        if (parsed && typeof parsed === 'object') return parsed;
      } catch (_) {}
    }
    return null;
  }
  function findValue(root, keys, maxDepth = 8) {
    const queue = [{ value: root, depth: 0 }];
    const seen = new Set();
    while (queue.length) {
      const item = queue.shift();
      if (!item || item.depth > maxDepth) continue;
      const value = item.value;
      if (!value || typeof value !== 'object' || seen.has(value)) continue;
      seen.add(value);
      for (const key of keys) {
        if (Object.prototype.hasOwnProperty.call(value, key)) {
          const found = value[key];
          if (found !== undefined && found !== null && String(found).trim() !== '') return found;
        }
      }
      const children = Array.isArray(value) ? value.slice(0, 120) : Object.values(value).slice(0, 160);
      for (const child of children) queue.push({ value: child, depth: item.depth + 1 });
    }
    return '';
  }
  function stateRoots() {
    const roots = [
      window.__INITIAL_STATE__,
      window.__NEXT_DATA__,
      window.__NUXT__,
      window.__RENDER_DATA__,
      window.RENDER_DATA,
      window._SSR_DATA,
      window.__data,
    ].filter(Boolean);
    for (const script of document.querySelectorAll('script')) {
      const text = script.textContent || '';
      if (!/(article|content|title|abstract|media_name|publish|group_id|765758)/i.test(text)) continue;
      const trimmed = text.trim();
      if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
        const parsed = safeJsonParse(trimmed);
        if (parsed) roots.push(parsed);
      }
      const patterns = [
        /<script[^>]*id=["']RENDER_DATA["'][^>]*>([\s\S]*?)<\/script>/ig,
        /window\.__INITIAL_STATE__\s*=\s*({[\s\S]*?});?\s*$/i,
        /window\._SSR_DATA\s*=\s*({[\s\S]*?});?\s*$/i,
      ];
      for (const pattern of patterns) {
        let match;
        while ((match = pattern.exec(text)) !== null) {
          const parsed = safeJsonParse(match[1]);
          if (parsed) roots.push(parsed);
        }
      }
    }
    return roots;
  }
  function htmlToText(value) {
    const box = document.createElement('div');
    box.innerHTML = String(value || '');
    return normalizeLines(box.innerText || box.textContent || '').join('\n');
  }
  function bestStateArticle() {
    const roots = stateRoots();
    for (const root of roots) {
      const title = clean(findValue(root, ['title', 'article_title', 'display_title']));
      const content = findValue(root, ['content', 'article_content', 'content_html', 'articleContent', 'detail_source_content']);
      const text = typeof content === 'string' && /<[^>]+>/.test(content) ? htmlToText(content) : normalizeLines(content).join('\n');
      if (chineseCount(text) >= 80 || chineseCount(title) >= 4) {
        return {
          title,
          author: clean(findValue(root, ['media_name', 'source', 'author', 'name', 'user_name'])),
          published_at: clean(findValue(root, ['publish_time', 'publishTime', 'create_time', 'createTime', 'datetime'])),
          abstract: clean(findValue(root, ['abstract', 'description', 'seo_description'])),
          text,
        };
      }
    }
    return {};
  }
  function candidateElements() {
    const selectors = [
      'article',
      '[class*="article-content"]',
      '[class*="articleContent"]',
      '[class*="article-content"]',
      '[class*="content"]',
      '[class*="detail"]',
      '[id*="article"]',
      'main',
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
    return Array.from(elements);
  }
  function bestDomArticle() {
    const candidates = [];
    for (const el of candidateElements()) {
      const lines = normalizeLines(el.innerText || el.textContent || '');
      const text = lines.join('\n');
      const score = chineseCount(text) - Math.max(0, lines.length - 80) * 2;
      candidates.push({ text, lines, score, selector: el.tagName.toLowerCase() + (el.id ? `#${el.id}` : '') });
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0] || { text: '', lines: [], score: 0, selector: '' };
  }
  function findPublishText(visibleText) {
    const patterns = [
      /\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?\s+\d{1,2}:\d{2}/,
      /\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?/,
      /发布于\s*([^\n]{6,30})/,
    ];
    for (const pattern of patterns) {
      const match = String(visibleText || '').match(pattern);
      if (match) return clean(match[1] || match[0]);
    }
    return '';
  }
  function findByline(lines, title) {
    const titleIndex = lines.findIndex(line => line === title || line.includes(title) || title.includes(line));
    const nearby = titleIndex >= 0 ? lines.slice(titleIndex + 1, titleIndex + 6) : lines.slice(0, 20);
    for (const line of nearby) {
      const match = line.match(/(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?\s+\d{1,2}:\d{2})\s*[·・]\s*(.+)$/);
      if (match) return { published_at: clean(match[1]), author: clean(match[2]) };
    }
    for (const line of nearby) {
      const match = line.match(/(.+?)\s*[·・]\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?\s+\d{1,2}:\d{2})$/);
      if (match) return { author: clean(match[1]), published_at: clean(match[2]) };
    }
    return {};
  }
  function cleanAuthor(value, fallback = '') {
    const blocked = /^(关注|已关注|TA的热门作品|查看更多|推荐|北京|视频|财经|更多|搜索|消息|发布|登录|评论|收藏|分享)$/;
    for (const line of normalizeLines(value)) {
      if (blocked.test(line)) continue;
      if (/官方账号$/.test(line)) continue;
      if (/^\d+$/.test(line)) continue;
      return line;
    }
    return clean(fallback);
  }
  function trimArticleText(text, title, byline) {
    const lines = normalizeLines(text);
    let start = 0;
    const titleIndex = lines.findIndex(line => line === title || line.includes(title) || title.includes(line));
    if (titleIndex >= 0) start = titleIndex + 1;

    const cleaned = [];
    for (let index = start; index < lines.length; index += 1) {
      const line = lines[index];
      if (!line) continue;
      if (line === title) continue;
      if (byline && (line === byline.author || line === byline.published_at)) continue;
      if (/^\d+$/.test(line) && index < start + 4) continue;
      if (/^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?\s+\d{1,2}:\d{2}\s*[·・]/.test(line)) continue;
      if (/^(推荐|北京|视频|财经|更多|搜索|消息|发布|登录|收藏|分享)$/.test(line)) continue;
      if (/^评论\s*\d*/.test(line)) break;
      if (/^请先\s*登录/.test(line)) break;
      if (/^查看全部\s*\d*\s*条评论/.test(line)) break;
      if (/^(TA的热门作品|头条热榜|扫码下载今日头条|看最新、最热资讯内容|首页|反馈|下载|顶部)$/.test(line)) break;
      cleaned.push(line);
    }
    return cleaned.join('\n');
  }

  const visibleText = document.body ? document.body.innerText || '' : '';
  const state = bestStateArticle();
  const dom = bestDomArticle();
  const title = clean(
    state.title ||
    selectorText(['h1', '[class*="title"]']) ||
    meta('og:title') ||
    document.title.replace(/今日头条.*/, '')
  );
  const visibleLines = normalizeLines(visibleText);
  const byline = findByline(visibleLines, title);
  const author = cleanAuthor(
    byline.author ||
    state.author ||
    selectorText(['[class*="author"]', '[class*="source"]', '[class*="media"]']) ||
    meta('author')
  );
  const publishedAt = clean(
    byline.published_at ||
    state.published_at ||
    selectorText(['time', '[class*="time"]', '[class*="date"]']) ||
    meta('article:published_time') ||
    findPublishText(visibleText)
  );
  const description = clean(state.abstract || meta('description') || meta('og:description'));
  const rawMainText = chineseCount(state.text) >= Math.min(80, chineseCount(dom.text)) ? state.text : dom.text;
  const mainText = trimArticleText(rawMainText, title, { author, published_at: publishedAt });
  return {
    final_url: location.href,
    title,
    author,
    published_at: publishedAt,
    description,
    main_text: mainText.slice(0, 30000),
    text_length: mainText.length,
    chinese_char_count: chineseCount(mainText),
    extraction_source: chineseCount(state.text) >= Math.min(80, chineseCount(dom.text)) ? 'page_state' : 'rendered_dom',
    dom_selector: dom.selector,
    visible_text_sample: clean(visibleText).slice(0, 1000),
    page_title: document.title || '',
    login_wall_detected: /登录后|扫码登录|手机号登录|请完成安全验证|输入验证码/.test(visibleText),
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
    await cdp.send('Network.enable');
    await cdp.send('Page.navigate', { url: targetUrl });

    const startedAt = Date.now();
    let latest = {};
    await sleep(2500);
    while (Date.now() - startedAt < timeoutMs) {
      await cdp.send('Runtime.evaluate', {
        expression: 'window.scrollBy(0, Math.max(600, window.innerHeight || 800));',
        returnByValue: true,
      }).catch(() => {});
      const result = await cdp.send('Runtime.evaluate', {
        expression: `(${extractionScript.toString()})()`,
        returnByValue: true,
        awaitPromise: true,
      }).catch(err => ({ result: { value: { error: String(err && err.message || err) } } }));
      latest = result.result && result.result.value || {};
      if ((latest.chinese_char_count || 0) >= 120 && latest.title) break;
      if (latest.login_wall_detected && Date.now() - startedAt > 8000) break;
      await sleep(2000);
    }
    cdp.close();
    browser.kill();
    console.log(JSON.stringify({
      ok: Boolean((latest.chinese_char_count || 0) >= 40 && latest.title),
      status: (latest.chinese_char_count || 0) >= 40 && latest.title ? 'article_extracted' : (latest.login_wall_detected ? 'login_or_verification_wall' : 'article_extract_incomplete'),
      ...latest,
      browser_stderr_tail: browserStderr.slice(-1200),
    }));
  } catch (err) {
    if (cdp) cdp.close();
    browser.kill();
    console.log(JSON.stringify({
      ok: false,
      status: 'cdp_failed',
      error: String(err && err.message || err),
      browser_stderr_tail: browserStderr.slice(-1200),
    }));
    process.exit(1);
  }
})();
"""


def run_browser_extract(
    browser: Path,
    url: str,
    profile_dir: Path,
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
            str(profile_dir),
            "1" if headed else "0",
            str(timeout_sec * 1000),
        ],
        cwd=str(PROJECT_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_sec + 30,
    )
    try:
        data = json.loads((completed.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        data = {
            "ok": False,
            "status": "invalid_extractor_json",
            "error": f"Invalid extractor JSON: {(completed.stdout or '')[-1000:]}",
        }
    return completed.returncode, data, completed.stderr or ""


def build_content_payload(url: str, article: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(article.get("ok")),
        "used_mcp": False,
        "stage": "fetch_toutiao_article",
        "source_type": "text/toutiao",
        "original_url": url,
        "final_url": article.get("final_url") or url,
        "title": article.get("title") or "",
        "author": article.get("author") or "",
        "published_at": article.get("published_at") or "",
        "description": article.get("description") or "",
        "visible_text": article.get("main_text") or "",
        "text_length": article.get("text_length") or 0,
        "chinese_char_count": article.get("chinese_char_count") or 0,
        "article_id": article_id_from_url(url),
        "status": article.get("status") or ("article_extracted" if article.get("ok") else "article_extract_failed"),
        "should_continue_transcribe": False,
        "need_ocr": False,
        "can_try_comments": False,
        "error": article.get("error"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch rendered Toutiao article text with local Edge/Chrome CDP; no MCP.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--job-dir")
    parser.add_argument("--runtime-dir", default="runtime/jobs")
    parser.add_argument("--timeout-sec", type=int, default=45)
    parser.add_argument("--browser-path")
    parser.add_argument("--profile-dir", default=os.environ.get("TOUTIAO_BROWSER_PROFILE", ""))
    parser.add_argument("--headed", action="store_true", help="Show the browser window while extracting.")
    parser.add_argument("--use-temp-profile", action="store_true", help="Use a fresh temporary browser profile instead of the persistent Toutiao login profile.")
    args = parser.parse_args()

    job_dir = make_job_dir(args)
    job_dir.mkdir(parents=True, exist_ok=True)
    write_json(job_dir / "input.json", {
        "url": args.url,
        "received_at": now_iso(),
        "job_id": job_dir.name,
        "source_type": "text/toutiao",
    })

    result: dict[str, Any] = {
        "ok": False,
        "used_mcp": False,
        "stage": "fetch_toutiao_article",
        "status": "not_started",
        "job_id": job_dir.name,
        "job_dir": str(job_dir),
        "url": args.url,
        "source_type": "text/toutiao",
        "article_id": article_id_from_url(args.url),
        "error": None,
    }

    if not is_toutiao_url(args.url):
        result.update({"status": "url_rejected", "error": "URL must be under toutiao.com."})
        write_json(job_dir / "toutiao_article.json", result)
        write_json(job_dir / "content.json", build_content_payload(args.url, result))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    browser = find_browser(args.browser_path)
    if not browser:
        result.update({"status": "browser_unavailable", "error": "No supported Edge/Chrome executable found."})
        write_json(job_dir / "toutiao_article.json", result)
        write_json(job_dir / "content.json", build_content_payload(args.url, result))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    if args.use_temp_profile:
        profile_dir = Path(tempfile.mkdtemp(prefix="LucasToutiaoArticle-"))
        profile_source = "temp_profile"
    else:
        profile_dir = Path(args.profile_dir).expanduser() if args.profile_dir else default_profile_dir()
        profile_source = "persistent_toutiao_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    exit_code, article, stderr = run_browser_extract(
        browser=browser,
        url=args.url,
        profile_dir=profile_dir,
        timeout_sec=max(10, args.timeout_sec),
        headed=bool(args.headed),
    )
    article.setdefault("ok", False)
    article.setdefault("status", "article_extract_failed")
    article.update({
        "used_mcp": False,
        "schema_name": "toutiao_article_material_v1",
        "schema_version": 1,
        "stage": "fetch_toutiao_article",
        "source_type": "text/toutiao",
        "source_url": args.url,
        "article_id": article_id_from_url(args.url),
        "job_id": job_dir.name,
        "job_dir": str(job_dir),
        "browser": str(browser),
        "profile_source": profile_source,
        "profile_dir": str(profile_dir),
        "extractor_exit_code": exit_code,
    })
    if exit_code != 0 and not article.get("error"):
        article["error"] = stderr[-1200:] or f"extractor exited with code {exit_code}"
    if article.get("browser_stderr_tail"):
        article["browser_stderr_tail"] = clean_text(article.get("browser_stderr_tail"))[:1200]

    write_json(job_dir / "toutiao_article.json", article)
    write_json(job_dir / "content.json", build_content_payload(args.url, article))

    result.update({
        "ok": bool(article.get("ok")),
        "status": article.get("status"),
        "title": article.get("title") or "",
        "author": article.get("author") or "",
        "published_at": article.get("published_at") or "",
        "final_url": article.get("final_url") or "",
        "text_length": article.get("text_length") or 0,
        "chinese_char_count": article.get("chinese_char_count") or 0,
        "extraction_source": article.get("extraction_source") or "",
        "profile_source": profile_source,
        "material_path": str(job_dir / "toutiao_article.json"),
        "content_path": str(job_dir / "content.json"),
        "error": article.get("error"),
    })
    write_json(job_dir / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if article.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
