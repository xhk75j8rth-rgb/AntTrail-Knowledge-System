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
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_STATUS_PATH = PROJECT_ROOT / "runtime" / "browser_sessions" / "xiaohongshu_session.json"
BROWSER_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]
SENSITIVE_QUERY_MARKERS = ("token", "secret", "key", "auth", "session", "cookie", "sid")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def make_job_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-xhs-{uuid.uuid4().hex[:6]}"


def default_profile_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "LucasKnowledgeDB" / "browser_profiles" / "xiaohongshu"


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def redact_url(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return url
    query_items = []
    redacted = False
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if any(marker in key.casefold() for marker in SENSITIVE_QUERY_MARKERS):
            query_items.append((key, "<redacted>"))
            redacted = True
        else:
            query_items.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(query_items), fragment="" if redacted else parsed.fragment))


def note_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    match = re.search(r"/(?:explore|item)/([^/?#]+)", parsed.path)
    if match:
        return match.group(1)
    match = re.search(r"/discovery/item/([^/?#]+)", parsed.path)
    return match.group(1) if match else ""


def is_xiaohongshu_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return parsed.scheme in {"http", "https"} and (
        host == "xiaohongshu.com"
        or host.endswith(".xiaohongshu.com")
        or host == "xhslink.com"
        or host.endswith(".xhslink.com")
    )


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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def cdp_port_ready(port: int) -> bool:
    if port <= 0:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def active_authorization_debug_port() -> int:
    payload = read_json(SESSION_STATUS_PATH)
    try:
        port = int(payload.get("debug_port") or 0)
    except (TypeError, ValueError):
        return 0
    return port if cdp_port_ready(port) else 0


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
const { spawn } = require('child_process');

const browserPath = process.argv[1];
const targetUrl = process.argv[2];
const profileDir = process.argv[3];
const headed = process.argv[4] === '1';
const timeoutMs = Number(process.argv[5] || 45000);
const existingPort = Number(process.argv[6] || 0);
const port = existingPort || (11400 + Math.floor(Math.random() * 1000));

fs.mkdirSync(profileDir, { recursive: true });

let browser = { kill() {} };
let browserStderr = '';
if (!existingPort) {
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
  if (!headed) {
    browserArgs.push('--headless=new', '--disable-gpu');
  }
  browser = spawn(browserPath, browserArgs, { stdio: ['ignore', 'ignore', 'pipe'] });
  browser.stderr.on('data', chunk => {
    browserStderr += chunk.toString();
    if (browserStderr.length > 4000) browserStderr = browserStderr.slice(-4000);
  });
}

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

async function createTemporaryTarget() {
  const response = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent('about:blank')}`, {
    method: 'PUT',
  });
  if (!response.ok) throw new Error(`CDP target creation failed: HTTP ${response.status}`);
  const target = await response.json();
  if (!target.webSocketDebuggerUrl) throw new Error('CDP target creation returned no websocket URL');
  return target;
}

async function selectPageTarget() {
  if (existingPort) return await createTemporaryTarget();
  const targets = await fetchJson('/json/list', 15000);
  const pageTarget = targets.find(t => t.type === 'page' && t.webSocketDebuggerUrl);
  if (!pageTarget) throw new Error('No CDP page target found');
  return pageTarget;
}

async function cleanupPage(cdp, pageTarget) {
  const cleanup = { paused_media: false, closed_page: false };
  if (!cdp) return cleanup;
  await cdp.send('Runtime.evaluate', {
    expression: `(() => {
      for (const media of document.querySelectorAll('video,audio')) {
        try { media.pause(); media.muted = true; media.removeAttribute('autoplay'); } catch (_) {}
      }
      return true;
    })()`,
    returnByValue: true,
  }).then(() => {
    cleanup.paused_media = true;
  }).catch(() => {});
  if (existingPort) {
    if (pageTarget && pageTarget.id) {
      await fetch(`http://127.0.0.1:${port}/json/close/${pageTarget.id}`).then(response => {
        cleanup.closed_page = response.ok;
      }).catch(() => {});
    }
    if (!cleanup.closed_page) {
      await Promise.race([
        cdp.send('Page.close').then(() => {
          cleanup.closed_page = true;
        }).catch(() => {}),
        sleep(800),
      ]);
    }
  }
  try { cdp.close(); } catch (_) {}
  return cleanup;
}

function extractionScript() {
  function clean(value) {
    return String(value || '').replace(/\u00a0/g, ' ').replace(/[ \t\r\f\v]+/g, ' ').trim();
  }
  function normalizeLines(text) {
    const blocked = [
      /^打开小红书/, /^小红书$/, /^首页$/, /^发现$/, /^消息$/, /^我$/, /^登录$/, /^注册$/,
      /^点赞$/, /^收藏$/, /^评论$/, /^分享$/, /^关注$/, /^已关注$/, /^说点什么/,
      /^相关推荐$/, /^相关笔记$/, /^更多$/, /^展开$/, /^收起$/, /^举报$/, /^广告$/,
      /^扫码登录$/, /^手机号登录$/, /^验证码$/, /^请完成安全验证/,
    ];
    const seen = new Set();
    const lines = [];
    for (const raw of String(text || '').split(/\n+/)) {
      const line = clean(raw);
      if (!line || line.length <= 1) continue;
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
  function cleanTitle(value) {
    return clean(value).replace(/\s*[-_]\s*小红书.*$/i, '').replace(/^小红书\s*[-_]\s*/i, '');
  }
  function normalizeCount(value) {
    const text = clean(value).replace(/,/g, '');
    const match = text.match(/(\d+(?:\.\d+)?)\s*([万亿wWkK]?)/);
    if (!match) return '';
    let number = Number(match[1]);
    if (!Number.isFinite(number)) return '';
    const unit = match[2] || '';
    if (unit === '万' || unit === 'w' || unit === 'W') number *= 10000;
    if (unit === '亿') number *= 100000000;
    if (unit === 'k' || unit === 'K') number *= 1000;
    return String(Math.round(number));
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
  function firstValue(obj, keys) {
    if (!obj || typeof obj !== 'object') return '';
    for (const key of keys) {
      if (Object.prototype.hasOwnProperty.call(obj, key)) {
        const value = obj[key];
        if (value !== undefined && value !== null && String(value).trim() !== '') return value;
      }
    }
    return '';
  }
  function findValue(root, keys, maxDepth = 6) {
    const queue = [{ value: root, depth: 0 }];
    const seen = new Set();
    while (queue.length) {
      const item = queue.shift();
      if (!item || item.depth > maxDepth) continue;
      const value = item.value;
      if (!value || typeof value !== 'object' || seen.has(value)) continue;
      seen.add(value);
      const direct = firstValue(value, keys);
      if (direct !== undefined && direct !== null && String(direct).trim() !== '') return direct;
      const children = Array.isArray(value) ? value.slice(0, 120) : Object.values(value).slice(0, 180);
      for (const child of children) queue.push({ value: child, depth: item.depth + 1 });
    }
    return '';
  }
  function parseStateRoots() {
    const roots = [
      window.__INITIAL_STATE__,
      window.__NEXT_DATA__,
      window.__NUXT__,
      window.__INITIAL_SSR_STATE__,
      window.__REDUX_STATE__,
      window.__XHS_STATE__,
    ].filter(Boolean);
    for (const script of document.querySelectorAll('script')) {
      const text = script.textContent || '';
      if (!text || !/(note|xhs|xiaohongshu|小红书|nickname|liked|collect|image|video|desc)/i.test(text)) continue;
      const trimmed = text.trim();
      if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
        const parsed = safeJsonParse(trimmed);
        if (parsed) roots.push(parsed);
      }
      for (const pattern of [
        /window\.__INITIAL_STATE__\s*=\s*({[\s\S]*?});?\s*$/i,
        /window\.__INITIAL_SSR_STATE__\s*=\s*({[\s\S]*?});?\s*$/i,
        /window\.__XHS_STATE__\s*=\s*({[\s\S]*?});?\s*$/i,
      ]) {
        const match = text.match(pattern);
        if (match) {
          const parsed = safeJsonParse(match[1]);
          if (parsed) roots.push(parsed);
        }
      }
    }
    return roots;
  }
  function mediaSignalsFromState(root) {
    const queue = [{ value: root, depth: 0 }];
    const seen = new Set();
    const imageUrls = new Set();
    const videoUrls = new Set();
    while (queue.length) {
      const item = queue.shift();
      if (!item || item.depth > 8) continue;
      const value = item.value;
      if (!value) continue;
      if (typeof value === 'string') {
        if (/https?:\/\/[^"' ]+\.(?:jpg|jpeg|png|webp)(?:\?|$)/i.test(value) || /xhscdn.*(?:image|img|webp|jpg|png)/i.test(value)) {
          imageUrls.add(value.split(/[?#]/)[0]);
        }
        if (/https?:\/\/[^"' ]+\.(?:mp4|m3u8|mov|m4v)(?:\?|$)/i.test(value) || /xhscdn.*(?:video|mp4|m3u8)/i.test(value)) {
          videoUrls.add(value.split(/[?#]/)[0]);
        }
        continue;
      }
      if (typeof value !== 'object' || seen.has(value)) continue;
      seen.add(value);
      const children = Array.isArray(value) ? value.slice(0, 200) : Object.values(value).slice(0, 220);
      for (const child of children) queue.push({ value: child, depth: item.depth + 1 });
    }
    return { imageUrls, videoUrls };
  }
  function bestStateNote() {
    let best = {};
    let bestScore = 0;
    const roots = parseStateRoots();
    for (const root of roots) {
      const queue = [{ value: root, depth: 0 }];
      const seen = new Set();
      while (queue.length) {
        const item = queue.shift();
        if (!item || item.depth > 8) continue;
        const value = item.value;
        if (!value || typeof value !== 'object' || seen.has(value)) continue;
        seen.add(value);
        const title = cleanTitle(firstValue(value, ['title', 'note_title', 'displayTitle', 'display_title']));
        const desc = clean(firstValue(value, ['desc', 'description', 'content', 'note_desc', 'noteDesc', 'text']));
        const noteId = clean(firstValue(value, ['note_id', 'noteId', 'id']));
        const score = chineseCount(title) * 2 + chineseCount(desc) * 3 + (noteId ? 10 : 0);
        if (score > bestScore && (chineseCount(title) >= 2 || chineseCount(desc) >= 8)) {
          const user = firstValue(value, ['user', 'userInfo', 'user_info', 'author']);
          const stats = firstValue(value, ['interactInfo', 'interact_info', 'statistics', 'stats']);
          bestScore = score;
          best = {
            title,
            main_text: desc,
            author: typeof user === 'object' ? clean(firstValue(user, ['nickname', 'name', 'user_name'])) : clean(user),
            published_at: clean(firstValue(value, ['time', 'publish_time', 'publishTime', 'created_at', 'create_time'])),
            like_count: normalizeCount(typeof stats === 'object' ? firstValue(stats, ['likedCount', 'liked_count', 'like_count']) : ''),
            collect_count: normalizeCount(typeof stats === 'object' ? firstValue(stats, ['collectedCount', 'collect_count', 'collected_count']) : ''),
            comment_count: normalizeCount(typeof stats === 'object' ? firstValue(stats, ['commentCount', 'comment_count']) : ''),
            share_count: normalizeCount(typeof stats === 'object' ? firstValue(stats, ['shareCount', 'share_count']) : ''),
            note_id: noteId,
          };
        }
        const children = Array.isArray(value) ? value.slice(0, 120) : Object.values(value).slice(0, 160);
        for (const child of children) queue.push({ value: child, depth: item.depth + 1 });
      }
    }
    const media = roots.reduce((acc, root) => {
      const found = mediaSignalsFromState(root);
      for (const item of found.imageUrls) acc.imageUrls.add(item);
      for (const item of found.videoUrls) acc.videoUrls.add(item);
      return acc;
    }, { imageUrls: new Set(), videoUrls: new Set() });
    best.state_image_count = media.imageUrls.size;
    best.state_video_count = media.videoUrls.size;
    best.state_video_sources = Array.from(media.videoUrls).slice(0, 10);
    return best;
  }
  function candidateElements() {
    const selectors = [
      '#detail-title',
      '[class*="note-content"]',
      '[class*="NoteContent"]',
      '[class*="desc"]',
      '[class*="content"]',
      '[class*="detail"]',
      'article',
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
    return Array.from(elements).filter(Boolean);
  }
  function bestDomText() {
    const candidates = [];
    for (const el of candidateElements()) {
      const lines = normalizeLines(el.innerText || el.textContent || '');
      const text = lines.join('\n');
      const score = chineseCount(text) - Math.max(0, lines.length - 80) * 2;
      candidates.push({ text, lines, score });
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0] || { text: '', lines: [], score: 0 };
  }
  function trimNoteText(text, title, author) {
    const lines = normalizeLines(text);
    const cleaned = [];
    let start = 0;
    const titleIndex = lines.findIndex(line => title && (line === title || line.includes(title) || title.includes(line)));
    if (titleIndex >= 0) start = titleIndex + 1;
    for (let index = start; index < lines.length; index += 1) {
      const line = lines[index];
      if (!line || line === title || line === author) continue;
      if (/^(评论|共\s*\d+\s*条评论|相关推荐|相关笔记|说点什么|登录后)/.test(line)) break;
      if (/^(赞|收藏|分享|关注|已关注)$/.test(line)) continue;
      cleaned.push(line);
      if (cleaned.join('').length > 12000) break;
    }
    return cleaned.join('\n');
  }
  function visibleCount(text, labels) {
    const escaped = labels.map(label => label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
    const patterns = [
      new RegExp(`(?:${escaped})[：:\\s]*(\\d+(?:\\.\\d+)?\\s*[万亿wWkK]?)`, 'i'),
      new RegExp(`(\\d+(?:\\.\\d+)?\\s*[万亿wWkK]?)[：:\\s]*(?:${escaped})`, 'i'),
    ];
    for (const pattern of patterns) {
      const match = String(text || '').match(pattern);
      if (match) return normalizeCount(match[1]);
    }
    return '';
  }
  function redactMedia(url) {
    try {
      const parsed = new URL(url);
      parsed.search = '';
      parsed.hash = '';
      return parsed.toString();
    } catch (_) {
      return String(url || '').split(/[?#]/)[0];
    }
  }

  const visibleText = document.body ? document.body.innerText || '' : '';
  const state = bestStateNote();
  const dom = bestDomText();
  const metaDescription = meta('description') || meta('og:description');
  const title = cleanTitle(
    state.title ||
    selectorText(['#detail-title', 'h1', '[class*="title"]']) ||
    meta('og:title') ||
    document.title
  );
  const author = clean(
    state.author ||
    selectorText(['[class*="author"] [class*="name"]', '[class*="user"] [class*="name"]', '[class*="nickname"]']) ||
    meta('author')
  );
  let mainText = chineseCount(state.main_text) >= 8 ? state.main_text : trimNoteText(dom.text, title, author);
  if (chineseCount(mainText) < 8 && metaDescription) mainText = cleanTitle(metaDescription).replace(title, '').trim();
  const domImages = Array.from(document.querySelectorAll('img'))
    .map(img => img.currentSrc || img.src || '')
    .filter(src => /xhscdn|xiaohongshu|sns-webpic|image|jpg|jpeg|png|webp/i.test(src));
  const domVideos = [];
  for (const video of document.querySelectorAll('video')) {
    if (video.currentSrc) domVideos.push(video.currentSrc);
    if (video.src) domVideos.push(video.src);
    for (const source of video.querySelectorAll('source')) {
      if (source.src) domVideos.push(source.src);
    }
  }
  const performanceSources = performance.getEntriesByType('resource').map(item => item.name || '');
  for (const src of performanceSources) {
    if (/\.(mp4|m3u8|mov|m4v)(\?|$)/i.test(src) || /xhscdn.*(?:video|mp4|m3u8)/i.test(src)) domVideos.push(src);
  }
  const imageSources = new Set([...domImages.map(redactMedia)]);
  const videoSources = new Set([...domVideos.map(redactMedia), ...(state.state_video_sources || []).map(redactMedia)]);
  const imageCount = Math.max(imageSources.size, Number(state.state_image_count || 0));
  const videoSourceCount = Math.max(videoSources.size, Number(state.state_video_count || 0));
  const loginWallDetected = /登录后|扫码登录|手机号登录|请完成安全验证|输入验证码|访问频繁|系统检测到|安全验证/.test(visibleText);
  const textLength = mainText.length;
  const chineseCharCount = chineseCount(mainText);
  return {
    final_url: location.href,
    title,
    author,
    published_at: clean(state.published_at || selectorText(['time', '[class*="time"]', '[class*="date"]'])),
    description: clean(metaDescription),
    main_text: mainText.slice(0, 30000),
    text_length: textLength,
    chinese_char_count: chineseCharCount,
    extraction_source: chineseCount(state.main_text) >= 8 ? 'page_state' : 'rendered_dom',
    page_title: document.title || '',
    visible_text_sample: clean(visibleText).slice(0, 1200),
    note_id: state.note_id || '',
    like_count: state.like_count || visibleCount(visibleText, ['点赞', '赞']),
    comment_count: state.comment_count || visibleCount(visibleText, ['评论']),
    collect_count: state.collect_count || visibleCount(visibleText, ['收藏']),
    share_count: state.share_count || visibleCount(visibleText, ['分享', '转发']),
    image_count: imageCount,
    video_source_count: videoSourceCount,
    video_sources: Array.from(videoSources).slice(0, 10),
    has_video: videoSourceCount > 0 || document.querySelectorAll('video').length > 0,
    note_type: videoSourceCount > 0 || document.querySelectorAll('video').length > 0
      ? 'video_note'
      : (imageCount > 0 ? 'image_text_note' : 'text_or_unknown_note'),
    login_wall_detected: loginWallDetected,
    body_text_length: visibleText.length,
  };
}

(async () => {
  let cdp;
  let pageTarget;
  try {
    pageTarget = await selectPageTarget();
    cdp = await makeCdp(pageTarget.webSocketDebuggerUrl);
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Network.enable');
    await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
      source: mediaPlaybackGuardScript(),
    }).catch(() => {});
    await cdp.send('Page.navigate', { url: targetUrl });

    const startedAt = Date.now();
    let latest = {};
    await sleep(3500);
    await cdp.send('Runtime.evaluate', {
      expression: mediaPlaybackGuardScript(),
      returnByValue: true,
    }).catch(() => {});
    while (Date.now() - startedAt < timeoutMs) {
      await cdp.send('Runtime.evaluate', {
        expression: 'window.scrollBy(0, Math.max(500, window.innerHeight || 800));',
        returnByValue: true,
      }).catch(() => {});
      const result = await cdp.send('Runtime.evaluate', {
        expression: `(${extractionScript.toString()})()`,
        returnByValue: true,
        awaitPromise: true,
      }).catch(err => ({ result: { value: { error: String(err && err.message || err) } } }));
      latest = result.result && result.result.value || {};
      if ((latest.chinese_char_count || 0) >= 20 && latest.title) break;
      if ((latest.image_count || 0) > 0 && latest.title && Date.now() - startedAt > 8000) break;
      if (latest.login_wall_detected && Date.now() - startedAt > 9000) break;
      await sleep(2000);
    }
    const pageCleanup = await cleanupPage(cdp, pageTarget);
    cdp = null;
    browser.kill();
    const ok = Boolean(((latest.chinese_char_count || 0) >= 8 || (latest.image_count || 0) > 0 || latest.has_video) && latest.title);
    console.log(JSON.stringify({
      ok,
      status: ok ? 'note_extracted' : (latest.login_wall_detected ? 'login_or_verification_wall' : 'note_extract_incomplete'),
      ...latest,
      opened_temporary_target: Boolean(existingPort),
      page_cleanup: pageCleanup,
      browser_stderr_tail: browserStderr.slice(-1200),
    }));
  } catch (err) {
    const pageCleanup = await cleanupPage(cdp, pageTarget).catch(() => ({ paused_media: false, closed_page: false }));
    cdp = null;
    browser.kill();
    console.log(JSON.stringify({
      ok: false,
      status: 'cdp_failed',
      error: String(err && err.message || err),
      opened_temporary_target: Boolean(existingPort && pageTarget),
      page_cleanup: pageCleanup,
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
    existing_debug_port: int = 0,
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
            str(existing_debug_port),
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


def build_content_payload(url: str, note: dict[str, Any]) -> dict[str, Any]:
    image_count = int(note.get("image_count") or 0)
    video_count = int(note.get("video_source_count") or 0)
    return {
        "ok": bool(note.get("ok")),
        "used_mcp": False,
        "stage": "fetch_xiaohongshu_note",
        "source_type": "mixed/xiaohongshu",
        "content_kind": "mixed_note",
        "original_url": redact_url(url),
        "source_url_redacted": redact_url(url),
        "final_url": redact_url(str(note.get("final_url") or url)),
        "final_url_redacted": redact_url(str(note.get("final_url") or url)),
        "title": note.get("title") or "",
        "author": note.get("author") or "",
        "published_at": note.get("published_at") or "",
        "description": note.get("description") or "",
        "visible_text": note.get("main_text") or "",
        "main_text": note.get("main_text") or "",
        "text_length": note.get("text_length") or 0,
        "chinese_char_count": note.get("chinese_char_count") or 0,
        "note_id": note.get("note_id") or note_id_from_url(url),
        "note_type": note.get("note_type") or "text_or_unknown_note",
        "has_video": bool(note.get("has_video")),
        "image_count": image_count,
        "video_source_count": video_count,
        "video_sources": note.get("video_sources") or [],
        "like_count": note.get("like_count") or "",
        "comment_count": note.get("comment_count") or "",
        "collect_count": note.get("collect_count") or "",
        "share_count": note.get("share_count") or "",
        "metrics_source": "xiaohongshu_note_cdp" if any(note.get(key) for key in ("like_count", "comment_count", "collect_count", "share_count")) else "",
        "metrics_status": "partial" if any(note.get(key) for key in ("like_count", "comment_count", "collect_count", "share_count")) else "unavailable",
        "status": note.get("status") or ("note_extracted" if note.get("ok") else "note_extract_failed"),
        "extraction_source": note.get("extraction_source") or "",
        "should_continue_transcribe": bool(note.get("has_video")),
        "need_ocr": bool(image_count or video_count or note.get("has_video")),
        "can_try_comments": False,
        "error": note.get("error"),
        "error_hint": note.get("error_hint"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Xiaohongshu note visible text and media signals with local Edge/Chrome CDP; no MCP.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--job-dir")
    parser.add_argument("--runtime-dir", default="runtime/jobs")
    parser.add_argument("--timeout-sec", type=int, default=45)
    parser.add_argument("--browser-path")
    parser.add_argument("--profile-dir", default=os.environ.get("XIAOHONGSHU_BROWSER_PROFILE", ""))
    parser.add_argument("--debug-port", type=int, default=0, help="Reuse an already opened Xiaohongshu authorization browser CDP port.")
    parser.add_argument("--headed", action="store_true", help="Show the browser window while extracting.")
    parser.add_argument("--use-temp-profile", action="store_true", help="Use a fresh temporary browser profile instead of the persistent Xiaohongshu login profile.")
    args = parser.parse_args()

    job_dir = make_job_dir(args)
    job_dir.mkdir(parents=True, exist_ok=True)
    write_json(job_dir / "input.json", {
        "url": redact_url(args.url),
        "url_redacted": redact_url(args.url),
        "received_at": now_iso(),
        "job_id": job_dir.name,
        "source_type": "mixed/xiaohongshu",
    })

    result: dict[str, Any] = {
        "ok": False,
        "used_mcp": False,
        "stage": "fetch_xiaohongshu_note",
        "status": "not_started",
        "job_id": job_dir.name,
        "job_dir": str(job_dir),
        "url": redact_url(args.url),
        "url_redacted": redact_url(args.url),
        "source_type": "mixed/xiaohongshu",
        "note_id": note_id_from_url(args.url),
        "error": None,
    }

    if not is_xiaohongshu_url(args.url):
        result.update({"status": "url_rejected", "error": "URL must be under xiaohongshu.com or xhslink.com."})
        write_json(job_dir / "xiaohongshu_note.json", result)
        write_json(job_dir / "content.json", build_content_payload(args.url, result))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    browser = find_browser(args.browser_path)
    if not browser:
        result.update({"status": "browser_unavailable", "error": "No supported Edge/Chrome executable found."})
        write_json(job_dir / "xiaohongshu_note.json", result)
        write_json(job_dir / "content.json", build_content_payload(args.url, result))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    if args.use_temp_profile:
        profile_dir = Path(tempfile.mkdtemp(prefix="LucasXiaohongshuNote-"))
        profile_source = "temp_profile"
        existing_debug_port = 0
    else:
        profile_dir = Path(args.profile_dir).expanduser() if args.profile_dir else default_profile_dir()
        profile_source = "persistent_xiaohongshu_profile"
        existing_debug_port = args.debug_port if args.debug_port and cdp_port_ready(args.debug_port) else active_authorization_debug_port()
        if existing_debug_port:
            profile_source = "persistent_xiaohongshu_authorization_cdp"
    profile_dir.mkdir(parents=True, exist_ok=True)

    exit_code, note, stderr = run_browser_extract(
        browser=browser,
        url=args.url,
        profile_dir=profile_dir,
        timeout_sec=max(10, args.timeout_sec),
        headed=bool(args.headed),
        existing_debug_port=existing_debug_port,
    )
    note.setdefault("ok", False)
    note.setdefault("status", "note_extract_failed")
    note.update({
        "used_mcp": False,
        "schema_name": "xiaohongshu_note_material_v1",
        "schema_version": 1,
        "stage": "fetch_xiaohongshu_note",
        "source_type": "mixed/xiaohongshu",
        "source_url": redact_url(args.url),
        "source_url_redacted": redact_url(args.url),
        "note_id": note.get("note_id") or note_id_from_url(args.url),
        "job_id": job_dir.name,
        "job_dir": str(job_dir),
        "browser": str(browser),
        "profile_source": profile_source,
        "profile_dir": str(profile_dir),
        "debug_port": existing_debug_port,
        "reused_authorization_browser": bool(existing_debug_port),
        "extractor_exit_code": exit_code,
    })
    if exit_code != 0 and not note.get("error"):
        note["error"] = stderr[-1200:] or f"extractor exited with code {exit_code}"
    if note.get("status") == "cdp_failed" and not existing_debug_port and not note.get("browser_stderr_tail"):
        note["error_hint"] = "persistent_profile_may_be_open_without_remote_debugging; close the old authorization window or re-authorize so the browser opens with a CDP port."
    if note.get("browser_stderr_tail"):
        note["browser_stderr_tail"] = clean_text(note.get("browser_stderr_tail"))[:1200]
    if note.get("final_url"):
        note["final_url"] = redact_url(str(note.get("final_url") or ""))

    write_json(job_dir / "xiaohongshu_note.json", note)
    write_json(job_dir / "content.json", build_content_payload(args.url, note))

    result.update({
        "ok": bool(note.get("ok")),
        "status": note.get("status"),
        "title": note.get("title") or "",
        "author": note.get("author") or "",
        "published_at": note.get("published_at") or "",
        "final_url": redact_url(str(note.get("final_url") or "")),
        "final_url_redacted": redact_url(str(note.get("final_url") or "")),
        "text_length": note.get("text_length") or 0,
        "chinese_char_count": note.get("chinese_char_count") or 0,
        "note_type": note.get("note_type") or "",
        "has_video": bool(note.get("has_video")),
        "image_count": note.get("image_count") or 0,
        "video_source_count": note.get("video_source_count") or 0,
        "extraction_source": note.get("extraction_source") or "",
        "profile_source": profile_source,
        "debug_port": existing_debug_port,
        "reused_authorization_browser": bool(existing_debug_port),
        "material_path": str(job_dir / "xiaohongshu_note.json"),
        "content_path": str(job_dir / "content.json"),
        "error": note.get("error"),
        "error_hint": note.get("error_hint"),
    })
    write_json(job_dir / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if note.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
