#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
import urllib.error
import urllib.request
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

from intake_platforms import PLATFORM_RULES, find_browser, profile_dir, session_status_path  # noqa: E402
from tools.ocr_media import build_material, dedupe_items, ocr_with_rapidocr, write_outputs  # noqa: E402

SENSITIVE_QUERY_MARKERS = ("token", "secret", "key", "auth", "session", "cookie", "sid")


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


def active_debug_port(platform_id: str) -> int:
    status = read_json(session_status_path(platform_id))
    try:
        port = int(status.get("debug_port") or 0)
    except (TypeError, ValueError):
        return 0
    return port if cdp_port_ready(port) else 0


def node_cdp_script() -> str:
    return r"""
const fs = require('fs');
const { spawn } = require('child_process');

const browserPath = process.argv[1];
const targetUrl = process.argv[2];
const profileDir = process.argv[3];
const imageDir = process.argv[4];
const maxImages = Number(process.argv[5] || 8);
const timeoutMs = Number(process.argv[6] || 90000);
const headed = process.argv[7] === '1';
const existingPort = Number(process.argv[8] || 0);
const port = existingPort || (12400 + Math.floor(Math.random() * 1000));

fs.mkdirSync(profileDir, { recursive: true });
fs.mkdirSync(imageDir, { recursive: true });

let browser = { kill() {} };
let browserStderr = '';
if (!existingPort) {
  const args = [
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
  if (!headed) args.push('--headless=new', '--disable-gpu');
  browser = spawn(browserPath, args, { stdio: ['ignore', 'ignore', 'pipe'] });
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

function candidateScript() {
  function cleanSrc(src) {
    try {
      const u = new URL(src);
      u.hash = '';
      return u.toString();
    } catch (_) {
      return String(src || '');
    }
  }
  const seen = new Set();
  const candidates = [];

  function addCandidate(kind, domIndex, element, src, naturalWidth = 0, naturalHeight = 0) {
    const rect = element.getBoundingClientRect();
    const width = rect.width || 0;
    const height = rect.height || 0;
    const area = width * height;
    if (width < 160 || height < 160 || area < 50000) return;
    const cleanedSrc = cleanSrc(src || '');
    if (kind === 'image' && (!cleanedSrc || cleanedSrc.startsWith('data:') || /\.svg(\?|$)/i.test(cleanedSrc))) return;
    const key = `${kind}:${cleanedSrc.split(/[?#]/)[0] || domIndex}:${Math.round(width)}x${Math.round(height)}`;
    if (seen.has(key)) return;
    seen.add(key);
    candidates.push({
      kind,
      domIndex,
      src: cleanedSrc,
      width,
      height,
      naturalWidth,
      naturalHeight,
      area,
    });
  }

  for (const [domIndex, img] of Array.from(document.images).entries()) {
    addCandidate('image', domIndex, img, img.currentSrc || img.src || '', img.naturalWidth || 0, img.naturalHeight || 0);
  }
  for (const [domIndex, video] of Array.from(document.querySelectorAll('video')).entries()) {
    addCandidate('video', domIndex, video, video.currentSrc || video.src || video.poster || '', video.videoWidth || 0, video.videoHeight || 0);
  }
  for (const [domIndex, canvas] of Array.from(document.querySelectorAll('canvas')).entries()) {
    addCandidate('canvas', domIndex, canvas, '', canvas.width || 0, canvas.height || 0);
  }
  for (const [domIndex, element] of Array.from(document.querySelectorAll('[style*="background-image"]')).entries()) {
    const bg = getComputedStyle(element).backgroundImage || '';
    const match = bg.match(/url\(["']?([^"')]+)["']?\)/i);
    if (!match) continue;
    addCandidate('background', domIndex, element, match[1], 0, 0);
  }
  candidates.sort((a, b) => b.area - a.area);
  return candidates.slice(0, 80);
}

function imageExtension(contentType, url) {
  const type = String(contentType || '').toLowerCase();
  if (type.includes('png')) return '.png';
  if (type.includes('webp')) return '.webp';
  if (type.includes('gif')) return '.gif';
  if (type.includes('jpeg') || type.includes('jpg')) return '.jpg';
  const clean = String(url || '').split(/[?#]/)[0].toLowerCase();
  if (clean.endsWith('.png')) return '.png';
  if (clean.endsWith('.webp')) return '.webp';
  if (clean.endsWith('.gif')) return '.gif';
  if (clean.endsWith('.jpeg') || clean.endsWith('.jpg')) return '.jpg';
  return '.jpg';
}

async function downloadCandidateImage(candidate, outputIndex) {
  if (!['image', 'background'].includes(String(candidate && candidate.kind || 'image'))) return null;
  const src = String(candidate && candidate.src || '');
  if (!/^https?:\/\//i.test(src)) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(src, {
      signal: controller.signal,
      headers: {
        'referer': targetUrl,
        'accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
      },
    });
    if (!response.ok) return null;
    const contentType = response.headers.get('content-type') || '';
    if (contentType && !contentType.toLowerCase().startsWith('image/')) return null;
    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.length < 1024) return null;
    const filename = `web_image_${String(outputIndex + 1).padStart(3, '0')}${imageExtension(contentType, src)}`;
    const path = `${imageDir.replace(/\\/g, '/')}/${filename}`;
    fs.writeFileSync(path, buffer);
    return {
      frame_index: outputIndex,
      path,
      source_url: src.split(/[?#]/)[0],
      natural_width: candidate.naturalWidth || 0,
      natural_height: candidate.naturalHeight || 0,
      clip_width: candidate.width || 0,
      clip_height: candidate.height || 0,
      capture_method: candidate.kind === 'background' ? 'background_image_download' : 'source_image_download',
    };
  } catch (_) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function scrollCandidateScript(candidate) {
  return new Promise(resolve => {
    const kind = String(candidate && candidate.kind || 'image');
    const domIndex = Number(candidate && candidate.domIndex || 0);
    let element = null;
    if (kind === 'video') element = Array.from(document.querySelectorAll('video'))[domIndex];
    else if (kind === 'canvas') element = Array.from(document.querySelectorAll('canvas'))[domIndex];
    else if (kind === 'background') element = Array.from(document.querySelectorAll('[style*="background-image"]'))[domIndex];
    else element = Array.from(document.images)[domIndex];
    if (!element) {
      resolve(null);
      return;
    }
    element.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
    setTimeout(() => {
      const rect = element.getBoundingClientRect();
      const viewportWidth = window.innerWidth || 1200;
      const viewportHeight = window.innerHeight || 900;
      const x = Math.max(0, Math.min(rect.left, viewportWidth - 1));
      const y = Math.max(0, Math.min(rect.top, viewportHeight - 1));
      const width = Math.max(1, Math.min(rect.width, viewportWidth - x));
      const height = Math.max(1, Math.min(rect.height, viewportHeight - y));
      resolve({
        x,
        y,
        width,
        height,
        src: candidate && candidate.src || element.currentSrc || element.src || element.poster || '',
        naturalWidth: element.naturalWidth || element.videoWidth || element.width || 0,
        naturalHeight: element.naturalHeight || element.videoHeight || element.height || 0,
        kind,
      });
    }, 500);
  });
}

(async () => {
  let cdp;
  let pageTarget;
  try {
    pageTarget = await selectPageTarget();
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
    while (Date.now() - started < Math.min(timeoutMs, 18000)) {
      const result = await cdp.send('Runtime.evaluate', {
        expression: `(${candidateScript.toString()})()`,
        returnByValue: true,
        awaitPromise: true,
      });
      const candidates = result.result && result.result.value || [];
      if (candidates.length >= Math.min(8, maxImages)) break;
      await cdp.send('Runtime.evaluate', {
        expression: 'window.scrollBy(0, Math.max(700, window.innerHeight || 900));',
        returnByValue: true,
      }).catch(() => {});
      await sleep(1200);
    }
    const candidateResult = await cdp.send('Runtime.evaluate', {
      expression: `(${candidateScript.toString()})()`,
      returnByValue: true,
      awaitPromise: true,
    });
    const candidates = (candidateResult.result && candidateResult.result.value || []).slice(0, maxImages);
    const images = [];
    for (let index = 0; index < candidates.length; index += 1) {
      const candidate = candidates[index];
      const downloaded = await downloadCandidateImage(candidate, images.length);
      if (downloaded) {
        images.push(downloaded);
        continue;
      }
      const rectResult = await cdp.send('Runtime.evaluate', {
        expression: `(${scrollCandidateScript.toString()})(${JSON.stringify(candidate)})`,
        returnByValue: true,
        awaitPromise: true,
      });
      const rect = rectResult.result && rectResult.result.value;
      if (!rect || rect.width < 30 || rect.height < 30) continue;
      const shot = await cdp.send('Page.captureScreenshot', {
        format: 'png',
        captureBeyondViewport: false,
        clip: { x: rect.x, y: rect.y, width: rect.width, height: rect.height, scale: 1 },
      });
      const filename = `web_image_${String(images.length + 1).padStart(3, '0')}.png`;
      const path = `${imageDir.replace(/\\/g, '/')}/${filename}`;
      fs.writeFileSync(path, Buffer.from(shot.data || '', 'base64'));
      images.push({
        frame_index: images.length,
        path,
        source_url: String(rect.src || candidate.src || '').split(/[?#]/)[0],
        natural_width: rect.naturalWidth || candidate.naturalWidth || 0,
        natural_height: rect.naturalHeight || candidate.naturalHeight || 0,
        clip_width: rect.width,
        clip_height: rect.height,
        capture_method: rect.kind === 'video' ? 'video_element_screenshot' : rect.kind === 'canvas' ? 'canvas_element_screenshot' : 'element_screenshot',
      });
    }
    const pageCleanup = await cleanupPage(cdp, pageTarget);
    cdp = null;
    browser.kill();
    console.log(JSON.stringify({
      ok: true,
      status: images.length ? 'images_captured' : 'no_candidate_images',
      images,
      candidate_count: candidates.length,
      image_dir: imageDir,
      reused_authorization_browser: Boolean(existingPort),
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
      image_dir: imageDir,
      reused_authorization_browser: Boolean(existingPort),
      opened_temporary_target: Boolean(existingPort && pageTarget),
      page_cleanup: pageCleanup,
      browser_stderr_tail: browserStderr.slice(-1200),
    }));
    process.exit(1);
  }
})();
"""


def run_capture(
    *,
    browser: Path,
    url: str,
    profile: Path,
    image_dir: Path,
    max_images: int,
    timeout_sec: int,
    headed: bool,
    debug_port: int,
) -> tuple[int, dict[str, Any], str]:
    completed = subprocess.run(
        [
            "node",
            "-e",
            node_cdp_script(),
            str(browser),
            url,
            str(profile),
            str(image_dir),
            str(max_images),
            str(timeout_sec * 1000),
            "1" if headed else "0",
            str(debug_port),
        ],
        cwd=str(PROJECT_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_sec + 40,
    )
    try:
        data = json.loads((completed.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        data = {
            "ok": False,
            "status": "invalid_capture_json",
            "error": f"Invalid capture JSON: {(completed.stdout or '')[-1000:]}",
        }
    return completed.returncode, data, completed.stderr or ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture visible webpage images and OCR them without MCP.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--platform", default="webpage", choices=sorted(PLATFORM_RULES.keys()))
    parser.add_argument("--max-images", type=int, default=8)
    parser.add_argument("--timeout-sec", type=int, default=90)
    parser.add_argument("--browser-path")
    parser.add_argument("--profile-dir", default="")
    parser.add_argument("--debug-port", type=int, default=0)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    job_dir = Path(args.job_dir).resolve()
    browser = find_browser(args.browser_path)
    temp_root = Path(os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir())
    run_temp = temp_root / "LucasWebImageOCR" / f"{job_dir.name}-{uuid.uuid4().hex[:6]}"
    image_dir = run_temp / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "ok": False,
        "used_mcp": False,
        "stage": "web_image_ocr",
        "status": "not_started",
        "job_id": job_dir.name,
        "job_dir": str(job_dir),
        "url": redact_url(args.url),
        "platform": args.platform,
        "max_images": max(1, args.max_images),
        "temp_dir": str(run_temp),
        "image_dir": str(image_dir),
        "input_path": str(image_dir),
        "text_items": [],
        "merged_text": "",
        "confidence": "low",
        "error": None,
    }

    if not browser:
        result.update({"status": "browser_unavailable", "error": "No supported Edge/Chrome executable found."})
        sampling = {
            "strategy": "web_visible_image_sources_then_screenshots",
            "reason": "browser unavailable",
            "max_frames": max(1, args.max_images),
            "timeout_sec": args.timeout_sec,
            "frames_dir": str(image_dir),
            "frame_count": 0,
            "frames": [],
        }
        write_outputs(job_dir, result, build_material(
            job_dir=job_dir,
            result=result,
            input_type="web_images",
            max_frames=args.max_images,
            timeout_sec=args.timeout_sec,
            sampling=sampling,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    profile = Path(args.profile_dir).expanduser() if args.profile_dir else profile_dir(args.platform)
    debug_port = args.debug_port if args.debug_port and cdp_port_ready(args.debug_port) else active_debug_port(args.platform)
    exit_code, capture, stderr = run_capture(
        browser=browser,
        url=args.url,
        profile=profile,
        image_dir=image_dir,
        max_images=max(1, args.max_images),
        timeout_sec=max(20, args.timeout_sec),
        headed=bool(args.headed),
        debug_port=debug_port,
    )
    image_paths = [Path(item.get("path")) for item in (capture.get("images") or []) if item.get("path")]
    image_paths = [path for path in image_paths if path.exists()]
    sampling = {
        "strategy": "web_visible_image_sources_then_screenshots",
        "reason": "capture top visible non-icon images from the rendered webpage, prefer source image bytes, then OCR each image",
        "max_frames": max(1, args.max_images),
        "timeout_sec": args.timeout_sec,
        "frames_dir": str(image_dir),
        "frame_count": len(image_paths),
        "frames": capture.get("images") or [],
        "candidate_count": capture.get("candidate_count") or 0,
        "reused_authorization_browser": bool(capture.get("reused_authorization_browser")),
    }
    result.update({
        "capture_status": capture.get("status"),
        "capture_exit_code": exit_code,
        "browser": str(browser),
        "profile_dir": str(profile),
        "debug_port": debug_port,
        "reused_authorization_browser": bool(capture.get("reused_authorization_browser")),
        "opened_temporary_target": bool(capture.get("opened_temporary_target")),
        "page_cleanup": capture.get("page_cleanup") if isinstance(capture.get("page_cleanup"), dict) else {},
        "frame_count": len(image_paths),
        "browser_stderr_tail": capture.get("browser_stderr_tail") or stderr[-1200:],
    })
    if exit_code != 0 or not capture.get("ok"):
        result.update({
            "ok": False,
            "status": capture.get("status") or "image_capture_failed",
            "error": capture.get("error") or stderr[-1200:] or f"capture exited with code {exit_code}",
        })
        write_outputs(job_dir, result, build_material(
            job_dir=job_dir,
            result=result,
            input_type="web_images",
            max_frames=args.max_images,
            timeout_sec=args.timeout_sec,
            sampling=sampling,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    if not image_paths:
        result.update({"ok": True, "status": "web_image_ocr_no_images"})
        write_outputs(job_dir, result, build_material(
            job_dir=job_dir,
            result=result,
            input_type="web_images",
            max_frames=args.max_images,
            timeout_sec=args.timeout_sec,
            sampling=sampling,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    ok, raw_items, detail = ocr_with_rapidocr(image_paths, args.timeout_sec)
    if not ok:
        result.update({"ok": False, "status": "ocr_failed", "error": detail[-1000:]})
        write_outputs(job_dir, result, build_material(
            job_dir=job_dir,
            result=result,
            input_type="web_images",
            max_frames=args.max_images,
            timeout_sec=args.timeout_sec,
            sampling=sampling,
            raw_text_item_count=len(raw_items),
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    text_items = dedupe_items(raw_items)
    merged = "\n".join(item["text"] for item in text_items)
    result.update({
        "ok": True,
        "status": "ocr_done",
        "text_items": text_items,
        "merged_text": merged,
        "confidence": "medium" if merged else "low",
        "rapidocr_stderr_tail": detail[-1000:] if detail else "",
    })
    write_outputs(job_dir, result, build_material(
        job_dir=job_dir,
        result=result,
        input_type="web_images",
        max_frames=args.max_images,
        timeout_sec=args.timeout_sec,
        sampling=sampling,
        raw_text_item_count=len(raw_items),
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
