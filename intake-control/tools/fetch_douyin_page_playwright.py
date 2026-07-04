#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


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


def clean_text(text: Any) -> str:
    return " ".join(str(text or "").split()).strip()


def normalize_count_text(value: Any) -> str:
    text = clean_text(value).replace(",", "")
    if not text:
        return ""
    import re

    match = re.search(r"(\d+(?:\.\d+)?)\s*([万亿wWkK]?)", text)
    if not match:
        return ""
    number = float(match.group(1))
    unit = match.group(2)
    if unit in {"万", "w", "W"}:
        number *= 10_000
    elif unit == "亿":
        number *= 100_000_000
    elif unit in {"k", "K"}:
        number *= 1_000
    return str(int(round(number)))


def extract_visible_douyin_metadata(visible_text: str, description: str = "") -> dict[str, Any]:
    import re

    lines = [clean_text(line) for line in (visible_text or "").splitlines()]
    lines = [line for line in lines if line]
    result: dict[str, Any] = {
        "author": "",
        "published_at": "",
        "like_count": "",
        "comment_count": "",
        "collect_count": "",
        "share_count": "",
        "metrics_source": "",
        "metrics_status": "unavailable",
    }

    publish_index: int | None = None
    for index, line in enumerate(lines):
        match = re.search(r"发布时间[：:]\s*(\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?)", line)
        if match:
            result["published_at"] = match.group(1)
            publish_index = index
            break
    if not result["published_at"]:
        match = re.search(r"于(\d{4})(\d{2})(\d{2})发布", description or "")
        if match:
            result["published_at"] = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    if publish_index is not None:
        blocked_prefixes = ("粉丝", "获赞", "关注", "推荐视频", "全部评论", "请先登录", "大家都在搜", "举报")
        for line in lines[publish_index + 1: publish_index + 5]:
            if line and not line.startswith(blocked_prefixes):
                result["author"] = line
                break

    marker_index: int | None = None
    for marker in ("举报",):
        if marker in lines:
            marker_index = lines.index(marker)
            break
    if marker_index is None and publish_index is not None:
        marker_index = publish_index
    if marker_index is not None:
        count_tokens: list[str] = []
        for line in reversed(lines[:marker_index]):
            if re.fullmatch(r"\d+(?:\.\d+)?\s*[万亿wWkK]?", line):
                count_tokens.append(line)
                if len(count_tokens) == 4:
                    break
                continue
            if count_tokens:
                break
        count_tokens.reverse()
        if len(count_tokens) == 4:
            like, comment, collect, share = (normalize_count_text(token) for token in count_tokens)
            result.update({
                "like_count": like,
                "comment_count": comment,
                "collect_count": collect,
                "share_count": share,
                "metrics_source": "douyin_page_visible_text",
                "metrics_status": "success",
            })

    if not result["metrics_source"] and (result["author"] or result["published_at"]):
        result["metrics_source"] = "douyin_page_visible_text"
        result["metrics_status"] = "partial"
    return result


def refine_page_metadata(data: dict[str, Any]) -> dict[str, Any]:
    refined = dict(data)
    visible = extract_visible_douyin_metadata(
        str(refined.get("visible_text") or ""),
        str(refined.get("description") or ""),
    )
    for key in ("author", "published_at"):
        if visible.get(key):
            refined[key] = visible[key]
    visible_counts = [visible.get(key) for key in ("like_count", "comment_count", "collect_count", "share_count")]
    if any(visible_counts):
        for key in ("like_count", "comment_count", "collect_count", "share_count"):
            refined[key] = visible.get(key) or ""
        refined["metrics_source"] = visible.get("metrics_source") or "douyin_page_visible_text"
        refined["metrics_status"] = visible.get("metrics_status") or "success"
    elif visible.get("metrics_source") and not refined.get("metrics_source"):
        refined["metrics_source"] = visible.get("metrics_source")
        refined["metrics_status"] = visible.get("metrics_status") or "partial"
    return refined


def write_json(job_dir: Path, payload: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "douyin_page_playwright.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def base_payload(url: str) -> dict[str, Any]:
    return {
        "ok": False,
        "used_mcp": False,
        "stage": "douyin_page_cdp",
        "source": "local_browser_cdp",
        "url": url,
        "final_url": "",
        "title": "",
        "author": "",
        "description": "",
        "published_at": "",
        "like_count": "",
        "comment_count": "",
        "collect_count": "",
        "share_count": "",
        "engagement_samples": [],
        "metrics_source": "",
        "metrics_status": "not_started",
        "visible_text": "",
        "video_sources": [],
        "audio_sources": [],
        "response_count": 0,
        "status": "not_started",
        "error": None,
    }


def find_browser(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    for candidate in BROWSER_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def node_cdp_script() -> str:
    return r"""
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const browserPath = process.argv[1];
const targetUrl = process.argv[2];
const profileDirArg = process.argv[3] || '';
const headed = process.argv[4] === '1';
const timeoutMs = Number(process.argv[5] || 45000);
const port = 9800 + Math.floor(Math.random() * 500);
const userDataDir = profileDirArg || path.join(os.tmpdir(), 'LucasDouyinVideoSource', `profile-${Date.now()}-${Math.random().toString(16).slice(2)}`);
fs.mkdirSync(userDataDir, { recursive: true });

const browserArgs = [
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${userDataDir}`,
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
            }, 10000);
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

function isVideoUrl(url) {
  if (/\.(js|css)(\?|$)/i.test(url || '') ||
      /media-audio|mime_type=audio|mp4a|douyin-pc-web|xgplayer|\/libs\//i.test(url || '')) {
    return false;
  }
  return /\.(mp4|m3u8)(\?|$)/i.test(url || '') ||
    /playwm|play_addr|aweme\/v1\/play|mime_type=video_mp4|media-video|bytecdn/i.test(url || '');
}

function isAudioUrl(url) {
  if (/\.(js|css)(\?|$)/i.test(url || '') ||
      /douyin-pc-web|xgplayer|\/libs\//i.test(url || '')) {
    return false;
  }
  return /\.(m4a|mp3|aac|wav|ogg)(\?|$)/i.test(url || '') ||
    /media-audio|mime_type=audio|audio_mp4|mp4a/i.test(url || '');
}

function addSource(target, seen, url, predicate) {
  if (!url || seen.has(url) || !/^https?:\/\//i.test(url) || !predicate(url)) return;
  seen.add(url);
  target.push(url);
}

function pageSnapshot() {
  const cleanText = value => String(value || '').replace(/\s+/g, ' ').trim();
  const firstText = (...values) => {
    for (const value of values) {
      const text = cleanText(value);
      if (text) return text;
    }
    return '';
  };
  const normalizeCount = value => {
    const raw = cleanText(value).replace(/,/g, '');
    if (!raw || /^(赞|点赞|评论|收藏|分享|转发)$/.test(raw)) return '';
    const match = raw.match(/(\d+(?:\.\d+)?)\s*([万亿wWkK]?)/);
    if (!match) return '';
    const number = Number(match[1]);
    if (!Number.isFinite(number)) return '';
    const unit = match[2] || '';
    if (unit === '万' || unit === 'w' || unit === 'W') return String(Math.round(number * 10000));
    if (unit === '亿') return String(Math.round(number * 100000000));
    if (unit === 'k' || unit === 'K') return String(Math.round(number * 1000));
    return String(Math.round(number));
  };
  const meta = name => {
    const el = document.querySelector('meta[name="' + name + '"], meta[property="' + name + '"]');
    return el ? (el.getAttribute('content') || '') : '';
  };
  const candidateCountValues = (text, labels) => {
    const result = [];
    const escapedLabels = labels.map(label => label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
    const patterns = [
      new RegExp(`(?:${escapedLabels})[：:\\s]*(\\d+(?:\\.\\d+)?\\s*[万亿wWkK]?)`, 'ig'),
      new RegExp(`(\\d+(?:\\.\\d+)?\\s*[万亿wWkK]?)[：:\\s]*(?:${escapedLabels})`, 'ig'),
    ];
    for (const pattern of patterns) {
      let match;
      while ((match = pattern.exec(text || '')) !== null) {
        const count = normalizeCount(match[1]);
        if (count) result.push(count);
      }
    }
    return result;
  };
  const findObjectValue = (root, keys, maxDepth = 8) => {
    const queue = [{ value: root, depth: 0 }];
    const seen = new Set();
    while (queue.length) {
      const current = queue.shift();
      if (!current || current.depth > maxDepth) continue;
      const value = current.value;
      if (!value || typeof value !== 'object') continue;
      if (seen.has(value)) continue;
      seen.add(value);
      for (const key of keys) {
        if (Object.prototype.hasOwnProperty.call(value, key)) {
          const found = value[key];
          if (found !== undefined && found !== null && String(found).trim() !== '') return found;
        }
      }
      const children = Array.isArray(value) ? value.slice(0, 80) : Object.values(value).slice(0, 120);
      for (const item of children) queue.push({ value: item, depth: current.depth + 1 });
    }
    return '';
  };
  const safeJsonParseCandidate = raw => {
    const attempts = [raw];
    try { attempts.push(decodeURIComponent(raw)); } catch (_) {}
    try { attempts.push(JSON.parse(`"${raw.replace(/"/g, '\\"')}"`)); } catch (_) {}
    for (const attempt of attempts) {
      try {
        const parsed = JSON.parse(attempt);
        if (parsed && typeof parsed === 'object') return parsed;
      } catch (_) {}
    }
    return null;
  };
  const parseScriptJsonCandidates = () => {
    const candidates = [];
    for (const script of document.querySelectorAll('script')) {
      const text = script.textContent || '';
      if (!text || !/(aweme|author|nickname|digg|collect|comment|share|create_time|publish)/i.test(text)) continue;
      const trimmed = text.trim();
      if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
        candidates.push(trimmed);
        continue;
      }
      for (const pattern of [
        /window\.__pace_f\.push\(\[1,\s*"([\s\S]*?)"\]\)/g,
        /<script[^>]*id=["']RENDER_DATA["'][^>]*>([\s\S]*?)<\/script>/ig,
      ]) {
        let match;
        while ((match = pattern.exec(text)) !== null) candidates.push(match[1]);
      }
    }
    return candidates.slice(0, 20);
  };
  const timestampToText = value => {
    const text = cleanText(value);
    if (!text) return '';
    if (/^\d{10,13}$/.test(text)) {
      const ms = text.length === 13 ? Number(text) : Number(text) * 1000;
      const date = new Date(ms);
      if (!Number.isNaN(date.getTime())) return date.toISOString();
    }
    return text;
  };
  const collectMetadata = visibleText => {
    const stateRoots = [
      window.__INITIAL_STATE__,
      window.__NUXT__,
      window.__NEXT_DATA__,
      window.__RENDER_DATA__,
      window.RENDER_DATA,
      window.__douyin_pc_initial_state__,
    ].filter(Boolean);
    for (const parsed of parseScriptJsonCandidates().map(safeJsonParseCandidate).filter(Boolean)) {
      stateRoots.push(parsed);
    }
    const rootValue = keys => {
      for (const root of stateRoots) {
        const value = findObjectValue(root, keys);
        if (value !== undefined && value !== null && String(value).trim() !== '') return value;
      }
      return '';
    };
    const authorObject = rootValue(['author', 'user', 'authorInfo', 'author_info']);
    const author = typeof authorObject === 'object'
      ? firstText(authorObject.nickname, authorObject.name, authorObject.unique_id, authorObject.short_id)
      : firstText(authorObject);
    const statsObject = rootValue(['statistics', 'statisticsInfo', 'stats', 'status']);
    const statValue = keys => {
      if (statsObject && typeof statsObject === 'object') {
        const value = findObjectValue(statsObject, keys, 3);
        if (value !== undefined && value !== null && String(value).trim() !== '') return value;
      }
      return rootValue(keys);
    };
    const likeCount = firstText(
      normalizeCount(statValue(['digg_count', 'like_count', 'diggCount', 'likeCount'])),
      candidateCountValues(visibleText, ['点赞', '获赞', '赞'])[0]
    );
    const commentCount = firstText(
      normalizeCount(statValue(['comment_count', 'commentCount'])),
      candidateCountValues(visibleText, ['评论'])[0]
    );
    const collectCount = firstText(
      normalizeCount(statValue(['collect_count', 'collectCount', 'favorite_count', 'favoriteCount'])),
      candidateCountValues(visibleText, ['收藏'])[0]
    );
    const shareCount = firstText(
      normalizeCount(statValue(['share_count', 'shareCount', 'forward_count', 'forwardCount'])),
      candidateCountValues(visibleText, ['分享', '转发'])[0]
    );
    const publishedAt = firstText(
      timestampToText(rootValue(['create_time', 'createTime', 'publish_time', 'publishTime', 'published_at', 'publishedAt'])),
      meta('article:published_time'),
      meta('og:video:release_date')
    );
    const samples = [];
    for (const line of visibleText.split(/\n+/)) {
      const cleaned = cleanText(line);
      if (/(点赞|评论|收藏|分享|转发|发布时间|发布于)/.test(cleaned)) samples.push(cleaned.slice(0, 160));
      if (samples.length >= 8) break;
    }
    const metricValues = [likeCount, commentCount, collectCount, shareCount].filter(Boolean);
    return {
      author: firstText(author, meta('author')),
      published_at: publishedAt,
      like_count: likeCount,
      comment_count: commentCount,
      collect_count: collectCount,
      share_count: shareCount,
      engagement_samples: samples,
      metrics_source: metricValues.length || author || publishedAt ? 'douyin_page_cdp' : '',
      metrics_status: metricValues.length >= 2 ? 'success' : (metricValues.length || author || publishedAt ? 'partial' : 'unavailable'),
    };
  };

  const sources = [];
  for (const video of document.querySelectorAll('video')) {
    if (video.currentSrc) sources.push(video.currentSrc);
    if (video.src) sources.push(video.src);
    for (const source of video.querySelectorAll('source')) {
      if (source.src) sources.push(source.src);
    }
  }
  const visibleText = document.body && document.body.innerText || '';
  const metadata = collectMetadata(visibleText);
  return {
    final_url: location.href,
    title: document.title || '',
    description: meta('description') || meta('og:description'),
    author: metadata.author || '',
    published_at: metadata.published_at || '',
    like_count: metadata.like_count || '',
    comment_count: metadata.comment_count || '',
    collect_count: metadata.collect_count || '',
    share_count: metadata.share_count || '',
    engagement_samples: metadata.engagement_samples || [],
    metrics_source: metadata.metrics_source || '',
    metrics_status: metadata.metrics_status || 'unavailable',
    visible_text: visibleText.slice(0, 8000),
    dom_video_sources: sources,
    performance_sources: performance.getEntriesByType('resource').map(item => item.name).slice(-300),
  };
}

(async () => {
  const videoSources = [];
  const audioSources = [];
  const seenVideo = new Set();
  const seenAudio = new Set();
  const responseUrls = [];
  const audioResponseUrls = [];
  let cdp;
  try {
    const targets = await fetchJson('/json/list', 15000);
    const pageTarget = targets.find(t => t.type === 'page' && t.webSocketDebuggerUrl);
    if (!pageTarget) throw new Error('No CDP page target found');
    cdp = await makeCdp(pageTarget.webSocketDebuggerUrl);
    cdp.onEvent(msg => {
      if (msg.method === 'Network.responseReceived') {
        const params = msg.params || {};
        const url = params.response && params.response.url || '';
        if (isVideoUrl(url)) {
          responseUrls.push(url);
          addSource(videoSources, seenVideo, url, isVideoUrl);
        }
        if (isAudioUrl(url)) {
          audioResponseUrls.push(url);
          addSource(audioSources, seenAudio, url, isAudioUrl);
        }
      }
      if (msg.method === 'Network.requestWillBeSent') {
        const params = msg.params || {};
        const url = params.request && params.request.url || '';
        if (isVideoUrl(url)) addSource(videoSources, seenVideo, url, isVideoUrl);
        if (isAudioUrl(url)) addSource(audioSources, seenAudio, url, isAudioUrl);
      }
    });

    await cdp.send('Network.enable');
    await cdp.send('Network.setCacheDisabled', { cacheDisabled: true }).catch(() => {});
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
      source: mediaPlaybackGuardScript(),
    }).catch(() => {});
    await cdp.send('Page.navigate', { url: targetUrl });

    const collectPageSources = async () => {
      const snapshot = await cdp.send('Runtime.evaluate', {
        expression: `(() => {
          const sources = [];
          for (const video of document.querySelectorAll('video')) {
            try {
              video.muted = true;
              video.autoplay = false;
              video.removeAttribute('autoplay');
              video.preload = 'metadata';
              video.pause();
            } catch (_) {}
            if (video.currentSrc) sources.push(video.currentSrc);
            if (video.src) sources.push(video.src);
            for (const source of video.querySelectorAll('source')) {
              if (source.src) sources.push(source.src);
            }
          }
          window.scrollBy(0, 400);
          for (const item of performance.getEntriesByType('resource').slice(-500)) {
            if (item && item.name) sources.push(item.name);
          }
          return sources;
        })()`,
        returnByValue: true,
        awaitPromise: true,
      }).catch(() => ({ result: { value: [] } }));
      const values = snapshot.result && snapshot.result.value || [];
      for (const source of values || []) {
        addSource(videoSources, seenVideo, source, isVideoUrl);
        addSource(audioSources, seenAudio, source, isAudioUrl);
      }
    };

    const startedAt = Date.now();
    await sleep(6000);
    await cdp.send('Runtime.evaluate', {
      expression: mediaPlaybackGuardScript(),
      returnByValue: true,
    }).catch(() => {});
    while (Date.now() - startedAt < Math.max(10000, timeoutMs - 5000)) {
      await collectPageSources();
      if (videoSources.length > 0) break;
      await sleep(4000);
    }

    const pageData = await cdp.send('Runtime.evaluate', {
      expression: `(${pageSnapshot.toString()})()`,
      returnByValue: true,
    }).catch(() => ({ result: { value: {} } }));
    const value = pageData.result && pageData.result.value || {};
    for (const source of value.dom_video_sources || []) {
      addSource(videoSources, seenVideo, source, isVideoUrl);
      addSource(audioSources, seenAudio, source, isAudioUrl);
    }
    for (const source of value.performance_sources || []) {
      addSource(videoSources, seenVideo, source, isVideoUrl);
      addSource(audioSources, seenAudio, source, isAudioUrl);
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
      ok: true,
      final_url: value.final_url || '',
      title: value.title || '',
      author: value.author || '',
      description: value.description || '',
      published_at: value.published_at || '',
      like_count: value.like_count || '',
      comment_count: value.comment_count || '',
      collect_count: value.collect_count || '',
      share_count: value.share_count || '',
      engagement_samples: value.engagement_samples || [],
      metrics_source: value.metrics_source || '',
      metrics_status: value.metrics_status || 'unavailable',
      visible_text: value.visible_text || '',
      video_sources: videoSources.slice(0, 30),
      audio_sources: audioSources.slice(0, 30),
      response_count: responseUrls.length,
      audio_response_count: audioResponseUrls.length,
      response_url_samples: responseUrls.slice(0, 10),
      audio_response_url_samples: audioResponseUrls.slice(0, 10),
      profile_dir_used: profileDirArg ? profileDirArg : '',
      temp_profile_used: !profileDirArg,
      browser_stderr_tail: browserStderr.slice(-1200),
    }));
  } catch (err) {
    if (cdp) cdp.close();
    browser.kill();
    console.log(JSON.stringify({
      ok: false,
      error: String(err && err.message || err),
      browser_stderr_tail: browserStderr.slice(-1200),
      profile_dir_used: profileDirArg ? profileDirArg : '',
      temp_profile_used: !profileDirArg,
    }));
    process.exit(1);
  }
})();
"""


def run_cdp_fetch(browser: Path, url: str, timeout_sec: int, profile_dir: str, headed: bool) -> tuple[int, dict[str, Any], str]:
    completed = subprocess.run(
        [
            "node",
            "-e",
            node_cdp_script(),
            str(browser),
            url,
            profile_dir,
            "1" if headed else "0",
            str(timeout_sec * 1000),
        ],
        cwd=str(PROJECT_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_sec + 20,
    )
    try:
        data = json.loads((completed.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        data = {"ok": False, "error": f"Invalid CDP JSON: {(completed.stdout or '')[-800:]}"}
    return completed.returncode, data, completed.stderr or ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Douyin page metadata and video source candidates with local browser CDP; no MCP.")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout-sec", type=int, default=45)
    parser.add_argument("--browser-path")
    parser.add_argument("--profile-dir", default=os.environ.get("DOUYIN_BROWSER_PROFILE", ""))
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    job_dir = Path(args.job_dir).resolve()
    payload = base_payload(args.url)
    browser = find_browser(args.browser_path)
    if not browser:
        payload.update({"status": "browser_unavailable", "error": "No supported Edge/Chrome executable found."})
        write_json(job_dir, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    exit_code, data, stderr = run_cdp_fetch(browser, args.url, args.timeout_sec, args.profile_dir, args.headed)
    if exit_code != 0 or not data.get("ok"):
        payload.update({
            "ok": False,
            "status": "cdp_failed",
            "error": data.get("error") or stderr[-1200:] or f"CDP fetch exited with code {exit_code}",
            "browser": str(browser),
            "browser_stderr_tail": data.get("browser_stderr_tail") or stderr[-1200:],
        })
        write_json(job_dir, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    data = refine_page_metadata(data)
    payload.update({
        "ok": True,
        "status": "page_fetched",
        "final_url": data.get("final_url", ""),
        "title": data.get("title", ""),
        "author": data.get("author", ""),
        "description": data.get("description", ""),
        "published_at": data.get("published_at", ""),
        "like_count": data.get("like_count", ""),
        "comment_count": data.get("comment_count", ""),
        "collect_count": data.get("collect_count", ""),
        "share_count": data.get("share_count", ""),
        "engagement_samples": data.get("engagement_samples", []),
        "metrics_source": data.get("metrics_source", ""),
        "metrics_status": data.get("metrics_status", "unavailable"),
        "visible_text": data.get("visible_text", ""),
        "video_sources": data.get("video_sources", []),
        "audio_sources": data.get("audio_sources", []),
        "response_count": data.get("response_count", 0),
        "audio_response_count": data.get("audio_response_count", 0),
        "response_url_samples": data.get("response_url_samples", []),
        "audio_response_url_samples": data.get("audio_response_url_samples", []),
        "profile_dir_used": data.get("profile_dir_used") or "",
        "temp_profile_used": bool(data.get("temp_profile_used")),
        "browser": str(browser),
        "browser_stderr_tail": data.get("browser_stderr_tail") or "",
    })
    write_json(job_dir, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
